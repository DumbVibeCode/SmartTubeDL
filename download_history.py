import os
import json
import time
from logger import log_message

DOWNLOAD_HISTORY_FILE = os.path.join(os.getcwd(), "download_history.json")


def add_to_history(url, title, format_type, duration=0):
    """Добавляет информацию о загруженном видео в историю"""
    history = load_download_history()

    # Создаем запись о загрузке
    download_record = {
        "url": url,
        "title": title,
        "format": format_type,
        "duration": duration,  # Длительность в секундах
        "date": time.strftime("%Y-%m-%d %H:%M:%S")
    }

    # Добавляем в начало списка (новые загрузки сверху)
    history.insert(0, download_record)

    # Ограничиваем историю 1000 записями
    if len(history) > 1000:
        history = history[:1000]

    # Сохраняем историю
    save_download_history(history)
    log_message(f"INFO Добавлена запись в историю загрузок: {title}")


def load_download_history():
    """Загружает историю загрузок из файла"""
    if not os.path.exists(DOWNLOAD_HISTORY_FILE):
        log_message("INFO Файл истории загрузок не существует, создаем пустой список")
        return []

    try:
        with open(DOWNLOAD_HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log_message(f"ERROR Ошибка при загрузке истории: {e}")
        return []


def save_download_history(history):
    """Атомарно сохраняет историю загрузок в файл"""
    try:
        history_dir = os.path.dirname(DOWNLOAD_HISTORY_FILE)
        if history_dir and not os.path.exists(history_dir):
            os.makedirs(history_dir, exist_ok=True)

        tmp_path = DOWNLOAD_HISTORY_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, DOWNLOAD_HISTORY_FILE)

        log_message(f"INFO История загрузок сохранена ({len(history)} записей)")
    except Exception as e:
        log_message(f"ERROR Ошибка при сохранении истории: {e}")