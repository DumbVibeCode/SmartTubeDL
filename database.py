import sqlite3
from logger import log_message

_conn = None


def connect_to_database(config_path=None):
    """Инициализирует SQLite FTS5 базу данных в памяти"""
    global _conn
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
    try:
        if not _conn:
            raise ValueError("Нет активного подключения к базе данных")
        # Префиксный поиск: word* ловит все формы слова (написан*, программ*, ...)
        # Это важно для русского языка, где много словоформ без стемминга
        safe_query = ' '.join(f'{w}*' for w in query.split() if w)
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
