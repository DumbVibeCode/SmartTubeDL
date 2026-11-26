import os
import shutil

# Добавляем Node.js в PATH (если ещё не найден через shutil.which), чтобы yt-dlp мог решать n-challenge
if not shutil.which('node'):
    _node_candidates = [
        r'C:\Program Files\nodejs',
        r'C:\Program Files (x86)\nodejs',
        os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Programs', 'nodejs'),
    ]
    for _nodejs_dir in _node_candidates:
        if _nodejs_dir and os.path.isdir(_nodejs_dir) and _nodejs_dir not in os.environ.get('PATH', ''):
            os.environ['PATH'] = _nodejs_dir + os.pathsep + os.environ.get('PATH', '')
            break

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
from config import initialize_settings, settings
from utils import (
    format_speed, update_speed, format_date,
    notify_ui, update_status_ui as update_download_status,
    open_channel_ui, open_playlist_ui,
)
from clipboard_utils import update_last_copy_time

# Зеркало состояния загрузки для обратной совместимости
is_downloading = False

invidious_url_var = ""

_AGE_ERRORS = ("sign in to confirm your age", "age-restricted", "inappropriate for some users")

def _is_age_error(e) -> bool:
    return any(s in str(e).lower() for s in _AGE_ERRORS)

def _cookies_browser() -> str:
    """Возвращает браузер для извлечения куки из настроек (по умолчанию 'firefox')."""
    return (settings.get("cookies_browser") or "").strip()


def _auth_opts() -> dict:
    """Опции для обхода возрастных ограничений: куки браузера + Node.js для n-challenge."""
    opts = {'js_runtimes': {'node': {}}}
    browser = _cookies_browser()
    if browser:
        opts['cookiesfrombrowser'] = (browser,)
    return opts


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
    """Живые куки залогиненного браузера — главный способ пройти бот-проверку."""
    browser = _cookies_browser()
    return {'cookiesfrombrowser': (browser,)} if browser else {}


def _yt_retry_opts() -> dict:
    """Опции повтора при бот-проверке: куки браузера + перебор клиентов, где
    tv/web_safari обычно обходят проверку, которую валит обычный web-клиент."""
    opts = {
        'extractor_args': {'youtube': {'player_client': ['tv', 'web_safari', 'default']}},
    }
    browser = _cookies_browser()
    if browser:
        opts['cookiesfrombrowser'] = (browser,)
    return opts


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


# VK отдаёт эти сообщения для видео, требующих входа. yt-dlp такие видео
# скачать не может (VK не принимает сессию вне браузера) — переключаемся на
# браузерный захват через залогиненный Selenium-браузер окна ВК.
_VK_LOGIN_ERRORS = ("signed-in users", "signed in users", "registered users",
                    "only available")

def _is_vk_login_error(e) -> bool:
    return any(s in str(e).lower() for s in _VK_LOGIN_ERRORS)

def _try_vk_browser_download(url: str, save_path: str) -> bool:
    """Фолбэк для закрытых VK-видео: качаем через залогиненный браузер окна ВК.
    Нужен открытый и залогиненный VK-браузер. Возвращает True при успехе."""
    try:
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        vw = getattr(app, "vk_window", None) if app else None
        if not vw or not getattr(vw, "driver", None):
            log_message("INFO VK: закрытое видео — откройте окно ВК и войдите, затем повторите")
            return False
        want_mp3 = settings.get("download_format") == "mp3"
        update_download_status("Качаю через браузер VK...", 0)
        out = vw.download_video_via_browser(url, save_path, update_download_status, want_mp3)
        if out:
            log_message(f"SUCCESS VK видео скачано через браузер: {out}")
            add_to_history(url=url, title=os.path.basename(out),
                           format_type=settings.get("download_format", "mp4"), duration=0)
            return True
        log_message("WARNING VK: браузерный захват видео не удался")
        return False
    except Exception as e:
        log_message(f"ERROR VK browser fallback: {e}")
        return False


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


def _save_description(folder: str, file_name: str, description: str):
    """Сохраняет описание в отдельный .txt рядом с видео, с тем же именем.
    Для «My Video.mp4» создаётся «My Video.txt»."""
    try:
        base = os.path.splitext(file_name)[0]
        path = os.path.join(folder, base + ".txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write((description or "").strip() + "\n")
        log_message(f"INFO Описание сохранено в {path}")
    except Exception as e:
        log_message(f"WARNING описание .txt: {e}")


def download_video(url, from_queue=False):
    global is_downloading

    if not _dl_utils.try_acquire_download():
        log_message(f"INFO Загрузка уже идет, добавляем URL в очередь: {url}")
        add_to_queue(url)
        return

    is_downloading = True
    _dl_utils.current_download_url = url
    _dl_utils.stop_requested = False
    _dl_utils.cancel_requested = False
    _dl_utils.is_paused = False
    _dl_utils.active_temp_files.clear()
    log_message(f"DEBUG Путь к cookies.txt: {os.path.abspath('cookies.txt')}")  # Логирование пути к cookies.txt
    log_message(f"DEBUG: Текущая директория: {os.getcwd()}")
    log_message(f"DEBUG: Наличие файла cookies.txt в текущей директории: {os.path.exists('cookies.txt')}")
    log_message(f"DEBUG: Абсолютный путь к cookies.txt: {os.path.abspath('cookies.txt')}")

    def on_download_complete():
        global is_downloading
        is_downloading = False
        _dl_utils.release_download()
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

    _dl_utils.reset_progress()

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
            "js_runtimes": {"node": {}}, "remote_components": ["ejs:github"],
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
            notify_ui("Премьера", "Это видео еще не вышло (премьера). Загрузка невозможна.")
            on_download_complete()
            if from_queue:
                remove_from_queue(url)
            return

        video_title = info.get("title", "video")
        _dl_utils.queue_titles[url] = video_title
        author = info.get("channel") or info.get("uploader") or ""
        src_name = "YouTube" if _is_yt else ("VK" if _is_vk_url else ("OK" if "ok.ru" in url.lower() else ""))
        save_path = _dl_utils.resolve_download_folder(save_path, source=src_name, author=author)
        safe_title = _dl_utils.format_download_filename(
            video_title, author=author, date_str=info.get("upload_date", ""), is_music=False
        )

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
        # Закрытое VK-видео (нужен вход) — пробуем скачать через браузер окна ВК
        if _is_vk_url and _is_vk_login_error(e):
            log_message(f"INFO VK: видео требует входа, пробую браузерный захват: {url}")
            if _try_vk_browser_download(url, save_path):
                notify_ui("YouTube Downloader", "Видео ВК скачано через браузер")
                if from_queue:
                    remove_from_queue(url)
                on_download_complete()
                return
        log_message(f"ERROR Видео недоступно: {url}. Ошибка: {e}")
        notify_ui("Ошибка", f"Видео недоступно: {str(e)}")
        if from_queue:
            remove_from_queue(url)
        on_download_complete()
        return

    except Exception as e:
        log_message(f"ERROR Ошибка при проверке видео: {url}. Подробности: {e}")
        log_message(f"DEBUG Трассировка: {traceback.format_exc()}")
        notify_ui("Ошибка", f"Не удалось загрузить видео: {str(e)}")
        if from_queue:
            remove_from_queue(url)
        on_download_complete()
        return

    update_download_status("Загрузка...", 0)

    audio_lang = str(settings.get("audio_track_lang", "auto") or "auto").strip().lower()
    if _is_yt and audio_lang == "ru":
        _audio_sel = "bestaudio[language^=ru]/bestaudio"
    elif _is_yt and audio_lang == "en":
        _audio_sel = "bestaudio[language^=en]/bestaudio"
    elif _is_yt and audio_lang == "original":
        _audio_sel = "bestaudio[format_note*=original]/bestaudio"
    else:
        _audio_sel = "bestaudio"

    quality_map = {
        "Best":  f"bestvideo+{_audio_sel}/best",
        "2160p": f"bestvideo[height<=2160]+{_audio_sel}/best",
        "1440p": f"bestvideo[height<=1440]+{_audio_sel}/best",
        "1080p": f"bestvideo[height<=1080]+{_audio_sel}/best",
        "720p":  f"bestvideo[height<=720]+{_audio_sel}/best",
        "480p":  f"bestvideo[height<=480]+{_audio_sel}/best",
    }

    # В outtmpl литеральный '%' надо экранировать как '%%'
    _outtmpl_base = unique_base.replace('%', '%%')
    _retries = int(settings.get("download_retries", 3) or 0)
    ydl_opts = {
        'outtmpl': os.path.join(save_path, f'{_outtmpl_base}.%(ext)s'),
        'restrict_filenames': False,
        'windowsfilenames': False,
        'noplaylist': True,
        'retries': _retries,
        'fragment_retries': _retries,
        'logger': _YtdlpLogger(),
        'js_runtimes': {'node': {}},
        'remote_components': ['ejs:github'],
    }
    if _is_yt:
        ydl_opts.update(_yt_cookie_opts())
    elif _has_cookies:
        ydl_opts['cookies'] = cookies_path
    if _is_vk_url:
        ydl_opts.update(_vk_opts())

    postprocessors = []
    is_mp3 = settings["download_format"] == "mp3"
    if is_mp3:
        # Не ограничиваем клиентов — yt-dlp сам выберет тот, что даёт audio-only
        if _is_yt and audio_lang in ("ru", "en"):
            ydl_opts['format'] = f'bestaudio[language^={audio_lang}]/bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio'
        elif _is_yt and audio_lang == "original":
            ydl_opts['format'] = 'bestaudio[format_note*=original]/bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio'
        else:
            ydl_opts['format'] = 'bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio'
        if settings.get("conversion_enabled", True):
            postprocessors.append({
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            })
    else:
        ydl_opts['format'] = quality_map.get(settings.get("video_quality", "1080p"), quality_map["Best"])
        if settings.get("conversion_enabled", True):
            ydl_opts['merge_output_format'] = 'mp4'
            postprocessors.append({
                'key': 'FFmpegVideoRemuxer',
                'preferedformat': 'mp4',
            })

    # Дополнительные опции YouTube (SponsorBlock, субтитры, главы, обложка)
    if _is_yt:
        if settings.get("sponsorblock_remove", False):
            postprocessors.append({
                'key': 'SponsorBlock',
                'categories': ['sponsor', 'selfpromo', 'interaction'],
                'when': 'after_filter',
            })
            postprocessors.append({
                'key': 'ModifyChapters',
                'remove_sponsor_segments': ['sponsor', 'selfpromo', 'interaction'],
            })

        subs_mode = str(settings.get("subtitles_mode", "none") or "none").lower()
        if subs_mode in ("srt", "embed") and not is_mp3:
            raw_langs = str(settings.get("subtitles_langs", "ru,en") or "ru,en")
            langs = [x.strip() for x in raw_langs.split(",") if x.strip()] or ["en"]
            ydl_opts['writesubtitles'] = True
            ydl_opts['writeautomaticsub'] = True
            ydl_opts['subtitleslangs'] = langs
            ydl_opts['subtitlesformat'] = 'srt/best'
            postprocessors.append({
                'key': 'FFmpegSubtitlesConvertor',
                'format': 'srt',
            })
            if subs_mode == "embed":
                postprocessors.append({'key': 'FFmpegEmbedSubtitle'})

        if settings.get("embed_chapters", True) and not is_mp3:
            postprocessors.append({
                'key': 'FFmpegMetadata',
                'add_chapters': True,
                'add_metadata': True,
            })

        if settings.get("embed_thumbnail", False):
            ydl_opts['writethumbnail'] = True
            postprocessors.append({
                'key': 'FFmpegThumbnailsConvertor',
                'format': 'jpg',
            })
            postprocessors.append({'key': 'EmbedThumbnail'})

    if postprocessors:
        ydl_opts['postprocessors'] = postprocessors

    _paused_by_user = False
    try:
        notify_ui("YouTube Downloader", "Видео загружается...")

        log_message(f"DEBUG ydl_opts: {ydl_opts}")
        ydl_opts["progress_hooks"] = [progress_hook]

        def _do_download(extra_opts: dict):
            opts = dict(ydl_opts)
            opts.update(extra_opts)
            with yt_dlp.YoutubeDL(opts) as ydl:
                dl_info = ydl.extract_info(url, download=True)
                # После постпроцессоров yt-dlp итоговый путь лежит в requested_downloads[0]['filepath']
                req_dls = dl_info.get("requested_downloads") or []
                final_p = req_dls[0].get("filepath") if req_dls else None
                if not final_p or not os.path.exists(final_p):
                    final_p = ydl.prepare_filename(dl_info)
                    if settings.get("conversion_enabled", True):
                        base_no_ext = os.path.splitext(final_p)[0]
                        cand = f"{base_no_ext}.{settings['download_format']}"
                        if os.path.exists(cand):
                            final_p = cand
                return dl_info, final_p

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

        # Умное прописывание ID3v2.3-тегов для MP3 (если пустые или битые)
        if final_file and final_file.lower().endswith(".mp3"):
            _dl_utils.ensure_mp3_tags(final_file, artist=author, title=video_title)

        # Опция «Скачивать с описаниями»: описание в отдельный .txt с именем видео
        if settings.get("download_with_description", False):
            _save_description(save_path, os.path.basename(final_file),
                              info.get("description", ""))

        if from_queue:
            remove_from_queue(url)

        notify_ui("YouTube Downloader", "Видео загружено успешно!")

        log_message(f"SUCCESS Загрузка завершена: {url}")

    except _UserStop:
        if _dl_utils.cancel_requested:
            _paused_by_user = False
            _dl_utils.cancel_requested = False
            _dl_utils.stop_requested = False
            _dl_utils.is_paused = False
            if from_queue:
                remove_from_queue(url)
            # Даём yt-dlp закрыть файловые дескрипторы и удаляем недокачанные фрагменты
            time.sleep(0.3)
            removed = _dl_utils.cleanup_download_fragments()
            update_download_status("Загрузка отменена", -1)
            log_message(f"INFO Загрузка отменена и удалена из очереди: {url} (удалено фрагментов: {removed})")
        else:
            _paused_by_user = True
            add_to_queue_front(url)
            _dl_utils.is_paused = True
            _dl_utils.stop_requested = False
            update_download_status("На паузе", 0)
            log_message(f"INFO Загрузка приостановлена: {url}")

    except Exception as e:
        error_message = f"ERROR Ошибка загрузки видео: {url}. Подробности: {e}"
        notify_ui("YouTube Downloader", f"Ошибка: {str(e)}")
        log_message(error_message)
        log_message(f"DEBUG Трассировка: {traceback.format_exc()}")

        if from_queue:
            remove_from_queue(url)
        return

    finally:
        _dl_utils.current_download_url = ""
        if not _paused_by_user:
            _dl_utils.active_temp_files.clear()
            on_download_complete()
        else:
            is_downloading = False
            _dl_utils.release_download()

_rl_state = {"time": 0.0, "bytes": 0, "limit": 0.0}


def _apply_rate_limit(downloaded_bytes: int):
    """Динамический лимит скорости на лету (применяется сразу при смене настройки)."""
    limit_mbps = float(settings.get("rate_limit_mbps", 0.0) or 0.0)
    now = time.monotonic()
    if limit_mbps <= 0:
        _rl_state["limit"] = 0.0
        _rl_state["time"] = now
        _rl_state["bytes"] = downloaded_bytes
        return
    if _rl_state["limit"] != limit_mbps or downloaded_bytes < _rl_state["bytes"] or _rl_state["time"] == 0.0:
        _rl_state["limit"] = limit_mbps
        _rl_state["time"] = now
        _rl_state["bytes"] = downloaded_bytes
        return
    delta_bytes = downloaded_bytes - _rl_state["bytes"]
    if delta_bytes <= 0:
        return
    limit_bps = limit_mbps * 1024.0 * 1024.0
    expected = delta_bytes / limit_bps
    elapsed = now - _rl_state["time"]
    if expected > elapsed:
        wait = expected - elapsed
        end_t = now + min(wait, 2.0)
        while time.monotonic() < end_t:
            if _dl_utils.cancel_requested or _dl_utils.stop_requested:
                raise _UserStop()
            time.sleep(min(0.05, max(0.0, end_t - time.monotonic())))
    # Сдвигаем окно каждые 2 секунды, чтобы сглаживать рывки
    now2 = time.monotonic()
    if now2 - _rl_state["time"] >= 2.0:
        _rl_state["time"] = now2
        _rl_state["bytes"] = downloaded_bytes


def progress_hook(d):
    try:
        for key in ("tmpfilename", "filename"):
            p = d.get(key)
            if p:
                _dl_utils.active_temp_files.add(p)
    except Exception:
        pass

    if _dl_utils.cancel_requested or _dl_utils.stop_requested:
        raise _UserStop()

    try:
        if d["status"] == "downloading":
            if "total_bytes" in d and d["total_bytes"] is not None:
                _dl_utils.global_file_size = d["total_bytes"]
            elif "total_bytes_estimate" in d and d["total_bytes_estimate"] is not None:
                _dl_utils.global_file_size = d["total_bytes_estimate"]

            if "downloaded_bytes" in d and d["downloaded_bytes"] is not None:
                _dl_utils.global_downloaded = d["downloaded_bytes"]
                _apply_rate_limit(_dl_utils.global_downloaded)
                update_speed(_dl_utils.global_downloaded)

            total = _dl_utils.global_file_size
            downloaded = _dl_utils.global_downloaded
            percent = (downloaded / total * 100) if total else 0
            update_download_status("Загрузка...", int(percent), downloaded, total)

        elif d["status"] == "finished":
            update_download_status("Готово!", 100, 0, 0)
            _dl_utils.reset_progress()

    except _UserStop:
        raise
    except Exception as e:
        log_message(f"Ошибка в progress_hook: {e}")

def download_channel_with_selection(channel_url):
    """Открывает PyQt6-окно выбора видео с канала."""
    log_message(f"INFO Обработка канала: {channel_url}")
    open_channel_ui(channel_url)


def download_playlist_with_selection(playlist_url):
    """Открывает PyQt6-окно выбора видео из плейлиста."""
    log_message(f"INFO Обработка плейлиста: {playlist_url}")
    open_playlist_ui(playlist_url)
