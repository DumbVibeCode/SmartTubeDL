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
    """Загружает описание видео через yt-dlp. Для YouTube берёт живые куки
    Firefox (как при скачивании) — иначе бот-проверка не даёт получить описание."""
    ydl_opts = {
        'quiet': True,
        'skip_download': True,
        'noplaylist': True,
        'js_runtimes': {'node': {}},
    }

    low = (video_url or "").lower()
    if "youtube.com" in low or "youtu.be" in low:
        ydl_opts['cookiesfrombrowser'] = ('firefox',)
    elif os.path.exists(cookies_file):
        ydl_opts['cookiefile'] = cookies_file

    def sync_fetch(opts):
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
            description = info.get('description', '') or ""
            return decode_html_entities(description) if description else ""

    try:
        return sync_fetch(ydl_opts)
    except Exception as e:
        # Повтор без куки Firefox (профиль мог быть занят/недоступен)
        if 'cookiesfrombrowser' in ydl_opts:
            opts2 = dict(ydl_opts)
            opts2.pop('cookiesfrombrowser', None)
            if os.path.exists(cookies_file):
                opts2['cookiefile'] = cookies_file
            try:
                return sync_fetch(opts2)
            except Exception:
                pass
        log_message(f"ERROR: Ошибка загрузки описания через yt-dlp для {video_url}: {e}")
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

_YT_ID_RE = re.compile(r'(?:v=|/embed/|/shorts/|youtu\.be/)([A-Za-z0-9_-]{11})')


def _youtube_id(url):
    m = _YT_ID_RE.search(url or "")
    return m.group(1) if m else None


def fetch_description_innertube(video_url):
    """Быстро получает описание YouTube через InnerTube API (~0.7 c).

    Это тот же эндпоинт youtubei/v1/player, которым пользуется сам сайт и yt-dlp.
    Бот-защита на него заметно мягче, чем на обычный GET страницы (тот часто
    отдаёт 429), а описание лежит в videoDetails.shortDescription и доступно
    даже когда playabilityStatus = UNPLAYABLE."""
    vid = _youtube_id(video_url)
    if not vid:
        return ""   # не YouTube — пусть решает вызывающий (BS/yt-dlp)
    # Ключ не нужен: при валидном клиентском контексте эндпоинт отвечает и без него
    api = "https://www.youtube.com/youtubei/v1/player?prettyPrint=false"
    body = {
        "context": {"client": {
            "clientName": "WEB", "clientVersion": "2.20240401.00.00",
            "hl": "en", "gl": "US",
        }},
        "videoId": vid,
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Content-Type": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        r = requests.post(api, json=body, headers=headers, timeout=8)
        if r.status_code != 200:
            return ""
        desc = (r.json().get("videoDetails", {}) or {}).get("shortDescription", "") or ""
        return decode_html_entities(desc) if desc else ""
    except Exception as e:
        log_message(f"WARNING InnerTube описание: {e}")
        return ""


def fetch_description_youtube_api(video_url, api_key=None):
    """Описание через официальный YouTube Data API (~0.8 c, надёжно).
    Ключ берётся из настроек, если не передан. Для видео, которые InnerTube
    не отдаёт без логина, это самый быстрый рабочий путь."""
    vid = _youtube_id(video_url)
    if not vid:
        return ""
    if api_key is None:
        try:
            from config import settings
            api_key = settings.get("youtube_api_key", "") or ""
        except Exception:
            api_key = ""
    if not api_key:
        return ""
    try:
        r = requests.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={"key": api_key, "part": "snippet", "id": vid},
            timeout=10,
        )
        if r.status_code == 200:
            items = r.json().get("items", [])
            if items:
                return items[0].get("snippet", {}).get("description", "") or ""
        else:
            log_message(f"DEBUG YouTube API описание: HTTP {r.status_code}")
    except Exception as e:
        log_message(f"WARNING YouTube API описание: {e}")
    return ""


def fetch_description(video_url):
    """Единый путь получения описания в порядке скорость/надёжность:
    официальный Data API (если есть ключ) → InnerTube → разбор HTML → yt-dlp."""
    d = fetch_description_youtube_api(video_url)
    if d:
        return d
    d = fetch_description_with_bs(video_url)   # InnerTube + HTML
    if d:
        return d
    return fetch_description_with_ytdlp(video_url) or ""


def fetch_description_with_bs(video_url):
    """Быстрый путь получения описания YouTube: сначала InnerTube API (~0.7 c),
    затем разбор HTML как запасной вариант. yt-dlp остаётся фолбэком у вызывающего."""
    # 1) InnerTube — быстро и надёжно
    desc = fetch_description_innertube(video_url)
    if desc:
        return desc

    # 2) Запасной разбор HTML страницы
    try:
        # Отправляем GET-запрос к странице видео
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
            "Cookie": "CONSENT=YES+1",   # минуем страницу согласия (иначе не-200)
        }
        sep = "&" if "?" in video_url else "?"
        response = requests.get(video_url + sep + "hl=en&gl=US", headers=headers, timeout=10)

        if response.status_code != 200:
            log_message(f"Ошибка при запросе страницы видео: {response.status_code}")
            return ""   # пусто → вызывающий не кеширует и попробует снова

        # Прямое и устойчивое извлечение shortDescription из сырого HTML
        m = re.search(r'"shortDescription":"((?:\\.|[^"\\])*)"', response.text)
        if m:
            try:
                d = json.loads('"' + m.group(1) + '"')
                if d:
                    return d
            except Exception:
                pass

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
        return ""

    except Exception as e:
        log_message(f"Ошибка при парсинге описания: {e}")
        return ""