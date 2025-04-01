import json
import psycopg2
from logger import log_message

conn = None
cursor = None

def connect_to_database(config_path="config.json"):
    """Подключается к базе данных PostgreSQL"""
    global conn, cursor
    try:
        with open(config_path, "r") as config_file:
            config = json.load(config_file)

        conn = psycopg2.connect(
            dbname=config["dbname"],
            user=config["user"],
            password=config["password"],
            host=config["host"],
            port=config["port"]
        )
        cursor = conn.cursor()
        log_message("✅ Подключение к базе данных успешно.")
    except Exception as e:
        log_message(f"[ERROR] Ошибка при подключении к базе данных: {e}")
        conn = None
        cursor = None

def insert_description(video_id, description):
    """Добавляет описание видео в базу данных"""
    global conn, cursor
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS video_descriptions (
                video_id TEXT PRIMARY KEY,
                description TEXT,
                tsv_description tsvector
            );
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tsv_description
            ON video_descriptions USING GIN (tsv_description);
        """)
        conn.commit()

        cursor.execute("""
            INSERT INTO video_descriptions (video_id, description, tsv_description)
            VALUES (%s, %s, to_tsvector('russian', %s))
            ON CONFLICT (video_id) DO UPDATE
            SET description = EXCLUDED.description,
                tsv_description = to_tsvector('russian', EXCLUDED.description);
        """, (video_id, description, description))
        conn.commit()
    except Exception as e:
        conn.rollback()
        log_message(f"[ERROR] Ошибка при добавлении описания: {e}")

def search_in_database(query):
    """Ищет видео по описаниям в базе данных с учетом словоформ"""
    global cursor
    try:
        cursor.execute("""
            SELECT video_id, description
            FROM video_descriptions
            WHERE tsv_description @@ plainto_tsquery('russian', %s)
        """, (query,))
        results = cursor.fetchall()
        log_message(f"Найдено совпадений в БД: {len(results)}")
        return results
    except Exception as e:
        log_message(f"Ошибка при поиске в базе данных: {e}")
        return []