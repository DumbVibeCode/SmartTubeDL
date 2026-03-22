import os
import json
import subprocess
import threading
import time
from tkinter import Tk, filedialog, messagebox
import tkinter
import requests
from logger import log_message

SETTINGS_FILE = os.path.join(os.getcwd(), "settings.json")
is_downloading = False
api_key_var = ""
invidious_url_var = ""

# Дефолтные настройки
DEFAULT_SETTINGS = {
    "download_folder": os.path.expanduser("~"),
    "auto_capture_enabled": True,
    "download_format": "mp4",
    "video_quality": "1080p",
    "conversion_enabled": True,
    "save_settings_on_exit": False,
    "youtube_api_key": "",
    "invidious_url": "http://localhost:3000",
    "last_search_query": "",
    "search_type": "video",
    "sort_order": "relevance",
    "max_results": "10",
    "use_alternative_api": False,
    "search_in_descriptions": False,
    "advanced_search": False,
    "advanced_query": "",
    "debug_mode": False  # Новый параметр
}

def format_size(size_bytes):
    """Форматирует размер в читаемый вид (КБ, МБ, ГБ)"""
    if size_bytes < 1024:
        return f"{size_bytes} Б"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} КБ"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} МБ"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} ГБ"

def load_settings():
    """Загружает настройки из файла или возвращает дефолтные"""
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                loaded_settings = json.load(f)
                log_message(f"INFO Загружены настройки из файла (save_settings_on_exit = {loaded_settings.get('save_settings_on_exit', False)})")
                # Добавляем недостающие ключи из DEFAULT_SETTINGS
                for key, value in DEFAULT_SETTINGS.items():
                    if key not in loaded_settings:
                        loaded_settings[key] = value
                return loaded_settings
        else:
            log_message("INFO Файл настроек не найден, используются дефолтные настройки")
            return DEFAULT_SETTINGS.copy()
    except json.JSONDecodeError:
        log_message("ERROR Файл настроек поврежден, используются дефолтные настройки")
        return DEFAULT_SETTINGS.copy()

def save_settings(settings):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log_message(f"ERROR Ошибка при сохранении настроек: {e}")

def update_single_setting(key, value):
    try:
        with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
            current_settings = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        current_settings = DEFAULT_SETTINGS.copy()
    
    current_settings[key] = value
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(current_settings, f, ensure_ascii=False, indent=4)

def initialize_settings():
    return load_settings()

def toggle_conversion(icon, item):
    settings["conversion_enabled"] = not settings["conversion_enabled"]
    save_settings(settings)
    log_message(f"Конвертация {'включена' if settings['conversion_enabled'] else 'выключена'}")

def set_convert_original(icon, item):
    settings["convert_format"] = "original"
    save_settings(settings)
    log_message("Формат конвертации установлен: исходный формат")

def set_convert_mp3(icon, item):
    settings["convert_format"] = "mp3"
    save_settings(settings)
    log_message("Формат конвертации установлен: MP3")

def set_convert_mp4(icon, item):
    settings["convert_format"] = "mp4"
    save_settings(settings)
    log_message("Формат конвертации установлен: MP4")

def set_quality_1080p(icon, item):
    settings["video_quality"] = "1080p"
    save_settings(settings)
    log_message("Качество видео установлено: 1080p")

def set_quality_720p(icon, item):
    settings["video_quality"] = "720p"
    save_settings(settings)
    log_message("Качество видео установлено: 720p")

def set_quality_480p(icon, item):
    settings["video_quality"] = "480p"
    save_settings(settings)
    log_message("Качество видео установлено: 480p")

def set_format_mp3(icon, item):
    settings["download_format"] = "mp3"
    save_settings(settings)
    log_message("Формат загрузки изменён на MP3")

def set_format_mp4(icon, item):
    settings["download_format"] = "mp4"
    save_settings(settings)
    log_message("Формат загрузки изменён на MP4")

def set_download_folder():
    root = Tk()
    root.withdraw()
    folder = filedialog.askdirectory()
    if folder:
        settings["download_folder"] = folder
        save_settings(settings)
        messagebox.showinfo("Настройки", f"Папка сохранения изменена на: {folder}")
        log_message(f"Выбрана папка: {folder}")
    root.destroy()

def toggle_auto_capture(icon, item):
    from clipboard import last_clipboard
    
    settings["auto_capture_enabled"] = not settings["auto_capture_enabled"]
    save_settings(settings)
    
    if settings["auto_capture_enabled"]:
        try:
            root = tkinter.Tk()
            root.withdraw()
            clipboard_snapshot = root.clipboard_get()
            root.destroy()
            last_clipboard = clipboard_snapshot
            log_message(f"Автоперехват ссылок включен, текущий буфер: '{clipboard_snapshot}'")
        except tkinter.TclError:
            last_clipboard = ""
            log_message("Автоперехват ссылок включен, буфер обмена пуст или недоступен")
    else:
        log_message("Автоперехват ссылок выключен")

def show_settings(icon, item):
    threading.Thread(target=set_download_folder).start()

def ensure_invidious_running():
    import docker_manager
    if docker_manager.is_invidious_running():
        log_message("SUCCESS Локальный сервер Invidious уже запущен")
        return
    log_message("INFO Запускаем Invidious через Docker...")
    ok, msg = docker_manager.start_invidious()
    if not ok:
        log_message(f"ERROR {msg}")
        return
    for _ in range(30):
        time.sleep(1)
        if docker_manager.is_invidious_running():
            log_message("SUCCESS Invidious успешно запущен")
            return
    log_message("Ошибка: Invidious не ответил после запуска (timeout 30 сек)")

def format_duration(duration):
    if not duration:
        return 'N/A'
    try:
        duration = duration[2:]
        hours = "00"
        minutes = "00"
        seconds = "00"
        if 'H' in duration:
            hours, duration = duration.split('H')
            hours = hours.zfill(2)
        if 'M' in duration:
            minutes, duration = duration.split('M')
            minutes = minutes.zfill(2)
        if 'S' in duration:
            seconds = duration.replace('S', '')
            seconds = seconds.zfill(2)
        if hours != "00":
            return f"{hours}:{minutes}:{seconds}"
        else:
            return f"{minutes}:{seconds}"
    except Exception as e:
        log_message(f"Ошибка форматирования длительности '{duration}': {e}")
        return 'N/A'

def format_invidious_duration(seconds):
    try:
        if not seconds:
            return "00:00:00"
        seconds = int(seconds)
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    except Exception as e:
        log_message(f"Ошибка форматирования времени Invidious: {e}")
        return "00:00:00"
    
def bind_var_to_settings(var, key):
    def callback(*args):
        settings[key] = var.get()
        # log_message(f"DEBUG settings[{key!r}] обновлено: {var.get()}")
    var.trace_add("write", callback)    

settings = initialize_settings()