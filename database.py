import re
import sqlite3
import threading
from logger import log_message

_conn = None
_db_lock = threading.Lock()


def connect_to_database(config_path=None):
    """Инициализирует SQLite FTS5 базу данных в памяти"""
    global _conn
    with _db_lock:
        try:
            _conn = sqlite3.connect(':memory:', check_same_thread=False)
            _conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS video_descriptions
                USING fts5(video_id UNINDEXED, description, tokenize='unicode61')
            """)
            _conn.commit()
            log_message("INFO: База данных (SQLite FTS5) инициализирована")
        except Exception as e:
            log_message(f"[ERROR] Ошибка инициализации SQLite: {e}")
            _conn = None


def insert_description(video_id, description):
    """Добавляет описание видео в базу данных"""
    global _conn
    # Не кешируем пустые описания и строки-заглушки об ошибке — иначе их потом
    # не перезагрузить (кеш «залипает»)
    if not description or description.startswith("Описание недоступно") \
            or description == "Описание отсутствует":
        return
    with _db_lock:
        try:
            if not _conn:
                raise ValueError("Нет активного подключения к базе данных")
            _conn.execute("DELETE FROM video_descriptions WHERE video_id = ?", (video_id,))
            _conn.execute(
                "INSERT INTO video_descriptions(video_id, description) VALUES (?, ?)",
                (video_id, description)
            )
            _conn.commit()
        except Exception as e:
            log_message(f"[ERROR] Ошибка при добавлении описания: {e}")


def search_in_database(query):
    """Ищет видео по описаниям (FTS5, unicode61)"""
    global _conn
    with _db_lock:
        try:
            if not _conn:
                raise ValueError("Нет активного подключения к базе данных")
            # Префиксный поиск: экранируем спецсимволы FTS5 и оборачиваем слова в кавычки,
            # чтобы кавычки, скобки, двоеточия и операторы (AND/OR/NOT) не вызывали синтаксическую ошибку.
            tokens = re.findall(r'\w+', query or "", flags=re.UNICODE)
            safe_query = ' '.join(f'"{t}"*' for t in tokens if t)
            if not safe_query:
                return []
            cur = _conn.execute(
                "SELECT video_id, description FROM video_descriptions WHERE description MATCH ?",
                (safe_query,)
            )
            results = cur.fetchall()
            log_message(f"Найдено совпадений в БД: {len(results)}")
            return results
        except Exception as e:
            log_message(f"Ошибка при поиске в базе данных: {e}")
            return []


def clear_descriptions_table():
    """Очищает таблицу video_descriptions"""
    global _conn
    with _db_lock:
        try:
            if not _conn:
                raise ValueError("Нет активного подключения к базе данных")
            _conn.execute("DELETE FROM video_descriptions")
            _conn.commit()
            log_message("INFO Таблица video_descriptions очищена перед новым поиском.")
        except Exception as e:
            log_message(f"[ERROR] Не удалось очистить таблицу video_descriptions: {e}")


def is_connected():
    """Проверяет, есть ли активное подключение"""
    return _conn is not None
