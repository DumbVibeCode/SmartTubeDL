import json
import os
import re
import asyncio
import requests
import yt_dlp
from bs4 import BeautifulSoup

from utils import decode_html_entities
from config import format_duration, format_invidious_duration, api_key_var
from logger import log_message

def fetch_description_with_ytdlp(video_url, cookies_file="cookies.txt"):
    """Асинхронно загружает описание видео через yt-dlp с поддержкой куки"""
    ydl_opts = {
        'quiet': True,
        'skip_download': True,
        'format': 'best',
    }
    
    if os.path.exists(cookies_file):
        ydl_opts['cookiefile'] = cookies_file
        # log_message(f"DEBUG: Используется файл куки для описания: {cookies_file}")
    
    def sync_fetch(url):
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            description = info.get('description', '')
            return decode_html_entities(description) if description else ""

    try:
        description = sync_fetch (video_url)
        return description
    
    except Exception as e:
        log_message(f"ERROR: Ошибка при асинхронной загрузке описания через yt-dlp для {video_url}: {e}")
        return ""


def fetch_videos_from_youtube_api(video_ids, api_key):
    """Получает полные данные о видео через YouTube API"""
    try:
        if not video_ids:
            return {'items': []}

        # Разбиваем на группы по 50 видео (максимальный размер запроса)
        video_groups = [video_ids[i:i + 50] for i in range(0, len(video_ids), 50)]
        all_videos = []

        for group in video_groups:
            video_ids_str = ','.join(group)
            url = f"https://www.googleapis.com/youtube/v3/videos?part=snippet,contentDetails&id={video_ids_str}&key={api_key}"
            
            response = requests.get(url)
            if response.status_code == 200:
                data = response.json()
                all_videos.extend(data.get('items', []))
            else:
                log_message(f"ERROR Ошибка при запросе к YouTube API: {response.status_code}")
                return {'items': []}

        return {'items': all_videos}
    except Exception as e:
        log_message(f"ERROR Ошибка при получении данных через YouTube API: {e}")
        return {'items': []}

def fetch_videos_from_invidious(video_ids, invidious_url_var):
    """Получает данные о видео через Invidious API"""
    log_message("DEBUG: Вызвана функция fetch_videos_from_invidious")
    invidious_url = invidious_url_var.get().strip()
    log_message(f"DEBUG: Используемый Invidious URL: {invidious_url}")
    if not invidious_url:
        log_message("ERROR: URL Invidious сервера отсутствует")
        return []

    try:
        api_endpoint = f"{invidious_url}/api/v1/videos"
        results = []
        for video_id in video_ids:
            log_message(f"DEBUG: Выполняется запрос к Invidious API для video_id: {video_id}")
            response = requests.get(f"{api_endpoint}/{video_id}")
            log_message(f"DEBUG: Ответ Invidious API для video_id {video_id}: {response.status_code} - {response.text[:500]}")
            if response.status_code == 200:
                data = response.json()
                log_message(f"DEBUG: Обрабатывается видео: {video_id}")
                results.append({
                    "videoId": video_id,
                    "title": data.get('title', 'Без названия'),
                    "channel": data.get('author', 'Неизвестный канал'),
                    "duration": format_invidious_duration(data.get('lengthSeconds', 0))
                })
            else:
                log_message(f"ERROR: Ошибка Invidious API для видео {video_id}: {response.status_code}")
        return results
    except Exception as e:
        log_message(f"ERROR Ошибка при получении данных через Invidious API: {e}")
        return []

def fetch_description_with_bs(video_url):
    """Получает полное описание видео с YouTube с помощью BeautifulSoup"""
    try:
        # Отправляем GET-запрос к странице видео
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        response = requests.get(video_url, headers=headers, timeout=10)
        
        if response.status_code != 200:
            log_message(f"Ошибка при запросе страницы видео: {response.status_code}")
            return "Описание недоступно (ошибка загрузки страницы)"
        
        # Парсим HTML-страницу
        soup = BeautifulSoup(response.text, "html.parser")
        
        # Ищем скрипт с ytInitialPlayerResponse
        scripts = soup.find_all("script")
        for script in scripts:
            if "ytInitialPlayerResponse" in script.text:
                try:
                    # Извлекаем JSON-данные с помощью регулярного выражения
                    match = re.search(r"ytInitialPlayerResponse\s*=\s*({.*?});", script.text, re.DOTALL)
                    if not match:
                        log_message("ytInitialPlayerResponse не найден в скрипте")
                        continue
                    
                    json_data = match.group(1)
                    data = json.loads(json_data)
                    
                    # Извлекаем описание из поля "shortDescription"
                    description = data.get("videoDetails", {}).get("shortDescription", "").strip()
                    if description:
                        return description
                except Exception as e:
                    log_message(f"Ошибка при извлечении shortDescription из JSON: {e}")
        
        # Попытка 2: Найти описание в теге <meta name="description">
        description_meta = soup.find("meta", {"name": "description"})
        if description_meta and "content" in description_meta.attrs:
            description = description_meta["content"].strip()
            if description:
                return description
        
        # Если описание не найдено
        return "Описание недоступно (не найдено на странице)"
    
    except Exception as e:
        log_message(f"Ошибка при парсинге описания: {e}")
        return "Описание недоступно (ошибка парсинга)"