import os

# Добавляем Node.js в PATH чтобы yt-dlp мог решать n-challenge
_nodejs_dir = r'C:\Program Files\nodejs'
if os.path.isdir(_nodejs_dir) and _nodejs_dir not in os.environ.get('PATH', ''):
    os.environ['PATH'] = _nodejs_dir + os.pathsep + os.environ.get('PATH', '')

from config import format_duration, format_invidious_duration, initialize_settings
from convert import convert_to_mp3, convert_to_mp4
from download_history import add_to_history
from logger import log_message
import yt_dlp
import threading
import time
import traceback
from bs4 import BeautifulSoup
import re
from queues import add_to_queue, add_to_queue_front, clear_queue_file, get_queue_count, get_queue_urls, process_queue, remove_from_queue
import utils as _dl_utils
from tray import show_notification, tray_icon, update_download_status
from config import initialize_settings, settings, is_downloading
from utils import global_file_size, global_downloaded, download_speed, last_update_time, last_downloaded_bytes, format_speed, update_speed, format_date
from clipboard_utils import update_last_copy_time

invidious_url_var = ""

_AGE_ERRORS = ("sign in to confirm your age", "age-restricted", "inappropriate for some users")

def _is_age_error(e) -> bool:
    return any(s in str(e).lower() for s in _AGE_ERRORS)

def _auth_opts() -> dict:
    """Опции для обхода возрастных ограничений: Firefox куки + Node.js для n-challenge."""
    return {'cookiesfrombrowser': ('firefox',), 'js_runtimes': {'node': {}}}


# «Sign in to confirm you're not a bot» — бот-проверка YouTube (обычно на
# флагнутых/VPN-IP). Лечится живыми куками аккаунта и/или сменой player_client.
_BOT_ERRORS = ("not a bot", "confirm you're not a bot", "confirm you are not a bot",
               "sign in to confirm")

def _is_bot_error(e) -> bool:
    return any(s in str(e).lower() for s in _BOT_ERRORS)


_YT_HOSTS = ("youtube.com", "youtu.be", "youtube-nocookie.com")

def _is_youtube(u: str) -> bool:
    return any(h in (u or "").lower() for h in _YT_HOSTS)


def _yt_cookie_opts() -> dict:
    """Живые куки залогиненного Firefox — главный способ пройти бот-проверку."""
    return {'cookiesfrombrowser': ('firefox',)}


def _yt_retry_opts() -> dict:
    """Опции повтора при бот-проверке: куки Firefox + перебор клиентов, где
    tv/web_safari обычно обходят проверку, которую валит обычный web-клиент."""
    return {
        'cookiesfrombrowser': ('firefox',),
        'extractor_args': {'youtube': {'player_client': ['tv', 'web_safari', 'default']}},
    }


# VK рубит небраузерные TLS-соединения (SSL: UNEXPECTED_EOF_WHILE_READING при
# загрузке JSON метаданных), поэтому к VK ходим с TLS-имперсонацией Chrome.
# Требует curl_cffi; если его нет — импорт не падает, просто без имперсонации.
try:
    from yt_dlp.networking.impersonate import ImpersonateTarget
    _IMPERSONATE_CHROME = ImpersonateTarget.from_str('chrome')
except Exception:
    _IMPERSONATE_CHROME = None

_VK_HOSTS = ("vk.com", "vk.ru", "vkvideo.ru", "vkvideo.com", "m.vk.com", "userapi.com")

def _is_vk(u: str) -> bool:
    return any(h in (u or "").lower() for h in _VK_HOSTS)

def _vk_opts() -> dict:
    return {'impersonate': _IMPERSONATE_CHROME} if _IMPERSONATE_CHROME else {}


def _patch_odnoklassniki_parse_json():
    """OK (ok.ru) иногда отдаёт flashvars.metadata уже как dict, а экстрактор
    yt-dlp безусловно вызывает _parse_json(metadata) и падает с
    'the JSON object must be str, bytes or bytearray, not dict'.
    Делаем _parse_json этого экстрактора терпимым к готовому dict/list.
    Патч идемпотентный и затрагивает только Одноклассники."""
    try:
        from yt_dlp.extractor.odnoklassniki import OdnoklassnikiIE
    except Exception as e:
        log_message(f"WARNING OK-патч не применён: {e}")
        return
    if getattr(OdnoklassnikiIE, "_ytd_ok_json_patched", False):
        return
    _orig_parse_json = OdnoklassnikiIE._parse_json

    def _parse_json_safe(self, json_string, *args, **kwargs):
        if isinstance(json_string, (dict, list)):
            return json_string
        return _orig_parse_json(self, json_string, *args, **kwargs)

    OdnoklassnikiIE._parse_json = _parse_json_safe
    OdnoklassnikiIE._ytd_ok_json_patched = True


_patch_odnoklassniki_parse_json()


class _YtdlpLogger:
    def debug(self, msg):
        if msg.startswith('[debug]'):
            return
        # log_message(f"DEBUG yt-dlp: {msg}")
    def info(self, msg):
        log_message(f"INFO yt-dlp: {msg}")
    def warning(self, msg):
        log_message(f"WARNING yt-dlp: {msg}")
    def error(self, msg):
        log_message(f"ERROR yt-dlp: {msg}")


class _UserStop(Exception):
    """Исключение для остановки загрузки пользователем (пауза)"""
    pass


def _unique_basename(folder: str, base: str) -> str:
    """Возвращает имя без расширения, для которого в папке нет ни одного файла
    '<имя>.*'. Если base занят — добавляет ' (2)', ' (3)' и т.д., чтобы видео
    с одинаковыми названиями не затирали друг друга."""
    try:
        existing = {os.path.splitext(f)[0] for f in os.listdir(folder)}
    except OSError:
        return base
    if base not in existing:
        return base
    i = 2
    while f"{base} ({i})" in existing:
        i += 1
    return f"{base} ({i})"


def _append_description(folder: str, file_name: str, description: str):
    """Дописывает в desc.txt в папке загрузки имя файла и его описание."""
    try:
        path = os.path.join(folder, "desc.txt")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"### {file_name}\n")
            f.write((description or "").strip() + "\n\n")
        log_message(f"INFO Описание добавлено в {path}")
    except Exception as e:
        log_message(f"WARNING desc.txt: {e}")


def download_video(url, from_queue=False):
    global is_downloading, global_file_size, global_downloaded, download_speed, last_update_time, last_downloaded_bytes

    if is_downloading:
        log_message(f"INFO Загрузка уже идет, добавляем URL в очередь: {url}")
        add_to_queue(url)
        return

    is_downloading = True
    _dl_utils.current_download_url = url
    _dl_utils.stop_requested = False
    _dl_utils.is_paused = False
    log_message(f"DEBUG Путь к cookies.txt: {os.path.abspath('cookies.txt')}")  # Логирование пути к cookies.txt
    log_message(f"DEBUG: Текущая директория: {os.getcwd()}")
    log_message(f"DEBUG: Наличие файла cookies.txt в текущей директории: {os.path.exists('cookies.txt')}")
    log_message(f"DEBUG: Абсолютный путь к cookies.txt: {os.path.abspath('cookies.txt')}")

    def on_download_complete():
        global is_downloading
        is_downloading = False
        queue_count = get_queue_count()
        if queue_count > 0:
            log_message(f"INFO В очереди остались URL ({queue_count}), запускаем обработку")
            threading.Thread(target=process_queue, daemon=True).start()
        else:
            log_message("INFO Очередь пуста после завершения загрузки")
            clear_queue_file()
            update_download_status("Ожидание...", 100)

    if not from_queue:
        if url in get_queue_urls():
            log_message(f"INFO URL уже в очереди: {url}")
            on_download_complete()
            return

    globals()['global_file_size'] = 0
    globals()['global_downloaded'] = 0
    globals()['download_speed'] = "0 KB/s"
    globals()['last_update_time'] = time.time()
    globals()['last_downloaded_bytes'] = 0

    if not url:
        log_message("ERROR Пустой URL для загрузки")
        on_download_complete()
        return

    save_path = settings["download_folder"]
    cookies_path = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cookies.txt'))
    _has_cookies = os.path.isfile(cookies_path) and os.path.getsize(cookies_path) > 100
    _is_yt = _is_youtube(url)
    _is_vk_url = _is_vk(url)

    if "&list=" in url:
        log_message(f"INFO URL содержит параметр плейлиста: {url}. Загружаем только видео.")

    def _extract_info(extra_opts: dict):
        opts = {
            "quiet": True, "noplaylist": True,
            "js_runtimes": {"node": {}}, "remote_components": {"ejs": "github"},
        }
        # Для YouTube сразу подкладываем живые куки Firefox — так бот-проверка
        # обычно не срабатывает. Для остальных сайтов — локальный cookies.txt.
        if _is_yt:
            opts.update(_yt_cookie_opts())
        elif _has_cookies:
            opts['cookies'] = cookies_path
        if _is_vk_url:
            opts.update(_vk_opts())
        opts.update(extra_opts)
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)

    try:
        try:
            info = _extract_info({})
        except yt_dlp.utils.DownloadError as e:
            if _is_yt and _is_bot_error(e):
                log_message("INFO YouTube: бот-проверка — повтор с куками Firefox и сменой клиента (tv/web_safari)")
                update_download_status("Обход проверки...", 0)
                info = _extract_info(_yt_retry_opts())
            elif _is_age_error(e):
                log_message("INFO Видео требует авторизации — повтор с куками Firefox")
                update_download_status("Авторизация...", 0)
                info = _extract_info(_auth_opts())
            else:
                raise

        if not info:
            log_message(f"ERROR Не удалось получить информацию о видео: {url}")
            raise Exception("Не удалось извлечь информацию о видео")

        if info.get('is_premiere', False) or info.get('live_status', '') == 'is_upcoming':
            log_message(f"INFO Пропуск премьеры: {url}")
            threading.Thread(target=show_notification, args=(tray_icon, "Премьера", "Это видео еще не вышло (премьера). Загрузка невозможна."), daemon=True).start()
            on_download_complete()
            if from_queue:
                remove_from_queue(url)
            return

        video_title = info.get("title", "video")
        _dl_utils.queue_titles[url] = video_title
        safe_title = re.sub(r'[\\/*?:"<>|]', "_", video_title)

        if settings["conversion_enabled"]:
            video_ext = settings["download_format"]
        else:
            video_ext = info.get("ext", "mp4")

        # Уникальное имя, чтобы видео с одинаковыми названиями не затирали друг друга
        unique_base = _unique_basename(save_path, safe_title)
        file_name = f"{unique_base}.{video_ext}"
        file_path = os.path.join(save_path, file_name)

        log_message(f"INFO Планируется загрузка файла: {file_path}")

    except yt_dlp.utils.DownloadError as e:
        log_message(f"ERROR Видео недоступно: {url}. Ошибка: {e}")
        threading.Thread(target=show_notification, args=(tray_icon, "Ошибка", f"Видео недоступно: {str(e)}"), daemon=True).start()
        if from_queue:
            remove_from_queue(url)
        on_download_complete()
        return

    except Exception as e:
        log_message(f"ERROR Ошибка при проверке видео: {url}. Подробности: {e}")
        log_message(f"DEBUG Трассировка: {traceback.format_exc()}")
        threading.Thread(target=show_notification, args=(tray_icon, "Ошибка", f"Не удалось загрузить видео: {str(e)}"), daemon=True).start()
        if from_queue:
            remove_from_queue(url)
        on_download_complete()
        return

    update_download_status("Загрузка...", 0)

    quality_map = {
        "1080p": "bestvideo[height<=1080]+bestaudio/best",
        "720p": "bestvideo[height<=720]+bestaudio/best",
        "480p": "bestvideo[height<=480]+bestaudio/best"
    }
    selected_quality = quality_map.get(settings["video_quality"], "best")

    # В outtmpl литеральный '%' надо экранировать как '%%'
    _outtmpl_base = unique_base.replace('%', '%%')
    ydl_opts = {
        'outtmpl': os.path.join(save_path, f'{_outtmpl_base}.%(ext)s'),
        'restrict_filenames': False,
        'windowsfilenames': False,
        'noplaylist': True,
        'logger': _YtdlpLogger(),
        'js_runtimes': {'node': {}},
        'remote_components': {'ejs': 'github'},
    }
    if _is_yt:
        ydl_opts['cookiesfrombrowser'] = ('firefox',)
    elif _has_cookies:
        ydl_opts['cookies'] = cookies_path
    if _is_vk_url:
        ydl_opts.update(_vk_opts())

    if settings["download_format"] == "mp3":
        # Не ограничиваем клиентов — yt-dlp сам выберет тот, что даёт audio-only
        ydl_opts['format'] = 'bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio'
    else:
        ydl_opts['format'] = quality_map.get(settings["video_quality"], "best")

    _paused_by_user = False
    try:
        threading.Thread(target=show_notification, args=(tray_icon, "YouTube Downloader", "Видео загружается..."), daemon=True).start()

        log_message(f"DEBUG ydl_opts: {ydl_opts}")
        ydl_opts["progress_hooks"] = [progress_hook]

        def _do_download(extra_opts: dict):
            opts = dict(ydl_opts)
            opts.update(extra_opts)
            with yt_dlp.YoutubeDL(opts) as ydl:
                dl_info = ydl.extract_info(url, download=True)
                return dl_info, ydl.prepare_filename(dl_info)

        try:
            info, downloaded_file = _do_download({})
        except yt_dlp.utils.DownloadError as e:
            if _is_yt and _is_bot_error(e):
                log_message("INFO YouTube: бот-проверка при загрузке — повтор с куками Firefox и сменой клиента (tv/web_safari)")
                update_download_status("Обход проверки...", 0)
                info, downloaded_file = _do_download(_yt_retry_opts())
            elif _is_age_error(e):
                log_message("INFO Загрузка требует авторизации — повтор с куками Firefox")
                update_download_status("Авторизация...", 0)
                info, downloaded_file = _do_download(_auth_opts())
            else:
                raise

        # Сохраняем в историю с длительностью
        video_duration = info.get('duration', 0)
        video_title = info.get('title', 'Неизвестное видео')
        log_message(f"DEBUG Сохранение в историю: {video_title}, длительность: {video_duration} сек")

        add_to_history(
            url=url,
            title=video_title,
            format_type=settings["download_format"],
            duration=video_duration
        )

        log_message(f"SUCCESS Файл загружен: {downloaded_file}")

        final_file = downloaded_file
        if settings["conversion_enabled"]:
            fmt = settings["download_format"]
            converted_file = None
            if fmt == "mp3" and downloaded_file.endswith((".m4a", ".webm", ".mp4", ".mkv", ".opus")):
                log_message("INFO Конвертация в MP3...")
                converted_file = convert_to_mp3(downloaded_file, update_download_status)
                if converted_file:
                    log_message(f"SUCCESS Конвертация завершена: {converted_file}")
            elif fmt == "mp4" and downloaded_file.endswith((".m4a", ".webm", ".mkv")):
                log_message("INFO Конвертация в MP4...")
                converted_file = convert_to_mp4(downloaded_file, update_download_status)
                if converted_file:
                    log_message(f"SUCCESS Конвертация завершена: {converted_file}")
            if converted_file:
                final_file = converted_file
        else:
            log_message("SUCCESS Файл сохранен в исходном формате")

        # Опция «Скачивать с описаниями»: пишем имя файла и описание в desc.txt
        if settings.get("download_with_description", False):
            _append_description(save_path, os.path.basename(final_file),
                                info.get("description", ""))

        if from_queue:
            remove_from_queue(url)

        threading.Thread(target=show_notification, args=(tray_icon, "YouTube Downloader", "Видео загружено успешно!"), daemon=True).start()

        log_message(f"SUCCESS Загрузка завершена: {url}")

    except _UserStop:
        _paused_by_user = True
        add_to_queue_front(url)
        _dl_utils.is_paused = True
        _dl_utils.stop_requested = False
        update_download_status("На паузе", 0)
        log_message(f"INFO Загрузка приостановлена: {url}")

    except Exception as e:
        error_message = f"ERROR Ошибка загрузки видео: {url}. Подробности: {e}"
        threading.Thread(target=show_notification, args=(tray_icon, "YouTube Downloader", f"Ошибка: {str(e)}"), daemon=True).start()
        log_message(error_message)
        log_message(f"DEBUG Трассировка: {traceback.format_exc()}")

        if from_queue:
            remove_from_queue(url)
        on_download_complete()
        return

    finally:
        _dl_utils.current_download_url = ""
        if not _paused_by_user:
            on_download_complete()
        else:
            is_downloading = False

def progress_hook(d):
    global global_file_size, global_downloaded, last_update_time, last_downloaded_bytes

    if _dl_utils.stop_requested:
        raise _UserStop()

    try:
        if d["status"] == "downloading":
            if "total_bytes" in d and d["total_bytes"] is not None:
                globals()['global_file_size'] = d["total_bytes"]
            elif "total_bytes_estimate" in d and d["total_bytes_estimate"] is not None:
                globals()['global_file_size'] = d["total_bytes_estimate"]

            if "downloaded_bytes" in d and d["downloaded_bytes"] is not None:
                globals()['global_downloaded'] = d["downloaded_bytes"]
                update_speed(global_downloaded)

            total = globals()['global_file_size']
            downloaded = globals()['global_downloaded']
            percent = (downloaded / total * 100) if total else 0
            update_download_status("Загрузка...", int(percent), downloaded, total)

        elif d["status"] == "finished":
            update_download_status("Готово!", 100, 0, 0)
            globals()['global_file_size'] = 0
            globals()['global_downloaded'] = 0
            globals()['download_speed'] = "0 KB/s"

    except Exception as e:
        log_message(f"Ошибка в progress_hook: {e}")

def download_channel_with_selection(channel_url):
    """Открывает PyQt6-окно выбора видео с канала."""
    log_message(f"INFO Обработка канала: {channel_url}")
    from tray import open_channel_window
    open_channel_window(channel_url)


def download_playlist_with_selection(playlist_url):
    """Открывает PyQt6-окно выбора видео из плейлиста."""
    log_message(f"INFO Обработка плейлиста: {playlist_url}")
    from tray import open_playlist_window
    open_playlist_window(playlist_url)
