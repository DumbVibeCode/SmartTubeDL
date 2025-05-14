import os
import threading
import time

from logger import log_message
from config import is_downloading

QUEUE_FILE = os.path.join(os.getcwd(), "download_queue.txt")

def ensure_queue_file_exists():
    """Убеждается, что файл очереди существует"""
    if not os.path.exists(QUEUE_FILE):
        try:
            os.makedirs(os.path.dirname(QUEUE_FILE), exist_ok=True)
            with open(QUEUE_FILE, "w", encoding="utf-8") as f:
                pass
            log_message(f"INFO Создан пустой файл очереди: {QUEUE_FILE}")
        except Exception as e:
            log_message(f"ERROR Ошибка при создании файла очереди: {e}")

def add_to_queue(url):
    """Добавляет URL в файловую очередь загрузок"""
    urls = get_queue_urls()
    if url in urls:
        log_message(f"URL уже в очереди: {url}")
        return False
    try:
        with open(QUEUE_FILE, "a", encoding="utf-8") as f:
            f.write(f"{url}\n")
        log_message(f"INFO Добавлено в очередь: {url}")
        return True
    except Exception as e:
        log_message(f"Ошибка при добавлении в очередь: {e}")
        return False

def get_queue_urls():
    """Получает список URL из очереди"""
    if not os.path.exists(QUEUE_FILE):
        return []
    try:
        with open(QUEUE_FILE, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    except Exception as e:
        log_message(f"Ошибка при чтении очереди: {e}")
        return []

def remove_from_queue(url):
    """Удаляет URL из очереди после загрузки"""
    urls = get_queue_urls()
    if url in urls:
        urls.remove(url)
        try:
            with open(QUEUE_FILE, "w", encoding="utf-8") as f:
                for u in urls:
                    f.write(f"{u}\n")
            log_message(f"URL удален из очереди: {url}")
        except Exception as e:
            log_message(f"Ошибка при обновлении очереди: {e}")

def clear_queue_file():
    """Очищает файл очереди полностью"""
    try:
        with open(QUEUE_FILE, "w", encoding="utf-8") as f:
            pass
        log_message("SUCCESS Файл очереди полностью очищен")
    except Exception as e:
        log_message(f"ERROR Ошибка при очистке файла очереди: {e}")

def get_next_url():
    """Возвращает следующий URL из очереди"""
    urls = get_queue_urls()
    return urls[0] if urls else None

def get_queue_count():
    """Возвращает количество URL в очереди"""
    urls = get_queue_urls()
    return len(urls)            

def process_queue():
    """Запускает обработку очереди загрузок"""
    from download import download_video  # Переносим импорт сюда
    # global is_downloading
    # log_message("DEBUG Проверка состояния is_downloading в process_queue")
    if is_downloading:
        # log_message("DEBUG is_downloading = True, обработка очереди отложена")
        return
    else:
        pass
        #log_message("DEBUG is_downloading = False, продолжаем обработку очереди")
    
    url = get_next_url()
    if not url:
        log_message("Очередь пуста")
        clear_queue_file()
        return
    
    log_message(f"INFO Начало загрузки URL из очереди: {url}")
    threading.Thread(target=download_video, args=(url, True)).start()            

def check_queue_on_startup():
    """Проверяет наличие URL в очереди при запуске программы"""
    count = get_queue_count()
    if count > 0:
        log_message(f"При запуске обнаружено {count} URL в очереди")
        time.sleep(3)
        threading.Thread(target=process_queue).start()