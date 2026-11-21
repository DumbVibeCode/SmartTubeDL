import os
import threading
import time

from logger import log_message
from utils import is_download_active

QUEUE_FILE = os.path.join(os.getcwd(), "download_queue.txt")
_queue_lock = threading.Lock()


def _read_queue_unlocked():
    """Читает список URL из файла очереди (вызывать только под _queue_lock)."""
    if not os.path.exists(QUEUE_FILE):
        return []
    try:
        with open(QUEUE_FILE, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    except Exception as e:
        log_message(f"Ошибка при чтении очереди: {e}")
        return []


def _write_queue_unlocked(urls):
    """Атомарно записывает список URL в файл очереди (вызывать только под _queue_lock)."""
    tmp_path = QUEUE_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        for u in urls:
            f.write(f"{u}\n")
    os.replace(tmp_path, QUEUE_FILE)


def ensure_queue_file_exists():
    """Убеждается, что файл очереди существует"""
    with _queue_lock:
        if not os.path.exists(QUEUE_FILE):
            try:
                queue_dir = os.path.dirname(QUEUE_FILE)
                if queue_dir:
                    os.makedirs(queue_dir, exist_ok=True)
                with open(QUEUE_FILE, "w", encoding="utf-8"):
                    pass
                log_message(f"INFO Создан пустой файл очереди: {QUEUE_FILE}")
            except Exception as e:
                log_message(f"ERROR Ошибка при создании файла очереди: {e}")


def add_to_queue(url, title=None):
    """Добавляет URL в файловую очередь загрузок"""
    with _queue_lock:
        urls = _read_queue_unlocked()
        if url in urls:
            log_message(f"URL уже в очереди: {url}")
            return False
        try:
            urls.append(url)
            _write_queue_unlocked(urls)
            if title:
                import utils as _u
                _u.queue_titles[url] = title
            log_message(f"INFO Добавлено в очередь: {url}")
            return True
        except Exception as e:
            log_message(f"Ошибка при добавлении в очередь: {e}")
            return False


def get_queue_urls():
    """Получает список URL из очереди"""
    with _queue_lock:
        return _read_queue_unlocked()


def add_to_queue_front(url):
    """Добавляет URL в начало очереди (используется при паузе)"""
    with _queue_lock:
        urls = _read_queue_unlocked()
        if url in urls:
            urls.remove(url)
        urls.insert(0, url)
        try:
            _write_queue_unlocked(urls)
            log_message(f"INFO URL добавлен в начало очереди: {url}")
        except Exception as e:
            log_message(f"ERROR Ошибка при добавлении в начало очереди: {e}")


def remove_from_queue(url):
    """Удаляет URL из очереди после загрузки"""
    with _queue_lock:
        urls = _read_queue_unlocked()
        if url in urls:
            urls.remove(url)
            try:
                _write_queue_unlocked(urls)
                import utils as _u
                _u.queue_titles.pop(url, None)
                log_message(f"URL удален из очереди: {url}")
            except Exception as e:
                log_message(f"Ошибка при обновлении очереди: {e}")


def clear_queue_file():
    """Очищает файл очереди полностью"""
    with _queue_lock:
        try:
            _write_queue_unlocked([])
            log_message("SUCCESS Файл очереди полностью очищен")
        except Exception as e:
            log_message(f"ERROR Ошибка при очистке файла очереди: {e}")


def get_next_url():
    """Возвращает следующий URL из очереди"""
    with _queue_lock:
        urls = _read_queue_unlocked()
        return urls[0] if urls else None


def get_queue_count():
    """Возвращает количество URL в очереди"""
    with _queue_lock:
        return len(_read_queue_unlocked())


def process_queue():
    """Запускает обработку очереди загрузок"""
    from download import download_video  # Переносим импорт сюда
    if is_download_active():
        return

    url = get_next_url()
    if not url:
        log_message("Очередь пуста")
        clear_queue_file()
        return

    log_message(f"INFO Начало загрузки URL из очереди: {url}")
    threading.Thread(target=download_video, args=(url, True), daemon=True).start()


def check_queue_on_startup():
    """Проверяет наличие URL в очереди при запуске программы"""
    count = get_queue_count()
    if count > 0:
        log_message(f"При запуске обнаружено {count} URL в очереди")
        time.sleep(3)
        threading.Thread(target=process_queue, daemon=True).start()