import json
import os
import psycopg2
from logger import log_message

_conn = None
_cursor = None

def connect_to_database(config_path="config.json"):
    """Подключается к базе данных PostgreSQL"""
    global _conn, _cursor
    try:
        if not os.path.exists(config_path):
            log_message(f"[ERROR] Файл конфигурации {config_path} не найден")
            raise FileNotFoundError(f"Файл конфигурации {config_path} не найден")

        with open(config_path, "r", encoding='utf-8') as config_file:
            config = json.load(config_file)

        log_message(f"INFO: Попытка подключения к базе данных: dbname={config['dbname']}, user={config['user']}, host={config['host']}, port={config['port']}")

        _conn = psycopg2.connect(
            dbname=config["dbname"],
            user=config["user"],
            password=config["password"],
            host=config["host"],
            port=config["port"]
        )
        _cursor = _conn.cursor()
        log_message("✅ Подключение к базе данных успешно.")
    except json.JSONDecodeError as e:
        log_message(f"[ERROR] Ошибка декодирования JSON в файле {config_path}: {e}")
        _conn = None
        _cursor = None
    except psycopg2.Error as e:
        log_message(f"[ERROR] Ошибка подключения к PostgreSQL: {e}")
        _conn = None
        _cursor = None
    except Exception as e:
        log_message(f"[ERROR] Неизвестная ошибка при подключении к базе данных: {e}")
        _conn = None
        _cursor = None

def insert_description(video_id, description):
    """Добавляет описание видео в базу данных"""
    global _conn, _cursor
    try:
        if not _conn or not _cursor:
            raise ValueError("Нет активного подключения к базе данных")
        
        _cursor.execute("""
            CREATE TABLE IF NOT EXISTS video_descriptions (
                video_id TEXT PRIMARY KEY,
                description TEXT,
                tsv_description tsvector
            );
        """)
        _cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tsv_description
            ON video_descriptions USING GIN (tsv_description);
        """)
        _conn.commit()

        _cursor.execute("""
            INSERT INTO video_descriptions (video_id, description, tsv_description)
            VALUES (%s, %s, to_tsvector('russian', %s))
            ON CONFLICT (video_id) DO UPDATE
            SET description = EXCLUDED.description,
                tsv_description = to_tsvector('russian', EXCLUDED.description);
        """, (video_id, description, description))
        _conn.commit()
    except Exception as e:
        _conn.rollback()
        log_message(f"[ERROR] Ошибка при добавлении описания: {e}")

def search_in_database(query):
    """Ищет видео по описаниям в базе данных с учетом словоформ"""
    global _cursor
    try:
        if not _cursor:
            raise ValueError("Нет активного подключения к базе данных")
        
        _cursor.execute("""
            SELECT video_id, description
            FROM video_descriptions
            WHERE tsv_description @@ plainto_tsquery('russian', %s)
        """, (query,))
        results = _cursor.fetchall()
        log_message(f"Найдено совпадений в БД: {len(results)}")
        return results
    except Exception as e:
        log_message(f"Ошибка при поиске в базе данных: {e}")
        return []

def clear_descriptions_table():
    """Очищает таблицу video_descriptions"""
    global _conn, _cursor
    try:
        if not _conn or not _cursor:
            raise ValueError("Нет активного подключения к базе данных")
        
        _cursor.execute("DELETE FROM video_descriptions;")
        _conn.commit()
        log_message("INFO Таблица video_descriptions очищена перед новым поиском.")
    except Exception as e:
        _conn.rollback()
        log_message(f"[ERROR] Не удалось очистить таблицу video_descriptions: {e}")

def is_connected():
    """Проверяет, есть ли активное подключение"""
    global _conn, _cursor
    return _conn is not None and _cursor is not None