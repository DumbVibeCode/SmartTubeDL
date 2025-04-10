import os
import time
from queues import get_queue_count
import ui
import utils
from PIL import Image, ImageDraw
from config import save_settings, set_format_mp3, set_format_mp4, set_quality_1080p, set_quality_480p, set_quality_720p, show_settings, toggle_auto_capture as config_toggle_auto_capture, toggle_conversion as config_toggle_conversion
from download_history import show_history
from logger import log_message
from ui import search_youtube_videos, settings, save_settings_var, search_window
from pystray import MenuItem as item, Icon
from config import format_size, save_settings
import threading
import sys

download_status = "Ожидание..."

icon_path = os.path.join(os.getcwd(), "icon.ico")
if not os.path.exists(icon_path):
    log_message("Файл icon.ico не найден, используется стандартная иконка")
    default_image = Image.new('RGB', (64, 64), (255, 255, 255))
    draw = ImageDraw.Draw(default_image)
    draw.rectangle([16, 16, 48, 48], fill="red", outline="black")
    tray_icon = Icon("YouTube Downloader", default_image, "YouTube Downloader - Ожидание...", menu=())
else:
    tray_icon = Icon("YouTube Downloader", Image.open(icon_path), "YouTube Downloader - Ожидание...", menu=())

def create_image():
    image = Image.new('RGB', (64, 64), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle([16, 16, 48, 48], fill="red", outline="black")
    return image

def create_progress_icon(progress):
    size = 64
    image = Image.new("RGBA", (size, size), (255, 255, 255, 0))
    draw = ImageDraw.Draw(image)
    draw.arc([4, 4, size - 4, size - 4], start=0, end=int(3.6 * progress), fill="blue", width=6)
    return image

def update_tray_icon(tray_icon, progress):
    if progress >= 100:
        icon_path = os.path.join(os.getcwd(), "icon.ico")
        if os.path.exists(icon_path):
            tray_icon.icon = Image.open(icon_path)
        else:
            tray_icon.icon = create_image()
    else:
        tray_icon.icon = create_progress_icon(progress)

def update_download_status(status, progress=None, downloaded=0, total_size=0):
    global download_status, tray_icon
    download_status = status

    try:
        if progress is not None:
            update_tray_icon(tray_icon, progress)
        
        hover_text = "YouTube Downloader"
        if status == "Загрузка..." and utils.download_speed != "0 KB/s":
            downloaded_str = format_size(downloaded)
            total_str = format_size(total_size) if total_size > 0 else "???"
            hover_text = f"Скорость: {utils.download_speed} ({downloaded_str} из {total_str})"
        else:
            hover_text = f"{hover_text} - {status}"

        tray_icon.title = hover_text
        tray_icon.menu = generate_menu()
        
    except Exception as e:
        log_message(f"Ошибка при обновлении статуса: {e}")
        try:
            tray_icon.icon = Image.open("icon.ico")
        except Exception as e:
            log_message(f"Ошибка при восстановлении иконки: {e}")

def toggle_auto_capture(icon, item):
    config_toggle_auto_capture(icon, item)
    icon.menu = generate_menu()
    log_message("Меню обновлено после toggle_auto_capture")

def toggle_conversion(icon, item):
    config_toggle_conversion(icon, item)
    icon.menu = generate_menu()
    log_message("Меню обновлено после toggle_conversion")

def set_format_mp3_with_update(icon, item):
    set_format_mp3(icon, item)  # Вызываем оригинальную функцию
    icon.menu = generate_menu()  # Обновляем меню
    log_message("Меню обновлено после выбора MP3")

def set_format_mp4_with_update(icon, item):
    set_format_mp4(icon, item)  # Вызываем оригинальную функцию
    icon.menu = generate_menu()  # Обновляем меню
    log_message("Меню обновлено после выбора MP4")

def generate_menu():
    queue_count = get_queue_count()
    queue_info = f" ({queue_count})" if queue_count > 0 else ""
    
    is_video_format = settings["download_format"] == "mp4"
    
    menu_items = [
        item('Выбрать папку', show_settings),
        item('История загрузок', show_history),
        item('Поиск на YouTube', search_youtube_videos),
        item('────────────', lambda icon, item: None),
        item('Формат:', lambda icon, item: None, enabled=False),
        item('  Музыка (MP3)', set_format_mp3_with_update, checked=lambda item: settings["download_format"] == "mp3"),
        item('  Видео (MP4)', set_format_mp4_with_update, checked=lambda item: settings["download_format"] == "mp4"),
        item('────────────', lambda icon, item: None),
        item('Качество:', lambda icon, item: None, enabled=False),
        item('  1080p', set_quality_1080p, checked=lambda item: settings["video_quality"] == "1080p", enabled=is_video_format),
        item('  720p', set_quality_720p, checked=lambda item: settings["video_quality"] == "720p", enabled=is_video_format),
        item('  480p', set_quality_480p, checked=lambda item: settings["video_quality"] == "480p", enabled=is_video_format),
        item('────────────', lambda icon, item: None),
        item('Автоперехват ссылок', toggle_auto_capture, checked=lambda item: settings["auto_capture_enabled"]),
        item('Конвертация', toggle_conversion, checked=lambda item: settings["conversion_enabled"]),
        item('────────────', lambda icon, item: None),
        item(f'Статус: {download_status}', lambda icon, item: None),
    ]
    
    if queue_count > 0:
        menu_items.append(item(f'В очереди{queue_info}', lambda icon, item: None))
        
    menu_items.append(item('Выход', exit_app))
    
    return tuple(menu_items)

def show_notification(icon, title, message):
    try:
        icon.notify(message, title)
        time.sleep(2)
        icon.notify("", "")
    except Exception as e:
        log_message(f"Ошибка при отображении уведомления: {e}")

def run_tray():
    global tray_icon
    tray_icon.menu = generate_menu()
    tray_icon.run()

def exit_app():
    """Завершает работу программы, сохраняя настройки при необходимости"""
    try:
        # Проверяем, нужно ли сохранять настройки
        should_save = settings.get("save_settings_on_exit", False)
        
        if should_save:
            save_settings(settings)
            log_message("INFO Все настройки сохранены при выходе из программы")
        else:
            log_message("INFO Выход без сохранения настроек")

        # Закрываем окно поиска, если оно открыто
        if search_window is not None and getattr(search_window, "winfo_exists", lambda: False)():
            try:
                search_window.destroy()
                log_message("INFO Окно поиска закрыто при выходе")
            except Exception as e:
                log_message(f"ERROR Не удалось закрыть окно поиска: {e}")

        # Уничтожаем иконку трея
        tray_icon.stop()

        # Безопасный выход
        sys.exit(0)

    except Exception as e:
        log_message(f"ERROR Ошибка при завершении программы: {e}")
        os._exit(1)  # Используем os._exit для принудительного завершения