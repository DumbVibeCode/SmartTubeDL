import sys
import threading
import pyperclip
import re
import time
from download import download_channel_with_selection, download_playlist_with_selection, download_video
from logger import log_message
from config import settings
from queues import add_to_queue
import utils as _utils
from utils import is_download_active, notify_ui
from clipboard_utils import get_last_copy_time

last_clipboard = ""
clipboard_monitor_disabled = False
ignore_clipboard_url = None
current_downloading_url = None
_last_seq_num = None


def _get_clipboard_seq():
    """Возвращает системный счётчик изменений буфера Windows (меняется при каждом Ctrl+C,
    даже если скопирована та же самая строка). На других ОС возвращает None."""
    if sys.platform == "win32":
        try:
            import ctypes
            return int(ctypes.windll.user32.GetClipboardSequenceNumber())
        except Exception:
            return None
    return None


def is_youtube_link(text):
    if not text:
        return False
    return re.match(r'^https?://(www\.|m\.)?(youtube\.com|youtu\.be)/', text.strip()) is not None


def detect_clipboard_change():
    global last_clipboard, clipboard_monitor_disabled, _last_seq_num
    if clipboard_monitor_disabled:
        return None
    try:
        seq = _get_clipboard_seq()
        if seq is not None:
            if seq == _last_seq_num:
                return None
            _last_seq_num = seq
            current_clipboard = (pyperclip.paste() or "").strip()
            if current_clipboard and is_youtube_link(current_clipboard):
                return current_clipboard
            return None

        # Фолбэк для не-Windows: сравнение текста
        current_clipboard = (pyperclip.paste() or "").strip()
        if current_clipboard and current_clipboard != last_clipboard and is_youtube_link(current_clipboard):
            return current_clipboard
    except Exception as e:
        log_message(f"ERROR Ошибка доступа к буферу через pyperclip: {e}")
        time.sleep(1)
    return None


def clear_clipboard():
    """Оставлено для обратной совместимости; системный буфер пользователя не стирается."""
    pass


def clipboard_monitor():
    global last_clipboard, clipboard_monitor_disabled, ignore_clipboard_url, current_downloading_url, _last_seq_num
    # Фиксируем текущее состояние буфера при старте (не стираем данные пользователя)
    _last_seq_num = _get_clipboard_seq()
    try:
        last_clipboard = (pyperclip.paste() or "").strip()
    except Exception:
        last_clipboard = ""
    log_message("INFO Мониторинг буфера обмена запущен")

    while True:
        time.sleep(0.4)
        if clipboard_monitor_disabled or not settings.get("auto_capture_enabled", True):
            # Синхронизируем счётчик, чтобы старые копирования не выстрелили при включении
            seq = _get_clipboard_seq()
            if seq is not None:
                _last_seq_num = seq
            continue
        try:
            current_clipboard = detect_clipboard_change()
            if not current_clipboard:
                continue

            # Игнорируем ссылки, только что скопированные самой программой (< 2 сек)
            if time.time() - get_last_copy_time() < 2:
                last_clipboard = current_clipboard
                continue

            last_clipboard = current_clipboard

            # Не дублируем ссылку, которая прямо сейчас качается
            if _utils.current_download_url and current_clipboard == _utils.current_download_url:
                continue

            if is_youtube_link(current_clipboard):
                log_message(f"INFO Обнаружена новая ссылка: {current_clipboard}")
                if "playlist?list=" in current_clipboard:
                    log_message("INFO Обнаружена ссылка на плейлист")
                    notify_ui("Плейлист YouTube", "Ссылка подхвачена — открываю вкладку плейлиста...")
                    threading.Thread(target=download_playlist_with_selection, args=(current_clipboard,), daemon=True).start()
                elif "/channel/" in current_clipboard or "/c/" in current_clipboard or "/user/" in current_clipboard or "/@" in current_clipboard:
                    log_message("INFO Обнаружена ссылка на канал")
                    notify_ui("Канал YouTube", "Ссылка подхвачена — открываю вкладку канала...")
                    threading.Thread(target=download_channel_with_selection, args=(current_clipboard,), daemon=True).start()
                else:
                    if is_download_active():
                        log_message("INFO Загрузка уже идет, добавляем в очередь")
                        notify_ui("Очередь YouTube", "Ссылка добавлена в очередь загрузки")
                        threading.Thread(target=add_to_queue, args=(current_clipboard,), daemon=True).start()
                    else:
                        log_message("INFO Запускаем загрузку напрямую")
                        current_downloading_url = current_clipboard
                        threading.Thread(target=download_video, args=(current_clipboard,), daemon=True).start()
        except Exception as e:
            log_message(f"ERROR Ошибка при мониторинге буфера обмена: {e}")


def start_monitoring():
    threading.Thread(target=clipboard_monitor, daemon=True).start()