import time
import html
import re
import tkinter as tk

from config import format_size
from logger import log_message

# Глобальные переменные для прогресса и скорости
global_file_size = 0
global_downloaded = 0
download_speed = "0 KB/s"
last_update_time = time.time()
last_downloaded_bytes = 0

save_settings_var = None

# Управление загрузкой (пауза/стоп)
stop_requested = False       # True = остановить текущую загрузку
current_download_url = ""    # URL текущей активной загрузки
is_paused = False            # True = загрузка поставлена на паузу

# Кэш названий для очереди {url: title}
queue_titles: dict = {}

# VK-очередь (параллельная с YouTube, не записывается в файл)
vk_queue: list = []        # [{"key": "vk:...", "label": "Исполнитель - Название"}, ...]
current_vk_key: str = ""   # ключ текущего скачивания ВК

# utils.py
def update_progress(completed: int, total: int, progress_var: tk.DoubleVar = None, root: tk.Tk = None, status_var: tk.StringVar = None):
    """Обновляет прогресс-бар и статусную строку"""
    if total > 0:
        progress = (completed / total) * 100
        if progress_var and root:
            root.after(0, lambda: progress_var.set(progress))
        if status_var:
            root.after(0, lambda: status_var.set(f"Обработано {completed}/{total} элементов"))
    else:
        if progress_var and root:
            root.after(0, lambda: progress_var.set(0))
        if status_var:
            root.after(0, lambda: status_var.set("Нет элементов для обработки"))

def decode_html_entities(text):
    """Декодирует HTML-сущности в обычный текст"""
    import html
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
    if time_diff >= 1:  # Обновляем скорость каждые 0.5 секунды
        speed_bytes = (downloaded_bytes - last_downloaded_bytes) / time_diff
        download_speed = format_speed(speed_bytes)
        last_update_time = current_time
        last_downloaded_bytes = downloaded_bytes
        
def prepare_tsquery(text):
    # Преобразуем в tsquery формат: концерт & 1991
    words = re.findall(r'\w+', text)
    return ' & '.join(words)

from logger import log_message  # Предполагается, что log_message импортируется в utils.py

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
    