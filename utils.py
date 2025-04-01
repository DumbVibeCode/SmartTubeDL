# utils.py
import time
from config import format_size
from logger import log_message

# Глобальные переменные для прогресса и скорости
global_file_size = 0
global_downloaded = 0
download_speed = "0 KB/s"
last_update_time = time.time()
last_downloaded_bytes = 0

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