import os
from config import format_duration, format_invidious_duration, initialize_settings
from convert import convert_to_mp3, convert_to_mp4
from download_history import add_to_history
from logger import log_message
import yt_dlp
import threading
import time
import traceback
from bs4 import BeautifulSoup
import re
from queues import add_to_queue, clear_queue_file, get_queue_count, get_queue_urls, process_queue, remove_from_queue
from tray import show_notification, tray_icon, update_download_status
from config import initialize_settings, settings, is_downloading
from utils import global_file_size, global_downloaded, download_speed, last_update_time, last_downloaded_bytes, format_speed, update_speed, format_date
from clipboard_utils import update_last_copy_time

invidious_url_var = ""

def download_video(url, from_queue=False):
    global is_downloading, global_file_size, global_downloaded, download_speed, last_update_time, last_downloaded_bytes

    if is_downloading:
        log_message(f"INFO Загрузка уже идет, добавляем URL в очередь: {url}")
        add_to_queue(url)
        return

    is_downloading = True
    log_message(f"DEBUG Путь к cookies.txt: {os.path.abspath('cookies.txt')}")  # Логирование пути к cookies.txt
    log_message(f"DEBUG: Текущая директория: {os.getcwd()}")
    log_message(f"DEBUG: Наличие файла cookies.txt в текущей директории: {os.path.exists('cookies.txt')}")
    log_message(f"DEBUG: Абсолютный путь к cookies.txt: {os.path.abspath('cookies.txt')}")

    def on_download_complete():
        global is_downloading
        is_downloading = False
        queue_count = get_queue_count()
        if queue_count > 0:
            log_message(f"INFO В очереди остались URL ({queue_count}), запускаем обработку")
            threading.Thread(target=process_queue, daemon=True).start()
        else:
            log_message("INFO Очередь пуста после завершения загрузки")
            clear_queue_file()
            update_download_status("Ожидание...", 100)

    if not from_queue:
        if url in get_queue_urls():
            log_message(f"INFO URL уже в очереди: {url}")
            on_download_complete()
            return

    globals()['global_file_size'] = 0
    globals()['global_downloaded'] = 0
    globals()['download_speed'] = "0 KB/s"
    globals()['last_update_time'] = time.time()
    globals()['last_downloaded_bytes'] = 0

    if not url:
        log_message("ERROR Пустой URL для загрузки")
        on_download_complete()
        return

    save_path = settings["download_folder"]

    if "&list=" in url:
        log_message(f"INFO URL содержит параметр плейлиста: {url}. Загружаем только видео.")

    try:
        with yt_dlp.YoutubeDL({"quiet": True, "noplaylist": True, "js_runtimes": {"node": {}}, "remote_components": {"ejs": "github"}}) as ydl:
            info = ydl.extract_info(url, download=False)

            if not info:
                log_message(f"ERROR Не удалось получить информацию о видео: {url}")
                raise Exception("Не удалось извлечь информацию о видео")

            if info.get('is_premiere', False) or info.get('live_status', '') == 'is_upcoming':
                log_message(f"INFO Пропуск премьеры: {url}")
                threading.Thread(target=show_notification, args=(tray_icon, "Премьера", "Это видео еще не вышло (премьера). Загрузка невозможна."), daemon=True).start()
                on_download_complete()
                if from_queue:
                    remove_from_queue(url)
                return

            video_title = info.get("title", "video")
            safe_title = re.sub(r'[\\/*?:"<>|]', "_", video_title)

            if settings["conversion_enabled"]:
                video_ext = settings["download_format"]
            else:
                video_ext = info.get("ext", "mp4")

            file_name = f"{safe_title}.{video_ext}"
            file_path = os.path.join(save_path, file_name)

            log_message(f"INFO Планируется загрузка файла: {file_path}")

    except yt_dlp.utils.DownloadError as e:
        log_message(f"ERROR Видео недоступно: {url}. Ошибка: {e}")
        threading.Thread(target=show_notification, args=(tray_icon, "Ошибка", f"Видео недоступно: {str(e)}"), daemon=True).start()
        if from_queue:
            remove_from_queue(url)
        on_download_complete()
        return

    except Exception as e:
        log_message(f"ERROR Ошибка при проверке видео: {url}. Подробности: {e}")
        log_message(f"DEBUG Трассировка: {traceback.format_exc()}")
        threading.Thread(target=show_notification, args=(tray_icon, "Ошибка", f"Не удалось загрузить видео: {str(e)}"), daemon=True).start()
        if from_queue:
            remove_from_queue(url)
        on_download_complete()
        return

    update_download_status("Загрузка...", 0)

    quality_map = {
        "1080p": "bestvideo[height<=1080]+bestaudio/best",
        "720p": "bestvideo[height<=720]+bestaudio/best",
        "480p": "bestvideo[height<=480]+bestaudio/best"
    }
    selected_quality = quality_map.get(settings["video_quality"], "best")

    # Используем полный путь к файлу cookies.txt
    # cookies_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cookies.txt')
    # cookies_path = r'c:\Down 1\YTD\cookies.txt'
    # cookies_path = os.path.abspath('cookies.txt')  # Или явно: 'C:\\Down 1\\YTD\\cookies.txt'
    cookies_path = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cookies.txt'))
    print("Путь к cookies.txt:", cookies_path)
    # 
    # Используем cookies.txt из текущей директории
    ydl_opts = {
        'outtmpl': os.path.join(save_path, '%(title)s.%(ext)s'),
        'cookies': cookies_path,
        'restrict_filenames': False,
        'windowsfilenames': False,
        'color': 'never',
        'noplaylist': True,
        'verbose': True,  # Включаем подробное логирование
        'debug': True,    # Добавляем режим отладки
        'js_runtimes': {'node': {}},                # JS runtime для YouTube challenge
        'remote_components': {'ejs': 'github'},  # EJS solver для YouTube
    }
    
    
    try:
        with open('cookies.txt', 'r', encoding='utf-8') as f:
            cookies_content = f.read()
            log_message(f"DEBUG: Размер файла cookies.txt: {len(cookies_content)} байт")
            log_message(f"DEBUG: Первые 100 символов cookies.txt: {cookies_content[:100]}")
    except Exception as e:
        log_message(f"ERROR: Не удалось прочитать cookies.txt: {e}")
    
    if settings["download_format"] == "mp3":
        ydl_opts['format'] = 'bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best'
    else:
        ydl_opts['format'] = quality_map.get(settings["video_quality"], "best")

    try:
        threading.Thread(target=show_notification, args=(tray_icon, "YouTube Downloader", "Видео загружается..."), daemon=True).start()
        
        log_message(f"DEBUG ydl_opts: {ydl_opts}")
        ydl_opts["progress_hooks"] = [progress_hook]

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            
            
            
            info = ydl.extract_info(url, download=True)
            downloaded_file = ydl.prepare_filename(info)

            # Сохраняем в историю с длительностью
            video_duration = info.get('duration', 0)
            video_title = info.get('title', 'Неизвестное видео')
            log_message(f"DEBUG Сохранение в историю: {video_title}, длительность: {video_duration} сек")

            add_to_history(
                url=url,
                title=video_title,
                format_type=settings["download_format"],
                duration=video_duration
            )

            log_message(f"SUCCESS Файл загружен: {downloaded_file}")

        if settings["conversion_enabled"]:
            if settings["download_format"] == "mp3" and downloaded_file.endswith((".m4a", ".webm", ".mp4", ".mkv")):
                log_message("INFO Конвертация в MP3...")
                converted_file = convert_to_mp3(downloaded_file, update_download_status)
                if converted_file:
                    log_message(f"SUCCESS Конвертация завершена: {converted_file}")
            elif settings["download_format"] == "mp4" and downloaded_file.endswith((".m4a", ".webm", ".mp4", ".mkv")):
                log_message("INFO Конвертация в MP4...")
                converted_file = convert_to_mp4(downloaded_file, update_download_status)
                if converted_file:
                    log_message(f"SUCCESS Конвертация завершена: {converted_file}")
        else:
            log_message("SUCCESS Файл сохранен в исходном формате")

        if from_queue:
            remove_from_queue(url)

        threading.Thread(target=show_notification, args=(tray_icon, "YouTube Downloader", "Видео загружено успешно!"), daemon=True).start()

        log_message(f"SUCCESS Загрузка завершена: {url}")

    except Exception as e:
        error_message = f"ERROR Ошибка загрузки видео: {url}. Подробности: {e}"
        threading.Thread(target=show_notification, args=(tray_icon, "YouTube Downloader", f"Ошибка: {str(e)}"), daemon=True).start()
        log_message(error_message)
        log_message(f"DEBUG Трассировка: {traceback.format_exc()}")

        if from_queue:
            remove_from_queue(url)
        on_download_complete()
        return

    finally:
        on_download_complete()

def progress_hook(d):
    global global_file_size, global_downloaded, last_update_time, last_downloaded_bytes

    try:
        if d["status"] == "downloading":
            if "total_bytes" in d and d["total_bytes"] is not None:
                globals()['global_file_size'] = d["total_bytes"]
            elif "total_bytes_estimate" in d and d["total_bytes_estimate"] is not None:
                globals()['global_file_size'] = d["total_bytes_estimate"]

            if "downloaded_bytes" in d and d["downloaded_bytes"] is not None:
                globals()['global_downloaded'] = d["downloaded_bytes"]
                update_speed(global_downloaded)

            percent = float(d["_percent_str"].strip().replace("%", ""))
            update_download_status("Загрузка...", int(percent), globals()['global_downloaded'], globals()['global_file_size'])

        elif d["status"] == "finished":
            update_download_status("Готово!", 100, 0, 0)
            globals()['global_file_size'] = 0
            globals()['global_downloaded'] = 0
            globals()['download_speed'] = "0 KB/s"

    except Exception as e:
        log_message(f"Ошибка в progress_hook: {e}")

def download_channel_with_selection(channel_url):
    """Открывает PyQt6-окно выбора видео с канала."""
    log_message(f"INFO Обработка канала: {channel_url}")
    from tray import open_channel_window
    open_channel_window(channel_url)


def download_playlist_with_selection(playlist_url):
    """Открывает PyQt6-окно выбора видео из плейлиста."""
    log_message(f"INFO Обработка плейлиста: {playlist_url}")
    from tray import open_playlist_window
    open_playlist_window(playlist_url)
