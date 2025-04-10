import time
import html
import re

from config import format_size
from logger import log_message

# Глобальные переменные для прогресса и скорости
global_file_size = 0
global_downloaded = 0
download_speed = "0 KB/s"
last_update_time = time.time()
last_downloaded_bytes = 0

save_settings_var = None 

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

def format_date(date_str):
    """Преобразует ISO 8601 дату в более читаемый формат"""
    try:
        # Дата в формате 2021-05-20T15:30:45Z
        date_part = date_str.split('T')[0]  # Берем только часть с датой
        year, month, day = date_part.split('-')
        return f"{day}.{month}.{year}"
    except Exception as e:
        log_message(f"Ошибка форматирования даты: {e}")
        return date_str

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
    