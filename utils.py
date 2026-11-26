import os
import time
import html
import re
import threading

import config as _config
from config import format_size, atomic_write_json
from logger import log_message

# Глобальные переменные для прогресса и скорости
global_file_size = 0
global_downloaded = 0
download_speed = "0 KB/s"
last_update_time = time.time()
last_downloaded_bytes = 0

save_settings_var = None

# Управление загрузкой (пауза/стоп) и потокобезопасный флаг активной загрузки
_download_lock = threading.Lock()
_downloading_event = threading.Event()

stop_requested = False       # True = остановить текущую загрузку (пауза)
cancel_requested = False     # True = отменить текущую загрузку, удалить из очереди и убрать фрагменты
current_download_url = ""    # URL текущей активной загрузки
is_paused = False            # True = загрузка поставлена на паузу
active_temp_files = set()    # пути временных/недокачанных файлов текущей загрузки
active_subprocesses = set()  # активные дочерние процессы (например, ffmpeg), чтобы гасить при выходе/отмене


def kill_active_subprocesses() -> None:
    """Принудительно завершает все активные дочерние процессы (ffmpeg, node и др.)."""
    import subprocess
    procs = list(active_subprocesses)
    active_subprocesses.clear()
    for p in procs:
        try:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=1.5)
                except Exception:
                    p.kill()
        except Exception:
            pass

    # На Windows дополнительно гасим всех прямых потомков текущего процесса (например, ffmpeg из yt-dlp)
    if os.name == "nt":
        try:
            my_pid = os.getpid()
            cf = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            ps = (
                f"Get-CimInstance Win32_Process -Filter 'ParentProcessId={my_pid}' | "
                "ForEach-Object { $_.ProcessId }"
            )
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                capture_output=True, text=True, timeout=5, creationflags=cf,
            ).stdout
            for token in out.split():
                if token.strip().isdigit():
                    cpid = int(token.strip())
                    if cpid != my_pid:
                        subprocess.run(
                            ["taskkill", "/PID", str(cpid), "/T", "/F"],
                            capture_output=True, timeout=5, creationflags=cf,
                        )
        except Exception:
            pass


def cleanup_download_fragments(paths=None) -> int:
    """Удаляет недокачанные файлы и фрагменты yt-dlp (.part, .ytdl, .part-Frag*, .temp.*)."""
    import glob
    targets = set(paths or active_temp_files)
    removed = 0
    candidates = set()
    for p in targets:
        if not p:
            continue
        candidates.add(p)
        candidates.add(p + ".part")
        candidates.add(p + ".ytdl")
        base = p[:-5] if p.endswith(".part") else p
        candidates.add(base)
        candidates.add(base + ".part")
        candidates.add(base + ".ytdl")
        try:
            for extra in glob.glob(glob.escape(base) + ".part-Frag*"):
                candidates.add(extra)
            for extra in glob.glob(glob.escape(base) + ".temp.*"):
                candidates.add(extra)
        except Exception:
            pass

    for fpath in candidates:
        if not fpath or not os.path.isfile(fpath):
            continue
        for _ in range(3):
            try:
                os.remove(fpath)
                removed += 1
                break
            except PermissionError:
                time.sleep(0.25)
            except OSError:
                break
    active_temp_files.clear()
    return removed

# UI-колбэки (регистрируются в app_qt.py, разрывают зависимость ядра от UI)
_ui_callbacks = {
    "notify": None,
    "status": None,
    "open_channel": None,
    "open_playlist": None,
}


def register_ui_callbacks(notify=None, status=None, open_channel=None, open_playlist=None) -> None:
    """Регистрирует UI-обработчики для уведомлений, статуса и окон каналов/плейлистов."""
    if notify is not None:
        _ui_callbacks["notify"] = notify
    if status is not None:
        _ui_callbacks["status"] = status
    if open_channel is not None:
        _ui_callbacks["open_channel"] = open_channel
    if open_playlist is not None:
        _ui_callbacks["open_playlist"] = open_playlist


def notify_ui(title: str, message: str) -> None:
    """Отправляет уведомление в UI (если зарегистрирован обработчик)."""
    cb = _ui_callbacks.get("notify")
    if cb:
        cb(str(title), str(message))


def update_status_ui(status: str, progress=None, downloaded: int = 0, total_size: int = 0) -> None:
    """Обновляет статус загрузки в UI (если зарегистрирован обработчик)."""
    cb = _ui_callbacks.get("status")
    if cb:
        cb(status, progress, downloaded, total_size)


def open_channel_ui(url: str) -> None:
    """Открывает окно канала в UI (если зарегистрирован обработчик)."""
    cb = _ui_callbacks.get("open_channel")
    if cb:
        cb(str(url))


def open_playlist_ui(url: str) -> None:
    """Открывает окно плейлиста в UI (если зарегистрирован обработчик)."""
    cb = _ui_callbacks.get("open_playlist")
    if cb:
        cb(str(url))


def is_download_active() -> bool:
    """Возвращает True, если сейчас идёт активная загрузка."""
    return _downloading_event.is_set()


def try_acquire_download() -> bool:
    """Атомарно захватывает слот загрузки. Возвращает False, если загрузка уже идёт."""
    with _download_lock:
        if _downloading_event.is_set():
            return False
        _downloading_event.set()
        _config.is_downloading = True
        return True


def release_download() -> None:
    """Освобождает слот загрузки."""
    with _download_lock:
        _downloading_event.clear()
        _config.is_downloading = False


def reset_progress() -> None:
    """Сбрасывает счётчики прогресса и скорости загрузки."""
    global global_file_size, global_downloaded, download_speed, last_update_time, last_downloaded_bytes
    global_file_size = 0
    global_downloaded = 0
    download_speed = "0 KB/s"
    last_update_time = time.time()
    last_downloaded_bytes = 0

# Кэш названий для очереди {url: title}
queue_titles: dict = {}

# VK-очередь (параллельная с YouTube, не записывается в файл)
vk_queue: list = []        # [{"key": "vk:...", "label": "Исполнитель - Название"}, ...]
current_vk_key: str = ""   # ключ текущего скачивания ВК

# utils.py
def update_progress(completed: int, total: int, progress_var=None, root=None, status_var=None):
    """Обновляет прогресс-бар и статусную строку (совместимость с search.py)"""
    if total > 0:
        progress = (completed / total) * 100
        if progress_var and root:
            root.after(0, lambda: progress_var.set(progress))
        if status_var and root:
            root.after(0, lambda: status_var.set(f"Обработано {completed}/{total} элементов"))
    else:
        if progress_var and root:
            root.after(0, lambda: progress_var.set(0))
        if status_var and root:
            root.after(0, lambda: status_var.set("Нет элементов для обработки"))

def decode_html_entities(text):
    """Декодирует HTML-сущности в обычный текст"""
    if text:
        return html.unescape(text)
    return text

def format_speed(speed_bytes):
    """Форматирует скорость в читаемый вид"""
    if speed_bytes < 1024:
        return f"{speed_bytes} B/s"
    elif speed_bytes < 1024 * 1024:
        return f"{speed_bytes / 1024:.1f} KB/s"
    else:
        return f"{speed_bytes / (1024 * 1024):.1f} MB/s"

def update_speed(downloaded_bytes):
    """Обновляет скорость загрузки"""
    global download_speed, last_update_time, last_downloaded_bytes
    current_time = time.time()
    time_diff = current_time - last_update_time
    if time_diff >= 1:
        speed_bytes = (downloaded_bytes - last_downloaded_bytes) / time_diff
        download_speed = format_speed(speed_bytes)
        last_update_time = current_time
        last_downloaded_bytes = downloaded_bytes

def prepare_tsquery(text):
    # Преобразуем в tsquery формат: концерт & 1991
    words = re.findall(r'\w+', text)
    return ' & '.join(words)

def safe_filename(text: str, default: str = "file") -> str:
    """Убирает запрещённые в Windows символы из имени файла."""
    cleaned = re.sub(r'[\\/*?:"<>|]', "_", text or "").strip()
    return cleaned or default


def request_state_save() -> None:
    """Просит главное PyQt6-приложение сохранить состояние окон/вкладок (с дебаунсом)."""
    try:
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None and hasattr(app, "request_state_save"):
            app.request_state_save()
    except Exception:
        pass


def format_date(date_str):
    """Преобразует дату в формат ДД.ММ.ГГГГ"""
    try:
        # Убираем возможное время, если есть 'T'
        date_part = date_str.split('T')[0]  # Берем только часть с датой
        
        # Проверяем формат: YYYY-MM-DD или YYYYMMDD
        if '-' in date_part:
            # Формат YYYY-MM-DD
            year, month, day = date_part.split('-')
        else:
            # Формат YYYYMMDD
            year = date_part[:4]
            month = date_part[4:6]
            day = date_part[6:8]
        
        return f"{day}.{month}.{year}"
    except Exception as e:
        log_message(f"Ошибка форматирования даты: {e}, входная строка: {date_str}")
        # Возвращаем дату по умолчанию, если не удалось распарсить
        return "01.01.1970"

# Функция для форматирования числа просмотров
def format_views(views_count):
    """Форматирует число просмотров в более читабельный вид"""
    try:
        count = int(views_count)
        if count < 1000:
            return str(count)
        elif count < 1000000:
            return f"{count/1000:.1f}K"
        else:
            return f"{count/1000000:.1f}M"
    except Exception as e:
        log_message(f"Ошибка форматирования просмотров: {e}")
        return views_count


def resolve_download_folder(base_folder: str, source: str = "", author: str = "", mode: str | None = None) -> str:
    """Возвращает целевую папку загрузки с учётом настройки subfolder_mode и создаёт её."""
    from config import settings
    folder = base_folder or settings.get("download_folder") or os.path.expanduser("~")
    sub_mode = mode if mode is not None else settings.get("subfolder_mode", "none")

    parts = []
    clean_src = safe_filename(source, default="")[:40].strip() if source else ""
    clean_author = safe_filename(author, default="")[:80].strip() if author else ""

    if sub_mode == "source" and clean_src:
        parts.append(clean_src)
    elif sub_mode == "author" and clean_author:
        parts.append(clean_author)
    elif sub_mode == "source_author":
        if clean_src:
            parts.append(clean_src)
        if clean_author:
            parts.append(clean_author)

    target = os.path.join(folder, *parts) if parts else folder
    try:
        os.makedirs(target, exist_ok=True)
    except OSError as e:
        log_message(f"WARNING Не удалось создать подпапку '{target}': {e}")
        return folder
    return target


def format_download_filename(title: str, author: str = "", date_str: str = "",
                             is_music: bool = False, template: str | None = None) -> str:
    """Форматирует безопасное имя файла (без расширения) по настройке filename_template."""
    from datetime import datetime
    from config import settings

    tpl = template if template is not None else settings.get("filename_template", "default")
    t = (title or "").strip() or "media"
    a = (author or "").strip()
    d = (date_str or "").strip()
    if len(d) == 8 and d.isdigit():
        d = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    elif not d:
        d = datetime.now().strftime("%Y-%m-%d")

    if tpl == "author_title":
        raw = f"{a} - {t}" if a else t
    elif tpl == "title_only":
        raw = t
    elif tpl == "date_title":
        raw = f"{d} - {t}"
    else:  # "default"
        raw = (f"{a} - {t}" if (is_music and a) else t)

    return safe_filename(raw, default="media")[:150].strip() or "media"


def _is_broken_tag(val: str) -> bool:
    """Определяет пустые, замещённые () или битые cp1251/latin1 (mojibake) ID3-теги."""
    s = (val or "").strip()
    if not s or s in ("?", "Unknown", "Unknown Artist", "Unknown Title"):
        return True
    if "\ufffd" in s:
        return True
    # Типичный mojibake: cp1251, прочитанный как latin1 (символы 0xC0..0xFF подряд)
    latin_ext = sum(1 for ch in s if 0x00C0 <= ord(ch) <= 0x00FF)
    cyr = sum(1 for ch in s if 0x0400 <= ord(ch) <= 0x04FF)
    if latin_ext >= 3 and cyr == 0:
        return True
    # UTF-8, прочитанный как cp1251 (пары «Р...» / «С...»)
    if ("Ð" in s or "Ñ" in s) and cyr == 0:
        return True
    return False


def _try_fix_mojibake(val: str) -> str:
    """Пробует аккуратно восстановить строку из latin1->cp1251 mojibake."""
    s = (val or "").strip()
    if not s or "\ufffd" in s:
        return s
    try:
        fixed = s.encode("latin1").decode("cp1251")
        if sum(1 for ch in fixed if 0x0400 <= ord(ch) <= 0x04FF) >= 2:
            return fixed
    except Exception:
        pass
    return s


def ensure_mp3_tags(path: str, artist: str = "", title: str = "") -> bool:
    """Умно дописывает/чинит ID3v2.3 (UTF-16) теги в MP3 без перекодирования аудио.

    Если artist и title уже валидные и без кракозябр — файл не трогается.
    Если пустые или битые — прописывает artist/title через ffmpeg -c copy -id3v2_version 3.
    """
    from config import settings
    if not settings.get("fix_mp3_tags", True):
        return False
    if not path or not path.lower().endswith(".mp3") or not os.path.isfile(path):
        return False

    import json
    import shutil
    import subprocess

    ffprobe = shutil.which("ffprobe")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False

    existing = {}
    if ffprobe:
        try:
            cf = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            out = subprocess.check_output(
                [ffprobe, "-v", "quiet", "-print_format", "json", "-show_entries", "format_tags", path],
                creationflags=cf,
                timeout=10,
            )
            data = json.loads(out.decode("utf-8", errors="replace"))
            raw_tags = (data.get("format") or {}).get("tags") or {}
            existing = {k.lower(): str(v) for k, v in raw_tags.items()}
        except Exception:
            existing = {}

    cur_artist = existing.get("artist", "")
    cur_title = existing.get("title", "")
    cur_album = existing.get("album", "")
    cur_comment = existing.get("comment", "")

    need_artist = bool(artist and _is_broken_tag(cur_artist))
    need_title = bool(title and _is_broken_tag(cur_title))
    fixed_album = _try_fix_mojibake(cur_album) if _is_broken_tag(cur_album) and cur_album else ""
    fixed_comment = _try_fix_mojibake(cur_comment) if _is_broken_tag(cur_comment) and cur_comment else ""
    need_album = bool(fixed_album and fixed_album != cur_album)
    need_comment = bool(fixed_comment and fixed_comment != cur_comment)

    if not (need_artist or need_title or need_album or need_comment):
        return False

    tmp_path = path + ".tagtmp.mp3"
    cmd = [
        ffmpeg, "-y", "-v", "error",
        "-i", path,
        "-c", "copy",
        "-map_metadata", "0",
        "-id3v2_version", "3",
        "-write_id3v1", "0",
    ]
    if need_artist:
        cmd.extend(["-metadata", f"artist={artist.strip()}"])
    if need_title:
        cmd.extend(["-metadata", f"title={title.strip()}"])
    if need_album:
        cmd.extend(["-metadata", f"album={fixed_album}"])
    if need_comment:
        cmd.extend(["-metadata", f"comment={fixed_comment}"])
    cmd.append(tmp_path)

    try:
        cf = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        res = subprocess.run(cmd, creationflags=cf, timeout=20, capture_output=True)
        if res.returncode == 0 and os.path.isfile(tmp_path) and os.path.getsize(tmp_path) > 0:
            os.replace(tmp_path, path)
            log_message(f"INFO ID3-теги обновлены (ID3v2.3): {os.path.basename(path)}")
            return True
    except Exception as e:
        log_message(f"WARNING ensure_mp3_tags '{os.path.basename(path)}': {e}")
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
    return False


    