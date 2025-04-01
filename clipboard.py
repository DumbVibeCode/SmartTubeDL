import threading
import pyperclip
import re
import time
from download import download_channel_with_selection, download_playlist_with_selection, download_video
from logger import log_message
from config import settings
from queues import add_to_queue
from config import is_downloading

last_clipboard = ""
clipboard_monitor_disabled = False
ignore_clipboard_url = None
current_downloading_url = None

def is_youtube_link(text):
    return re.match(r'^https?://(www\.)?(youtube\.com|youtu\.be)/', text) is not None

def detect_clipboard_change():
    global last_clipboard, clipboard_monitor_disabled
    if clipboard_monitor_disabled:
        return None
    try:
        current_clipboard = pyperclip.paste()
        if current_clipboard and current_clipboard != last_clipboard and is_youtube_link(current_clipboard):
            return current_clipboard
    except Exception as e:
        log_message(f"Ошибка доступа к буферу через pyperclip: {e}")
        time.sleep(1)
    return None

def clear_clipboard():
    try:
        pyperclip.copy("")
        log_message("Буфер обмена очищен.")
    except Exception as e:
        log_message(f"Ошибка при очистке буфера обмена: {e}")

def clipboard_monitor():
    global last_clipboard, clipboard_monitor_disabled, ignore_clipboard_url, current_downloading_url
    last_clipboard = ""

    while True:
        time.sleep(1)
        if clipboard_monitor_disabled or not settings["auto_capture_enabled"]:
            continue
        try:
            current_clipboard = detect_clipboard_change()
            if not current_clipboard or current_clipboard == last_clipboard:
                continue
            if current_clipboard == ignore_clipboard_url:
                ignore_clipboard_url = None
                last_clipboard = current_clipboard
                continue
            if current_downloading_url and current_clipboard == current_downloading_url:
                last_clipboard = current_clipboard
                continue
            last_clipboard = current_clipboard
            if current_clipboard.startswith("https://www.youtube.com"):
                log_message(f"INFO Обнаружена новая ссылка: {current_clipboard}")
                if "playlist?list=" in current_clipboard:
                    log_message("INFO Обнаружена ссылка на плейлист")
                    threading.Thread(target=download_playlist_with_selection, args=(current_clipboard,)).start()
                elif "/channel/" in current_clipboard or "/c/" in current_clipboard or "/user/" in current_clipboard or "/@" in current_clipboard:
                    log_message("INFO Обнаружена ссылка на канал")
                    threading.Thread(target=download_channel_with_selection, args=(current_clipboard,)).start()
                else:
                    if is_downloading:
                        log_message("INFO Загрузка уже идет, добавляем в очередь")
                        add_to_queue(current_clipboard)
                    else:
                        log_message("INFO Запускаем загрузку напрямую")
                        current_downloading_url = current_clipboard
                        download_thread = threading.Thread(target=download_video, args=(current_clipboard,))
                        download_thread.start()
                        # Ждём завершения загрузки
                        download_thread.join()  # Блокируем, пока загрузка не завершится
                        current_downloading_url = None
                        clear_clipboard()  # Очищаем буфер после завершения
        except Exception as e:
            log_message(f"ERROR Ошибка при мониторинге буфера обмена: {e}")

def start_monitoring():
    threading.Thread(target=clipboard_monitor, daemon=True).start()