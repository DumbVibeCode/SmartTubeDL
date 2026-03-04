import os
import threading
from tkinter import messagebox, ttk
import requests
import aiohttp
import asyncio
import traceback
import tkinter as tk
import yt_dlp

from clipboard_utils import update_last_copy_time
from config import ensure_invidious_running, format_duration, save_settings
from database import connect_to_database, insert_description, search_in_database, is_connected, clear_descriptions_table
from fetch import fetch_description_with_bs, fetch_description_with_ytdlp
from logger import log_message
from queues import add_to_queue, process_queue
from utils import decode_html_entities, update_progress
from config import format_invidious_duration, is_downloading, invidious_url_var, api_key_var

cookies_file = "cookies.txt"

async def fetch_playlist_author(session, invidious_url, playlist_id):
    """Асинхронно запрашивает данные плейлиста для получения автора"""
    playlist_endpoint = f"{invidious_url}/api/v1/playlists/{playlist_id}"
    try:
        async with session.get(playlist_endpoint) as response:
            if response.status == 200:
                data = await response.json()
                author = decode_html_entities(data.get('author', 'Неизвестный канал'))
                log_message(f"DEBUG Получен автор плейлиста {playlist_id}: {author}")
                return playlist_id, author
            else:
                log_message(f"ERROR Ошибка при запросе плейлиста {playlist_id}: {response.status}")
                return playlist_id, "Неизвестный канал"
    except Exception as e:
        log_message(f"ERROR Ошибка при асинхронном запросе плейлиста {playlist_id}: {e}")
        return playlist_id, "Неизвестный канал"

async def fetch_missing_authors(invidious_url, playlist_items):
    """Собирает авторов для плейлистов с пустым author параллельно"""
    async with aiohttp.ClientSession() as session:
        tasks = []
        for item in playlist_items:
            if not item['author']:  # Если author пустой
                tasks.append(fetch_playlist_author(session, invidious_url, item['playlistId']))
        
        if tasks:
            results = await asyncio.gather(*tasks)
            author_map = dict(results)
            for item in playlist_items:
                if not item['author']:
                    item['author'] = author_map.get(item['playlistId'], 'Неизвестный канал')
                    
def search_via_ytdlp(query, max_results, search_type, search_in_descriptions=False, video_descriptions=None, 
                          progress_var=None, root=None, cookies_file="cookies.txt", advanced_search=False, advanced_query=""):
    """Асинхронно выполняет поиск через yt-dlp с фильтрацией по описаниям, кэшированием и куками"""
    if not query:
        log_message("ERROR: Пустой поисковый запрос")
        return None

    if search_type != 'video':
        log_message(f"WARNING: yt-dlp поддерживает только поиск видео, тип {search_type} игнорируется")
        return None

    if video_descriptions is None:
        video_descriptions = {}

    max_results = int(max_results)
    search_query = f"ytsearch{max_results}:{query}"
    
    ydl_opts = {
        'quiet': True,
        'extract_flat': True,
        'playlistend': max_results,
        'format': 'best',
        'js_runtimes': {'node': {}},
        'remote_components': {'ejs': 'github'},
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            result = ydl.extract_info(search_query, download=False)
            entries = result.get('entries', [])
            filtered_items = []
            seen_ids = set()

            video_items = []
            for item in entries:
                video_id = item.get('id')
                if not video_id or video_id in seen_ids:
                    continue
                seen_ids.add(video_id)
                title = decode_html_entities(item.get('title', 'Без названия'))
                channel = decode_html_entities(item.get('uploader', 'Неизвестный канал'))
                duration = item.get('duration', 0)
                video_url = f"https://www.youtube.com/watch?v={video_id}"
                video_items.append((video_id, title, channel, duration, video_url))

            # Обработка поиска по описаниям
            if search_in_descriptions and video_items:
                total_tasks = len(video_items)
                completed_tasks = 0
                descriptions = []
                for video_id, title, channel, duration, video_url in video_items:
                        if video_id in video_descriptions:
                            log_message(f"DEBUG: Описание взято из кэша для {video_id}")
                            description = video_descriptions[video_id]
                        else:
                            description = fetch_description_with_ytdlp(video_url, cookies_file)
                            video_descriptions[video_id] = description
                            log_message(f"DEBUG: Загружено описание для {video_id}")

                        descriptions.append(description)
                        completed_tasks += 1
                        update_progress(completed_tasks, total_tasks, progress_var, root)


                for (video_id, _, _, _, video_url), description in zip(
                    [(vid, t, c, d, url) for vid, t, c, d, url in video_items if vid not in video_descriptions],
                    descriptions
                ):
                    video_descriptions[video_id] = description

                # Фильтрация результатов
                for video_id, title, channel, duration, video_url in video_items:
                    description = video_descriptions.get(video_id, "")
                    # if advanced_search:
                    #     if evaluate_advanced_query(description.lower(), advanced_query.lower()):
                    #         filtered_items.append({
                    #             'type': search_type,
                    #             'videoId': video_id,
                    #             'title': title,
                    #             'author': channel,
                    #             'lengthSeconds': duration,
                    #             'video_url': video_url
                    #         })
                    #         log_message(f"DEBUG: Видео {title} соответствует расширенному запросу '{advanced_query}'")
                    #     else:
                    #         log_message(f"DEBUG: Видео {title} исключено, не соответствует '{advanced_query}'")
                    if search_in_descriptions:
                        if query.lower() in description.lower():
                            filtered_items.append({
                                'type': search_type,
                                'videoId': video_id,
                                'title': title,
                                'author': channel,
                                'description': description,
                                'lengthSeconds': duration,
                                'video_url': video_url
                            })
                        else:
                            log_message(f"DEBUG: Видео {title} исключено, запрос '{query}' не найден в описании")
            else:
                filtered_items = [{
                    'type': search_type,
                    'videoId': video_id,
                    'title': title,
                    'author': channel,
                    'description': 'Описание недоступно (не загружалось)',
                    'lengthSeconds': duration,
                    'video_url': video_url
                } for video_id, title, channel, duration, video_url in video_items]

            # Фоновая загрузка оставшихся описаний
            # async def preload_descriptions():
            #     remaining_tasks = [
            #         fetch_description_with_ytdlp(video_url, cookies_file)
            #         for video_id, _, _, _, video_url in video_items
            #         if video_id not in video_descriptions
            #     ]
            #     if remaining_tasks:
            #         remaining_descriptions = await asyncio.gather(*remaining_tasks)
            #         for (video_id, _, _, _, _), desc in zip(
            #             [(vid, t, c, d, url) for vid, t, c, d, url in video_items if vid not in video_descriptions],
            #             remaining_descriptions
            #         ):
            #             video_descriptions[video_id] = desc
            #         log_message(f"DEBUG: Завершена фоновая загрузка описаний для {len(remaining_descriptions)} видео")

            # if video_items and not (search_in_descriptions or advanced_search):
            #     threading.Thread(target=lambda: asyncio.run(preload_descriptions()), daemon=True).start()

            if progress_var and root:
                root.after(0, lambda: progress_var.set(100))

            log_message(f"INFO: Найдено через yt-dlp: {len(filtered_items)} результатов после фильтрации")
            return {'items': filtered_items, 'channel_stats': {}}

    except Exception as e:
        log_message(f"ERROR: Ошибка при поиске через yt-dlp: {e}")
        log_message(f"Трассировка: {traceback.format_exc()}")
        return None

def evaluate_advanced_query(description, advanced_query):
    """Простая логика оценки расширенного запроса"""
    # Пример: поддержка AND, OR, NOT через пробелы
    # "python AND tutorial NOT beginner" -> должен содержать "python" и "tutorial", но не "beginner"
    terms = advanced_query.split()
    result = True
    i = 0
    while i < len(terms):
        term = terms[i]
        if term.upper() == "AND":
            i += 1
            continue
        elif term.upper() == "OR":
            i += 1
            result = result or (terms[i] in description)
        elif term.upper() == "NOT":
            i += 1
            result = result and (terms[i] not in description)
        i += 1
    return result
          
        
def search_via_invidious(query, invidious_url, max_results, search_type, sort_by, search_in_descriptions):
    """Выполняет поиск видео через Invidious API"""
    if not query:
        return None  # status_var не нужен тут, обработка в perform_search

    if not invidious_url:
        return None  # То же самое

    if invidious_url.endswith('/'):
        invidious_url = invidious_url[:-1]

    max_results = int(max_results)  # Уже строка преобразована в int
    sort_map = {
        "relevance": "relevance",
        "date": "date",
        "rating": "rating",
        "viewCount": "views",
        "title": "alphabetical"
    }
    invidious_sort = sort_map.get(sort_by, "relevance")

    type_map = {
        "video": "video",
        "channel": "channel",
        "playlist": "playlist"
    }
    invidious_type = type_map.get(search_type, "video")

    try:
        if "localhost" in invidious_url or "127.0.0.1" in invidious_url:
            ensure_invidious_running()

        api_endpoint = f"{invidious_url}/api/v1/search"
        params = {
            'type': invidious_type,
            'sort_by': invidious_sort,
            'page': 1,
            'q': query
        }

        all_results = []
        while len(all_results) < max_results:
            response = requests.get(api_endpoint, params=params)
            if response.status_code != 200:
                log_message(f"ERROR Ошибка Invidious API: {response.status_code}")
                break

            page_results = response.json()
            if not page_results:
                break

            all_results.extend(page_results)
            params['page'] += 1
            if len(all_results) >= max_results:
                break

        results = all_results[:max_results]
        log_message(f"INFO Всего найдено результатов: {len(results)}")

        filtered_items = []
        seen_ids = set()

        for item in results:
            try:
                if search_type == 'video':
                    if item.get('type') != 'video':
                        continue
                    video_id = item.get('videoId')
                    if video_id in seen_ids:
                        continue
                    seen_ids.add(video_id)
                    title = decode_html_entities(item.get('title', 'Без названия'))
                    channel = decode_html_entities(item.get('author', 'Неизвестный канал'))
                    duration = format_invidious_duration(item.get('lengthSeconds', 0))
                    video_url = f"https://www.youtube.com/watch?v={video_id}"
                    
                    if search_in_descriptions:
                        description = fetch_description_with_bs(video_url)
                        if query.lower() not in description.lower():
                            continue

                elif search_type == 'channel':
                    if item.get('type') != 'channel':
                        continue
                    channel_id = item.get('authorId')
                    if channel_id in seen_ids:
                        continue
                    seen_ids.add(channel_id)
                    title = decode_html_entities(item.get('author', 'Без названия'))
                    channel = title
                    video_url = f"https://www.youtube.com/channel/{channel_id}"

                elif search_type == 'playlist':
                    if item.get('type') != 'playlist':
                        continue
                    playlist_id = item.get('playlistId')
                    if playlist_id in seen_ids:
                        continue
                    seen_ids.add(playlist_id)
                    title = decode_html_entities(item.get('title', 'Без названия'))
                    channel = decode_html_entities(item.get('author', 'Неизвестный канал'))
                    video_count = item.get('videoCount', 'N/A')
                    video_url = f"https://www.youtube.com/playlist?list={playlist_id}"

                    filtered_items.append({
                        'type': search_type,
                        'playlistId': playlist_id,
                        'title': title,
                        'author': channel,
                        'description': decode_html_entities(item.get('description', 'Описание отсутствует')),
                        'video_count': video_count,
                        'video_url': video_url
                    })
                    continue

                filtered_items.append({
                    'type': search_type,
                    'videoId': video_id if search_type == 'video' else None,
                    'channelId': channel_id if search_type == 'channel' else None,
                    'playlistId': playlist_id if search_type == 'playlist' else None,
                    'title': title,
                    'author': channel,
                    'description': decode_html_entities(item.get('description', 'Описание отсутствует')),
                    'lengthSeconds': item.get('lengthSeconds', 0) if search_type == 'video' else None,
                    'video_url': video_url
                })

            except Exception as e:
                log_message(f"ERROR Ошибка при обработке результата Invidious API: {e}")

        if search_type == 'playlist':
            asyncio.run(fetch_missing_authors(invidious_url, [item for item in filtered_items if item['type'] == 'playlist']))

        log_message(f"INFO После фильтрации осталось: {len(filtered_items)}")

        # Если есть плейлисты, проверяем и заполняем авторов асинхронно
        if search_type == 'playlist':
            log_message("DEBUG Запуск асинхронного запроса авторов плейлистов")
            asyncio.run(fetch_missing_authors(invidious_url, [item for item in filtered_items if item['type'] == 'playlist']))
            log_message("DEBUG Асинхронные запросы завершены")

        log_message(f"INFO После фильтрации осталось: {len(filtered_items)}")
        # status_var.set(f"Найдено результатов: {len(filtered_items)}")

        return {'items': filtered_items, 'channel_stats': {}}

    except Exception as e:
        log_message(f"ERROR Ошибка при поиске через Invidious API: {e}")
        log_message(f"Трассировка: {traceback.format_exc()}")
        # status_var.set(f"Ошибка: {str(e)}")
        return None

def search_via_youtube_api(query, api_key, search_type, order, max_results, search_in_descriptions):
    """Выполняет поиск видео через официальный YouTube API с пагинацией"""
    if not query:
        return None

    if not api_key:
        return None

    log_message(f"INFO Поиск по запросу: {query}")
    max_results = int(max_results)

    try:
        base_url = "https://www.googleapis.com/youtube/v3/search"
        params = {
            'key': api_key,
            'part': 'snippet',
            'maxResults': min(50, max_results),
            'type': search_type,
            'order': order,
            'q': query
        }

        all_items = []
        next_page_token = None

        while len(all_items) < max_results:
            if next_page_token:
                params['pageToken'] = next_page_token

            response = requests.get(base_url, params=params)
            if response.status_code != 200:
                log_message(f"ERROR Ошибка API: {response.status_code} - {response.text}")
                break

            data = response.json()
            items = data.get('items', [])
            all_items.extend(items)

            if len(all_items) >= max_results or not data.get('nextPageToken'):
                break

            next_page_token = data.get('nextPageToken')
            log_message(f"DEBUG Получен токен следующей страницы: {next_page_token}")

        results = all_items[:max_results]
        log_message(f"INFO Всего найдено результатов: {len(results)}")

        filtered_items = []
        channel_stats = {}
        video_durations = {}

        # Получаем длительность для видео
        if search_type == 'video':
            video_ids = [item['id']['videoId'] for item in results if 'id' in item and 'videoId' in item['id']]
            if video_ids:
                videos_url = "https://www.googleapis.com/youtube/v3/videos"
                videos_params = {
                    'key': api_key,
                    'part': 'contentDetails',
                    'id': ','.join(video_ids)
                }
                response = requests.get(videos_url, params=videos_params)
                if response.status_code == 200:
                    video_data = response.json()
                    for video in video_data.get('items', []):
                        duration_iso = video.get('contentDetails', {}).get('duration', '')
                        if duration_iso:
                            # Конвертируем ISO 8601 в читаемый формат
                            from config import format_duration
                            video_durations[video['id']] = format_duration(duration_iso)

        if search_type == 'channel':
            channel_ids = [item['id']['channelId'] for item in results]
            channels_url = "https://www.googleapis.com/youtube/v3/channels"
            channels_params = {
                'key': api_key,
                'part': 'statistics',
                'id': ','.join(channel_ids)
            }
            response = requests.get(channels_url, params=channels_params)
            if response.status_code == 200:
                channel_data = response.json()
                for channel in channel_data.get('items', []):
                    channel_stats[channel['id']] = channel['statistics'].get('videoCount', 'N/A')

        elif search_type == 'playlist':
            playlist_ids = [item['id']['playlistId'] for item in results]
            playlists_url = "https://www.googleapis.com/youtube/v3/playlists"
            playlists_params = {
                'key': api_key,
                'part': 'contentDetails',
                'id': ','.join(playlist_ids)
            }
            response = requests.get(playlists_url, params=playlists_params)
            if response.status_code == 200:
                playlist_data = response.json()
                for playlist in playlist_data.get('items', []):
                    channel_stats[playlist['id']] = playlist['contentDetails'].get('itemCount', 'N/A')

        for item in results:
            if search_type == 'video':
                video_id = item['id']['videoId']
                video_url = f"https://www.youtube.com/watch?v={video_id}"
                title = decode_html_entities(item['snippet']['title'])
                channel = decode_html_entities(item['snippet']['channelTitle'])
                description = decode_html_entities(item['snippet'].get('description', ''))

                # Отладочное логирование
                log_message(f"DEBUG Description length for '{title[:50]}': {len(description)}")

                if search_in_descriptions:
                    if query.lower() not in description.lower():
                        continue

                # Добавляем длительность
                duration = video_durations.get(video_id, '')

                filtered_items.append({
                    'id': {'videoId': video_id},
                    'snippet': {'title': title, 'channelTitle': channel, 'description': description},
                    'video_url': video_url,
                    'duration': duration
                })
            elif search_type == 'channel':
                video_id = item['id']['channelId']
                video_url = f"https://www.youtube.com/channel/{video_id}"
                title = decode_html_entities(item['snippet']['title'])
                channel = title
                filtered_items.append({
                    'id': {'channelId': video_id},
                    'snippet': {'title': title, 'channelTitle': channel},
                    'video_url': video_url
                })
            elif search_type == 'playlist':
                video_id = item['id']['playlistId']
                video_url = f"https://www.youtube.com/playlist?list={video_id}"
                title = decode_html_entities(item['snippet']['title'])
                channel = decode_html_entities(item['snippet']['channelTitle'])
                filtered_items.append({
                    'id': {'playlistId': video_id},
                    'snippet': {'title': title, 'channelTitle': channel},
                    'video_url': video_url,
                    'video_count': channel_stats.get(video_id, 'N/A')
                })

        return {'items': filtered_items, 'channel_stats': channel_stats}

    except Exception as e:
        log_message(f"ERROR Ошибка при выполнении поиска через YouTube API: {e}")
        return None
    
def perform_search(search_var, type_var, order_var, max_results_var, api_key_var, invidious_url_var, 
                   use_alternative_api_var, use_ytdlp_search_var, search_in_descriptions_var, 
                   advanced_search_var, advanced_query_var, tree, video_urls, status_var, 
                   video_descriptions, settings, progress_var=None, root=None):
    """Выполняет поиск видео через выбранный API"""
    log_message("DEBUG: Начало выполнения perform_search")
    search_type = type_var.get().strip()
    
    if not isinstance(tree, ttk.Treeview):
        log_message(f"ERROR: tree не является Treeview, получен тип {type(tree)}")
        messagebox.showerror("Ошибка", "Внутренняя ошибка: неверный объект таблицы")
        return

    for item in tree.get_children():
        tree.delete(item)
    video_urls.clear()            

    try:
        # Сохраняем текущий поисковый запрос
        status_var.set(f"Поиск...")
        if progress_var:
            progress_var.set(0)  # Сбрасываем прогресс перед началом
        # search_window.update()
        settings["last_search_query"] = search_var.get().strip()
        # save_settings(settings)
        
        

        # Определяем, какой метод использовать
        use_alternative = use_alternative_api_var.get()
        use_ytdlp = use_ytdlp_search_var.get()
        search_in_descriptions = search_in_descriptions_var.get()
        advanced_search = advanced_search_var.get()
        query = search_var.get().strip()
        advanced_query = advanced_query_var.get().strip() if advanced_search else ""

        if search_type == 'channel' or search_type == 'playlist':
            tree.heading("duration", text="Количество видео")
        else:
            tree.heading("duration", text="Длительность")

        # Подключаемся к базе данных только для расширенного поиска
        if advanced_search:
            advanced_query = advanced_query_var.get().strip()

            if not is_connected():
                log_message("DEBUG: Подключение к базе данных отсутствует, вызываем connect_to_database")
                connect_to_database()
                if not is_connected():
                    log_message("ERROR: Не удалось установить подключение к базе данных после попытки")
                    status_var.set("Ошибка подключения к базе данных")
                    return

            if not advanced_query:
                status_var.set("Введите запрос для поиска по описаниям")
                log_message(f"ERROR Введите запрос для поиска по описаниям")
                return

            log_message(f"INFO Выполняется расширенный поиск по описаниям: {advanced_query}")
            
            # Сначала делаем обычный поиск через API
            if use_ytdlp:
                log_message("INFO: Выбран поиск через yt-dlp")
                if search_type != 'video':
                    status_var.set("yt-dlp поддерживает только поиск видео")
                    return
                results = search_via_ytdlp(query, max_results_var.get(), search_type, 
                                                    search_in_descriptions, video_descriptions, 
                                                    progress_var, root, cookies_file="cookies.txt",
                                                    advanced_search=advanced_search, advanced_query=advanced_query) or {'items': [], 'channel_stats': {}}
                log_message(f"DEBUG: Получено {len(results['items'])} результатов через yt-dlp")
                log_message("INFO Загрузка описаний в базу данных (yt-dlp)")
                clear_descriptions_table()  # Очищаем таблицу
                completed_tasks = 0
                total_tasks = len(results['items'])
                progress_var.set(0)
                for item in results.get('items', []):
                    video_id = item.get('videoId')
                    if not video_id:
                        log_message("DEBUG: Пропущен элемент без videoId")
                        continue
                    title = decode_html_entities(item.get('title', 'Без названия'))
                    channel = decode_html_entities(item.get('author', 'Неизвестный канал'))
                    duration = format_invidious_duration(item.get('lengthSeconds', 0))
                    video_url = item.get('video_url')
                    # item_id = tree.insert('', tk.END, values=(title, channel, duration))
                    # video_urls[item_id] = video_url
                    description = fetch_description_with_ytdlp(video_url)
                    insert_description(video_id, description)
                    completed_tasks += 1
                    update_progress(completed_tasks, total_tasks, progress_var, root)
                    log_message(f"DEBUG: Добавлено в базу (yt-dlp): {title} ({video_url})")
            
            elif use_alternative:
                log_message("INFO Выбран поиск через Invidious API")
                results = search_via_invidious(search_var.get(), invidious_url_var.get(), max_results_var.get(), 
                                          search_type, order_var.get(), search_in_descriptions_var.get()) or {'items': [], 'channel_stats': {}}

                # Загружаем описания в базу данных
                log_message("INFO Загрузка описаний в базу данных (Invidious API)")
                clear_descriptions_table()  # Очищаем таблицу
                log_message("DEBUG: Таблица описаний очищена")
                completed_tasks = 0
                total_tasks = len(results['items'])
                progress_var.set(0)
                for item in results['items']:
                    video_id = item.get('videoId')
                    if video_id:  # Проверяем, что video_id существует
                        video_url = f"https://www.youtube.com/watch?v={video_id}"
                        # log_message(f"DEBUG: Загрузка описания для видео {video_id}")
                        description = fetch_description_with_bs(video_url)
                        log_message(f"DEBUG: Получено описание длиной {len(description)} символов")
                        insert_description(video_id, description)
                        completed_tasks += 1
                        update_progress(completed_tasks, total_tasks, progress_var, root)
                        log_message(f"DEBUG: Описание сохранено в базу данных для видео {video_id}")

            else:
                log_message("INFO Выбран поиск через официальный YouTube API")
                results = search_via_youtube_api(search_var.get(), api_key_var.get(), search_type, 
                                            order_var.get(), max_results_var.get(), search_in_descriptions_var.get()) or {'items': [], 'channel_stats': {}}

                # Загружаем описания в базу данных
                log_message("INFO Загрузка описаний в базу данных (YouTube API)")
                clear_descriptions_table()  # Очищаем таблицу
                log_message("DEBUG: Таблица описаний очищена")
                completed_tasks = 0
                total_tasks = len(results['items'])
                progress_var.set(0)
                # Сначала получаем все описания через API
                video_ids = [item['id']['videoId'] for item in results.get('items', [])]
                api_key = api_key_var.get().strip()
                videos_url = "https://www.googleapis.com/youtube/v3/videos"
                videos_params = {
                    'key': api_key,
                    'part': 'snippet',
                    'id': ','.join(video_ids)
                }
                response = requests.get(videos_url, params=videos_params)
                
                if response.status_code == 200:
                    video_data = response.json()
                    for video in video_data.get('items', []):
                        video_id = video['id']
                        description = video['snippet']['description']
                        video_descriptions[video_id] = description
                        # log_message(f"DEBUG: Загрузка описания для видео {video_id}")
                        log_message(f"DEBUG: Получено описание длиной {len(description)} символов")
                        insert_description(video_id, description)
                        completed_tasks += 1
                        update_progress(completed_tasks, total_tasks, progress_var, root)
                        log_message(f"DEBUG: Описание сохранено в базу данных для видео {video_id}")
                else:
                    log_message(f"ERROR: Ошибка при загрузке описаний через YouTube API: {response.status_code}")

            # Теперь ищем по базе данных
            db_results = search_in_database(advanced_query)
            log_message(f"INFO Найдено совпадений в базе: {len(db_results)}")

            if not db_results:
                status_var.set("По запросу ничего не найдено")
                return

            # Получаем список video_id из результатов поиска
            video_ids = [video_id for video_id, _ in db_results]

            # Получаем длительности всех видео одним запросом
            try:
                videos_url = "https://www.googleapis.com/youtube/v3/videos"
                videos_params = {
                    'key': api_key_var.get().strip(),
                    'part': 'contentDetails',
                    'id': ','.join(video_ids)
                }
                response = requests.get(videos_url, params=videos_params)
                video_durations = {}
                if response.status_code == 200:
                    video_data = response.json()
                    for video in video_data.get('items', []):
                        try:
                            video_id = video.get('id')
                            duration_iso = video.get('contentDetails', {}).get('duration')
                            if duration_iso:
                                video_durations[video_id] = format_duration(duration_iso)
                        except Exception as e:
                            log_message(f"DEBUG: ошибка форматирования длительности для {video.get('id')}: {e}")
                            continue

                # Fallback: если для каких-то видео не удалось получить длительность через API,
                # пробуем подтянуть их через yt-dlp (медленнее, но часто надёжнее).
                try:
                    missing_ids = [vid for vid in video_ids if vid not in video_durations]
                    if missing_ids:
                        try:
                            import yt_dlp as _ytdlp
                            ydl_opts = {'quiet': True, 'js_runtimes': {'node': {}}, 'remote_components': {'ejs': 'github'}}
                            with _ytdlp.YoutubeDL(ydl_opts) as ydl:
                                for vid in missing_ids:
                                    try:
                                        info = ydl.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False)
                                        dur_sec = info.get('duration')
                                        if dur_sec is not None:
                                            video_durations[vid] = format_invidious_duration(dur_sec)
                                            log_message(f"DEBUG: длительность через yt-dlp для {vid}: {video_durations[vid]}")
                                    except Exception as e:
                                        log_message(f"DEBUG: yt-dlp fallback failed for {vid}: {e}")
                        except Exception as e:
                            log_message(f"DEBUG: yt-dlp не доступен для fallback длительностей: {e}")
                except Exception:
                    # Любые ошибки во fallback не должны ломать основной поиск
                    pass
            except Exception as e:
                log_message(f"Ошибка при получении длительностей видео: {e}")
                video_durations = {}

            # Отображаем результаты в интерфейсе
            for video_id, description in db_results:
                # Ищем видео в результатах API
                video = None

                if use_alternative:
                    for item in results['items']:
                        if item.get("videoId") == video_id:
                            video = item
                            break
                elif use_ytdlp:
                    for item in results['items']:
                        log_message(f"DEBUG: Item structure: {item}")
                        if item.get("videoId") == video_id:
                            video = item
                            break
                else:
                    for item in results.get('items', []):
                        if item['id']['videoId'] == video_id:
                            video = item
                            break

                if video:
                    if use_alternative:
                        title = decode_html_entities(video.get("title", "Без названия"))
                        channel = decode_html_entities(video.get("author", "Неизвестный канал"))
                        duration = format_invidious_duration(video.get("lengthSeconds", 0))
                    elif use_ytdlp:
                        title = decode_html_entities(video.get("title", "Без названия"))
                        channel = decode_html_entities(video.get("author", "Неизвестный канал"))
                        duration = format_invidious_duration(video.get("lengthSeconds", 0))
                    else:
                        title = decode_html_entities(video['snippet']['title'])
                        channel = decode_html_entities(video['snippet']['channelTitle'])
                        duration = video_durations.get(video_id, 'N/A')

                    video_url = f"https://www.youtube.com/watch?v={video_id}"
                    item_id = tree.insert('', tk.END, values=(title, channel, duration))
                    video_urls[item_id] = video_url

            status_var.set(f"Найдено совпадений: {len(db_results)}")
            return

        # Если это не расширенный поиск, просто делаем обычный поиск
        if not advanced_search:
            if use_ytdlp:
                log_message("INFO: Выбран поиск через yt-dlp")
                if search_type != 'video':
                    status_var.set("yt-dlp поддерживает только поиск видео")
                    return
                results = search_via_ytdlp(query, max_results_var.get(), search_type, 
                                               search_in_descriptions, video_descriptions, 
                                               progress_var, root, cookies_file="cookies.txt") or {'items': [], 'channel_stats': {}}
                log_message(f"DEBUG: Получено {len(results['items'])} результатов через yt-dlp")

                for item in results.get('items', []):
                    if search_type == 'video':
                        video_id = item.get('videoId')
                        if not video_id:
                            log_message("DEBUG: Пропущен элемент без videoId")
                            continue
                        title = decode_html_entities(item.get('title', 'Без названия'))
                        channel = decode_html_entities(item.get('author', 'Неизвестный канал'))
                        duration = format_invidious_duration(item.get('lengthSeconds', 0))
                        video_url = item.get('video_url')
                        item_id = tree.insert('', tk.END, values=(title, channel, duration))
                        video_urls[item_id] = video_url
                        log_message(f"DEBUG: Добавлено в таблицу (yt-dlp): {title} ({video_url})")
                    else:
                        log_message(f"WARNING: Поиск каналов или плейлистов через yt-dlp пока не поддерживается")
                        status_var.set("Поиск каналов и плейлистов через yt-dlp не поддерживается")
                        return
                    
            elif use_alternative:
                log_message("INFO Выбран поиск через Invidious API")
                results = search_via_invidious(search_var.get(), invidious_url_var.get(), max_results_var.get(), 
                                          search_type, order_var.get(), search_in_descriptions_var.get()) or {'items': [], 'channel_stats': {}}
                log_message(f"DEBUG: Получено {len(results.get('items', []))} результатов, начинаем обработку...")

                for item in tree.get_children():
                    tree.delete(item)
                video_urls.clear()

                for item in results.get('items', []):
                    if search_type == 'video':
                        video_id = item.get('videoId')
                        title = decode_html_entities(item.get('title', 'Без названия'))
                        channel = decode_html_entities(item.get('author', 'Неизвестный канал'))
                        duration = format_invidious_duration(item.get('lengthSeconds', 0))
                        video_url = item.get('video_url')
                    elif search_type == 'channel':
                        video_id = item.get('channelId')
                        title = decode_html_entities(item.get('title', 'Без названия'))  # Название канала
                        channel = title  # Дублируем в "Канал"
                        duration = 'Канал'  # Без количества видео
                        video_url = item.get('video_url')
                    elif search_type == 'playlist':
                        video_id = item.get('playlistId')
                        title = decode_html_entities(item.get('title', 'Без названия'))
                        channel = decode_html_entities(item.get('author', 'Неизвестный канал'))
                        duration = str(item.get('video_count', 'N/A'))  # Количество видео из Invidious
                        video_url = item.get('video_url')

                    item_id = tree.insert('', tk.END, values=(title, channel, duration))
                    video_urls[item_id] = video_url

                status_var.set(f"Найдено результатов: {len(results.get('items', []))}")

            else:
                log_message("INFO Выбран поиск через официальный YouTube API")
                results = search_via_youtube_api(search_var.get(), api_key_var.get(), search_type, 
                                            order_var.get(), max_results_var.get(), search_in_descriptions_var.get()) or {'items': [], 'channel_stats': {}}

                for item in tree.get_children():
                    tree.delete(item)
                video_urls.clear()

                # Обработка результатов в зависимости от типа поиска
                if search_type == 'video':
                    video_ids = [item['id']['videoId'] for item in results.get('items', [])]
                    try:
                        videos_url = "https://www.googleapis.com/youtube/v3/videos"
                        videos_params = {
                            'key': api_key_var.get().strip(),
                            'part': 'contentDetails',
                            'id': ','.join(video_ids)
                        }
                        response = requests.get(videos_url, params=videos_params)
                        video_durations = {}
                        if response.status_code == 200:
                            video_data = response.json()
                            for video in video_data.get('items', []):
                                    try:
                                        video_id = video['id']
                                        duration_iso = video.get('contentDetails', {}).get('duration')
                                        if duration_iso:
                                            video_durations[video_id] = format_duration(duration_iso)
                                    except Exception as e:
                                        log_message(f"DEBUG: ошибка форматирования длительности для {video.get('id')}: {e}")
                                        continue

                        # Fallback: если какие-то видео остались без длительности, пробуем yt-dlp
                        missing_ids = [vid for vid in video_ids if vid not in video_durations]
                        if missing_ids:
                            try:
                                import yt_dlp as _ytdlp
                                ydl_opts = {'quiet': True, 'js_runtimes': {'node': {}}, 'remote_components': {'ejs': 'github'}}
                                with _ytdlp.YoutubeDL(ydl_opts) as ydl:
                                    for vid in missing_ids:
                                        try:
                                            info = ydl.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False)
                                            dur_sec = info.get('duration')
                                            if dur_sec is not None:
                                                video_durations[vid] = format_invidious_duration(dur_sec)
                                                log_message(f"DEBUG: длительность через yt-dlp для {vid}: {video_durations[vid]}")
                                        except Exception as e:
                                            log_message(f"DEBUG: yt-dlp fallback failed for {vid}: {e}")
                            except Exception as e:
                                log_message(f"DEBUG: yt-dlp не доступен для fallback длительностей: {e}")
                    except Exception as e:
                        log_message(f"ERROR Ошибка при получении длительностей видео: {e}")
                        video_durations = {}





                for item in results.get('items', []):
                    if search_type == 'video':
                        video_id = item['id']['videoId']
                        title = decode_html_entities(item['snippet']['title'])
                        channel = decode_html_entities(item['snippet']['channelTitle'])
                        duration = video_durations.get(video_id, 'N/A')
                        video_url = f"https://www.youtube.com/watch?v={video_id}"
                    elif search_type == 'channel':
                        video_id = item['id']['channelId']
                        title = decode_html_entities(item['snippet']['title'])
                        channel = decode_html_entities(item['snippet']['channelTitle'])
                        duration = results['channel_stats'].get(video_id, 'N/A')
                        video_url = f"https://www.youtube.com/channel/{video_id}"
                    elif search_type == 'playlist':
                        video_id = item['id']['playlistId']
                        title = decode_html_entities(item['snippet']['title'])
                        channel = decode_html_entities(item['snippet']['channelTitle'])  # Исправлено с item.get('author')
                        duration = str(item.get('video_count', 'N/A'))  # Используем video_count из filtered_items
                        log_message(f"DEBUG: {duration}")
                        video_url = f"https://www.youtube.com/playlist?list={video_id}"

                    item_id = tree.insert('', tk.END, values=(title, channel, duration))
                    video_urls[item_id] = video_url

                if results:
                    status_var.set(f"Найдено результатов: {len(results.get('items', []))}")
                else:
                    status_var.set("Результаты не найдены")

    except Exception as e:
        log_message(f"ERROR Ошибка в perform_search: {e}")
        log_message(f"Трассировка: {traceback.format_exc()}")
        messagebox.showerror("Ошибка", f"Произошла ошибка: {e}")
