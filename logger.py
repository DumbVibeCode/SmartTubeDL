import os
import time
import threading

LOG_FILE = os.path.join(os.getcwd(), "log.txt")
_write_lock = threading.Lock()  # сериализует запись: при параллельных воркерах
                                # строки иначе накладываются и портят файл (битый UTF-8)


def log_message(message):
    """Потокобезопасно логирует сообщение в файл."""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    full_message = f"{timestamp} - {message}"

    try:
        with _write_lock:
            with open(LOG_FILE, "a", encoding="utf-8") as log:
                log.write(full_message + "\n")
    except Exception as e:
        print(f"DEBUG Ошибка записи в лог: {e}")


def clear_log():
    """Очищает лог-файл."""
    try:
        with _write_lock:
            with open(LOG_FILE, "w", encoding="utf-8") as log:
                log.write("")
        log_message("SUCCESS Лог очищен")
    except Exception as e:
        log_message(f"ERROR Ошибка при очистке лога: {e}")