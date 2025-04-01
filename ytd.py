import os
import sys
import threading
import time
import msvcrt  # Для Windows

from config import initialize_settings, save_settings
from tray import run_tray
from clipboard import clear_clipboard, start_monitoring
from queues import check_queue_on_startup, ensure_queue_file_exists
from logger import LOG_FILE, log_message
LOCK_FILE = "lockfile.lock"

# Инициализация настроек
settings = initialize_settings()

# Гарантированно создаём лог-файл
try:
    with open(LOG_FILE, "a", encoding="utf-8") as log:
        log.write("\n--- Программа запущена ---\n")
except Exception as e:
    print(f"ERROR Ошибка при создании лога: {e}")

# Установка формата по умолчанию, если его нет
if "download_format" not in settings:
    settings["download_format"] = "mp4"
    save_settings(settings)

# Очистка буфера обмена и запуск процессов
clear_clipboard()
start_monitoring()
ensure_queue_file_exists()

# Запуск трея и очереди в отдельных потоках
# threading.Thread(target=run_tray, daemon=True).start()
# threading.Thread(target=check_queue_on_startup).start()

def is_already_running():
    try:
        # Открываем файл для блокировки
        lock_fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR)
        # Пытаемся установить эксклюзивную блокировку
        msvcrt.locking(lock_fd, msvcrt.LK_NBLCK, 1)  # LK_NBLCK — неблокирующая блокировка
        # Если блокировка успешна, оставляем файл открытым до конца программы
        return False, lock_fd
    except OSError:
        # Если блокировка не удалась, значит другая копия уже работает
        return True, None
    except Exception as e:
        log_message(f"Ошибка при проверке блокировки: {e}")
        return True, None

def main():
    # Проверяем, запущена ли уже копия
    running, lock_fd = is_already_running()
    if running:
        log_message("Программа уже запущена. Вторая копия не будет запущена.")
        print("Программа уже запущена. Закройте первую копию, чтобы запустить новую.")
        sys.exit(1)

    try:
        # Инициализируем настройки
        initialize_settings()
        log_message("Программа запущена")
        
        # Запускаем трей
        run_tray()

    finally:
        # Освобождаем блокировку и удаляем файл при выходе
        if lock_fd is not None:
            try:
                msvcrt.locking(lock_fd, msvcrt.LK_UNLCK, 1)  # Снимаем блокировку
                os.close(lock_fd)
                os.remove(LOCK_FILE)
                log_message("Блокировка снята, файл удалён")
            except Exception as e:
                log_message(f"Ошибка при снятии блокировки: {e}")

if __name__ == "__main__":
    main()

# Держим программу открытой
while True:
    time.sleep(10)