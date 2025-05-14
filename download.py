import tkinter as tk
import os
from config import format_duration, format_invidious_duration, initialize_settings
from convert import convert_to_mp3, convert_to_mp4
from download_history import add_to_history
from logger import log_message
from tkinter import ttk, messagebox
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
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from description import show_description
from ui import configure_entry
from concurrent.futures import ThreadPoolExecutor, as_completed  # Для параллельной загрузки
from clipboard_utils import update_last_copy_time  # Добавляем импорт

invidious_url_var = ""

def download_video(url, from_queue=False):
    global is_downloading, global_file_size, global_downloaded, download_speed, last_update_time, last_downloaded_bytes

    if is_downloading:
        log_message(f"INFO Загрузка уже идет, добавляем URL в очередь: {url}")
        add_to_queue(url)
        return

    is_downloading = True

    def on_download_complete():
        global is_downloading
        is_downloading = False
        queue_count = get_queue_count()
        if queue_count > 0:
            log_message(f"INFO В очереди остались URL ({queue_count}), запускаем обработку")
            threading.Thread(target=process_queue).start()
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
        with yt_dlp.YoutubeDL({"quiet": True, "noplaylist": True}) as ydl:
            info = ydl.extract_info(url, download=False)

            if not info:
                log_message(f"ERROR Не удалось получить информацию о видео: {url}")
                raise Exception("Не удалось извлечь информацию о видео")

            if info.get('is_premiere', False) or info.get('live_status', '') == 'is_upcoming':
                log_message(f"INFO Пропуск премьеры: {url}")
                messagebox.showinfo("Премьера", "Это видео еще не вышло (премьера). Загрузка невозможна.")
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
        messagebox.showerror("Ошибка", f"Видео недоступно: {str(e)}")
        if from_queue:
            remove_from_queue(url)
        on_download_complete()
        return

    except Exception as e:
        log_message(f"ERROR Ошибка при проверке видео: {url}. Подробности: {e}")
        log_message(f"DEBUG Трассировка: {traceback.format_exc()}")
        messagebox.showerror("Ошибка", f"Не удалось загрузить видео: {str(e)}")
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

    ydl_opts = {
        'outtmpl': os.path.join(save_path, '%(title)s.%(ext)s'),
        'cookies': 'cookies.txt',
        'restrict_filenames': False,
        'windowsfilenames': False,
        'no_color': True,
        'noplaylist': True,
    }

    if settings["download_format"] == "mp3":
        ydl_opts['format'] = 'bestaudio[ext=m4a]/best[ext=mp3]'
    else:
        ydl_opts['format'] = quality_map.get(settings["video_quality"], "best")

    try:
        threading.Thread(target=show_notification, args=(tray_icon, "YouTube Downloader", "Видео загружается...")).start()

        ydl_opts["progress_hooks"] = [progress_hook]

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            downloaded_file = ydl.prepare_filename(info)

            add_to_history(
                url=url,
                title=info.get('title', 'Неизвестное видео'),
                format_type=settings["download_format"]
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

        threading.Thread(target=show_notification, args=(tray_icon, "YouTube Downloader", "Видео загружено успешно!")).start()

        log_message(f"SUCCESS Загрузка завершена: {url}")

    except Exception as e:
        error_message = f"ERROR Ошибка загрузки видео: {url}. Подробности: {e}"
        threading.Thread(target=show_notification, args=(tray_icon, "YouTube Downloader", f"Ошибка: {str(e)}")).start()
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

import tkinter as tk
import os
from config import format_duration, format_invidious_duration, initialize_settings
from convert import convert_to_mp3, convert_to_mp4
from download_history import add_to_history
from logger import log_message
from tkinter import ttk, messagebox
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
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from description import show_description
from ui import configure_entry
from concurrent.futures import ThreadPoolExecutor, as_completed  # Для параллельной загрузки

invidious_url_var = ""

# ... (функции download_video и progress_hook без изменений, опущены для краткости) ...

def download_channel_with_selection(channel_url):
    from clipboard import clear_clipboard
    log_message(f"INFO Обработка канала: {channel_url}")
    
    try:
        from tray import root
        if root is None or not hasattr(root, 'winfo_exists') or not root.winfo_exists():
            log_message("DEBUG Создание нового корневого окна в download_channel_with_selection")
            root = tk.Tk()
            root.withdraw()
            import tray
            tray.root = root

        window = tk.Toplevel(root)
        window.title("Загрузка видео с канала")
        window.geometry("1000x700")
        window.resizable(True, True)
        
        window.update_idletasks()
        screen_width = window.winfo_screenwidth()
        screen_height = window.winfo_screenheight()
        x = (screen_width - 1000) // 2
        y = (screen_height - 700) // 2
        window.geometry(f"1000x700+{x}+{y}")
        
        window.deiconify()
        window.lift()
        window.focus_force()
        if os.name == 'nt':
            window.attributes('-topmost', True)
            window.update()
            window.attributes('-topmost', False)
        
        main_frame = ttk.Frame(window, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        progress_label = ttk.Label(main_frame, text="Загрузка списка видео: 0%", font=("Arial", 10))
        progress_label.pack(pady=(10, 5))
        progress = ttk.Progressbar(main_frame, length=350, mode="determinate", maximum=100)
        progress.pack(pady=(0, 10))
        
        cancel_button = ttk.Button(main_frame, text="Остановить", command=lambda: stop_loading())
        cancel_button.pack(pady=(0, 10))
        
        cancelled = [False]
        
        def stop_loading():
            cancelled[0] = True
            if progress_label.winfo_exists():
                progress_label.config(text="Загрузка остановлена")
            if progress.winfo_exists():
                progress.stop()
                progress['value'] = 0
            if cancel_button.winfo_exists():
                cancel_button.destroy()
            if status_label.winfo_exists():
                status_var.set(f"Загрузка остановлена. Загружено: {loaded_videos} видео")
            log_message("INFO Загрузка списка видео остановлена пользователем")
        
        def check_cancelled():
            if not window.winfo_exists():
                cancelled[0] = True
                return True
            return cancelled[0]
        
        window.update()
        
        results_frame = ttk.LabelFrame(main_frame, text="Видео", padding=10)
        results_frame.pack(fill=tk.BOTH, expand=True)
        
        container = ttk.Frame(results_frame)
        container.pack(fill=tk.BOTH, expand=True)
        
        scrollbar = ttk.Scrollbar(container)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        columns = ("title", "duration", "date")
        tree = ttk.Treeview(container, columns=columns, show="headings", yscrollcommand=scrollbar.set)
        
        def sort_column(tree, col, reverse):
            data = [(tree.set(item, col), item) for item in tree.get_children('')]
            if col == "duration":
                def parse_duration(dur):
                    try:
                        parts = dur.split(':')
                        if len(parts) == 3:
                            h, m, s = map(int, parts)
                            return h * 3600 + m * 60 + s
                        elif len(parts) == 2:
                            m, s = map(int, parts)
                            return m * 60 + s
                        else:
                            return 0
                    except (ValueError, TypeError):
                        return 0
                data.sort(key=lambda x: parse_duration(x[0]), reverse=reverse)
            else:
                data.sort(key=lambda x: x[0].lower(), reverse=reverse)
            for index, (val, item) in enumerate(data):
                tree.move(item, '', index)
            tree.heading(col, command=lambda: sort_column(tree, col, not reverse))
        
        tree.heading("title", text="Название", command=lambda: sort_column(tree, "title", False))
        tree.heading("duration", text="Длительность", command=lambda: sort_column(tree, "duration", False))
        
        tree.column("title", width=500, anchor=tk.W)
        tree.column("duration", width=100, anchor=tk.CENTER)
        tree.column("date", width=150, anchor=tk.CENTER)
        
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=tree.yview)
        
        search_frame = ttk.Frame(main_frame)
        search_frame.pack(fill=tk.X, pady=5)
        search_var = tk.StringVar()
        search_entry = configure_entry(
            parent=search_frame,
            textvariable=search_var,
            label_text="Поиск по названию:",
            width=50,
            focus=True,
            entry_type="entry"
        )
        search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))
        
        valid_entries = []
        video_urls = {}
        video_descriptions = {}
        initial_durations = {}
        original_items = {}
        loaded_videos = 0
        
        ydl_opts = {
            'quiet': True,
            'extract_flat': True,
            'skip_download': True,
            'ignoreerrors': True,
            'no_color': True,
        }
        
        def load_channel_info():
            nonlocal loaded_videos
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(channel_url, download=False)
                    if not info:
                        log_message("ERROR Не удалось получить информацию о канале")
                        return
                    
                    total_videos = info.get('playlist_count', 0) or len(info.get('entries', []))
                    loaded_videos = 0
                    
                    channel_title = info.get('title', 'Канал YouTube')
                    if window.winfo_exists():
                        window.title(f"Видео с канала: {channel_title}")
                        ttk.Label(main_frame, text=f"Канал: {channel_title}", font=("Arial", 12, "bold")).pack(pady=5)
                        total_label = ttk.Label(main_frame, text=f"Всего видео на канале: {total_videos}")
                        total_label.pack(pady=5)
                    
                    entries = info.get('entries', [])
                    for entry in entries:
                        if check_cancelled():
                            break
                        if entry is None:
                            continue
                        try:
                            if (entry.get('title') != '[Private video]' and
                                not entry.get('is_premiere', False) and
                                entry.get('live_status', '') != 'is_upcoming'):
                                video_id = entry.get('id')
                                if video_id:
                                    valid_entries.append(entry)
                                    video_urls[video_id] = f"https://www.youtube.com/watch?v={video_id}"
                                    video_descriptions[video_id] = "Описание будет загружено при запросе"
                                    duration = entry.get('duration', None)
                                    initial_durations[video_id] = format_invidious_duration(duration) if duration else "Загрузка..."
                                    title = entry.get('title', 'Без названия')
                                    date = "-"
                                    item_id = tree.insert('', tk.END, values=(title, initial_durations[video_id], date))
                                    original_items[item_id] = (title, initial_durations[video_id], date)
                                    video_urls[item_id] = f"https://www.youtube.com/watch?v={video_id}"
                                    video_descriptions[video_id] = "Описание будет загружено при запросе"
                                    
                                    loaded_videos += 1
                                    if total_videos > 0 and not cancelled[0]:
                                        progress_value = (loaded_videos / total_videos) * 100
                                        if progress_label.winfo_exists():
                                            progress_label.config(text=f"Загрузка списка видео: {int(progress_value)}%")
                                        if progress.winfo_exists():
                                            progress['value'] = progress_value
                                        if window.winfo_exists():
                                            window.update()
                                    elif not cancelled[0]:
                                        if progress_label.winfo_exists():
                                            progress_label.config(text=f"Загрузка списка видео: {loaded_videos} видео")
                                        if window.winfo_exists():
                                            window.update()
                        except Exception as e:
                            log_message(f"ERROR Ошибка при обработке видео: {e}")
                            continue
                    
                    if not cancelled[0]:
                        if total_videos > loaded_videos:
                            if total_label.winfo_exists():
                                total_label.config(text=f"Всего доступных видео: {loaded_videos} (Пропущено {total_videos - loaded_videos} премьер, приватных или недоступных видео)")
                        
                        if progress_label.winfo_exists():
                            progress_label.destroy()
                        if progress.winfo_exists():
                            progress.destroy()
                        if cancel_button.winfo_exists():
                            cancel_button.destroy()
                        if status_label.winfo_exists():
                            status_var.set(f"Отображается: {len(original_items)} видео")
                        
                        log_message(f"INFO Найдено {len(valid_entries)} доступных видео из {loaded_videos} на канале")
            
            except Exception as e:
                log_message(f"ERROR Ошибка при загрузке информации о канале: {e}")
                if not cancelled[0]:
                    try:
                        if progress_label.winfo_exists():
                            progress_label.config(text="Ошибка загрузки")
                        messagebox.showerror("Ошибка", "Не удалось получить информацию о канале. Проверьте URL или соединение.")
                        window.destroy()
                    except tk.TclError:
                        log_message("DEBUG Окно уже закрыто")
        
        channel_info_thread = threading.Thread(target=load_channel_info)
        channel_info_thread.daemon = True
        channel_info_thread.start()
        
        def fetch_metadata(video_id):
            ydl_opts_full = {
                'quiet': True,
                'extract_flat': False,
                'skip_download': True,
                'ignoreerrors': True,
                'no_color': True,
            }
            try:
                with yt_dlp.YoutubeDL(ydl_opts_full) as ydl:
                    info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
                    if info:
                        title = info.get('title', 'Без названия')
                        duration = format_invidious_duration(info.get('duration', 0))
                        return video_id, (title, duration, "-")
            except Exception as e:
                log_message(f"DEBUG Ошибка при загрузке метаданных видео {video_id}: {e}")
            return video_id, None
        
        def load_full_metadata():
            to_fetch = [
                entry.get('id') for entry in valid_entries
                if initial_durations.get(entry.get('id')) == "Загрузка..."
            ]
            if not to_fetch:
                log_message("INFO Все метаданные уже загружены на этапе extract_flat=True")
                return
            
            with ThreadPoolExecutor(max_workers=5) as executor:
                future_to_video = {executor.submit(fetch_metadata, video_id): video_id for video_id in to_fetch}
                for future in as_completed(future_to_video):
                    if cancelled[0]:
                        log_message("INFO Загрузка метаданных остановлена")
                        return
                    video_id = future_to_video[future]
                    try:
                        result = future.result()
                        if result:
                            _, (title, duration, date) = result
                            for item_id in original_items:
                                if video_urls[item_id].endswith(video_id):
                                    original_items[item_id] = (title, duration, date)
                                    tree.item(item_id, values=(title, duration, date))
                                    if window.winfo_exists():
                                        window.update()
                                    break
                    except Exception as e:
                        log_message(f"DEBUG Ошибка в потоке для видео {video_id}: {e}")
        
        metadata_thread = threading.Thread(target=load_full_metadata)
        metadata_thread.daemon = True
        metadata_thread.start()
        
        def filter_videos(*args):
            search_text = search_var.get().lower()
            tree.delete(*tree.get_children())
            visible_count = 0
            for item_id, (title, duration, date) in original_items.items():
                if search_text in title.lower():
                    tree.insert('', tk.END, item_id, values=(title, duration, date))
                    visible_count += 1
            if not cancelled[0]:
                status_var.set(f"Отображается: {visible_count} из {len(original_items)} видео")
        
        search_var.trace("w", filter_videos)
        
        context_menu = tk.Menu(tree, tearoff=0)
        def copy_url():
            selected = tree.selection()[0] if tree.selection() else None
            if selected and selected in video_urls:
                url = video_urls[selected]
                window.clipboard_clear()
                window.clipboard_append(url)
                update_last_copy_time()  # Добавляем вызов, чтобы сбросить таймер
                status_var.set("URL скопирован в буфер обмена")
                log_message(f"INFO Ссылка скопирована: {url}")
        
        def add_to_download_queue():
            selected_items = tree.selection()
            if not selected_items:
                status_var.set("Ничего не выбрано для добавления в очередь")
                return
            added_count = 0
            for selected in selected_items:
                if selected in video_urls:
                    url = video_urls[selected]
                    if add_to_queue(url):
                        added_count += 1
            status_var.set(f"Добавлено в очередь загрузки: {added_count} видео")
            log_message(f"INFO Добавлено {added_count} видео в очередь загрузки")
            if not is_downloading:
                threading.Thread(target=process_queue).start()
        
        def open_in_browser():
            selected = tree.selection()[0] if tree.selection() else None
            if selected and selected in video_urls:
                import webbrowser
                url = video_urls[selected]
                webbrowser.open(url)
                status_var.set("Открыто в браузере")
        
        context_menu.add_command(label="Копировать URL", command=copy_url)
        context_menu.add_command(label="Добавить в очередь загрузки", command=add_to_download_queue)
        context_menu.add_command(label="Открыть в браузере", command=open_in_browser)
        context_menu.add_command(label="Показать описание", command=lambda: show_description(
            tree, video_urls, window, status_var, status_label, video_descriptions))
        
        tree.bind("<Button-3>", lambda event: context_menu.post(event.x_root, event.y_root))
        
        def on_double_click(event):
            item = tree.selection()[0] if tree.selection() else None
            if item and item in video_urls:
                url = video_urls[item]
                add_to_queue(url)
                status_var.set(f"Видео добавлено в очередь: {url}")
                log_message(f"INFO Видео добавлено в очередь загрузки: {url}")
                if not is_downloading:
                    threading.Thread(target=process_queue).start()
        
        tree.bind("<Double-1>", on_double_click)
        
        status_var = tk.StringVar(value="Всего видео: 0")
        status_label = ttk.Label(results_frame, textvariable=status_var, foreground="blue", font=("Arial", 12))
        status_label.pack(anchor=tk.W, pady=(5, 0))
        
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=10)
        ttk.Button(button_frame, text="Загрузить выбранные", command=add_to_download_queue).pack(side=tk.RIGHT, padx=5)
        ttk.Button(button_frame, text="Выделить все", command=lambda: tree.selection_set(list(original_items.keys()))).pack(side=tk.RIGHT, padx=5)
        ttk.Button(button_frame, text="Убрать выделение", command=lambda: tree.selection_remove(tree.get_children())).pack(side=tk.RIGHT, padx=5)
        ttk.Button(button_frame, text="Остановить", command=lambda: stop_loading()).pack(side=tk.RIGHT, padx=5)
        
        def on_closing():
            try:
                cancelled[0] = True
                window.destroy()
                log_message("INFO Окно канала закрыто")
            except tk.TclError:
                log_message("DEBUG Окно канала уже уничтожено")
        
        window.protocol("WM_DELETE_WINDOW", on_closing)
        
        try:
            window.update()
            log_message("INFO Окно канала успешно открыто")
        except tk.TclError:
            log_message("ERROR Не удалось обновить окно канала, приложение завершено")
            return
    
    except Exception as e:
        log_message(f"ERROR Критическая ошибка при обработке канала: {e}")
        log_message(f"DEBUG Трассировка: {traceback.format_exc()}")
        try:
            messagebox.showerror("Ошибка", f"Произошла ошибка при обработке канала: {str(e)}")
        except tk.TclError:
            log_message("DEBUG Не удалось показать messagebox, приложение завершено")


def download_playlist_with_selection(playlist_url):
    from clipboard import clear_clipboard
    log_message(f"INFO Обработка плейлиста: {playlist_url}")
    
    try:
        from tray import root
        if root is None or not hasattr(root, 'winfo_exists') or not root.winfo_exists():
            log_message("DEBUG Создание нового корневого окна в download_playlist_with_selection")
            root = tk.Tk()
            root.withdraw()
            import tray
            tray.root = root

        window = tk.Toplevel(root)
        window.title("Загрузка видео из плейлиста")
        window.geometry("1000x700")
        window.resizable(True, True)
        
        window.update_idletasks()
        screen_width = window.winfo_screenwidth()
        screen_height = window.winfo_screenheight()
        x = (screen_width - 1000) // 2
        y = (screen_height - 700) // 2
        window.geometry(f"1000x700+{x}+{y}")
        
        window.deiconify()
        window.lift()
        window.focus_force()
        if os.name == 'nt':
            window.attributes('-topmost', True)
            window.update()
            window.attributes('-topmost', False)
        
        main_frame = ttk.Frame(window, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        progress_label = ttk.Label(main_frame, text="Загрузка списка видео: 0%", font=("Arial", 10))
        progress_label.pack(pady=(10, 5))
        progress = ttk.Progressbar(main_frame, length=350, mode="determinate", maximum=100)
        progress.pack(pady=(0, 10))
        
        cancel_button = ttk.Button(main_frame, text="Остановить", command=lambda: stop_loading())
        cancel_button.pack(pady=(0, 10))
        
        cancelled = [False]
        
        def stop_loading():
            cancelled[0] = True
            if progress_label.winfo_exists():
                progress_label.config(text="Загрузка остановлена")
            if progress.winfo_exists():
                progress.stop()
                progress['value'] = 0
            if cancel_button.winfo_exists():
                cancel_button.destroy()
            if status_label.winfo_exists():
                status_var.set(f"Загрузка остановлена. Загружено: {loaded_videos} видео")
            log_message("INFO Загрузка списка видео из плейлиста остановлена пользователем")
        
        def check_cancelled():
            if not window.winfo_exists():
                cancelled[0] = True
                return True
            return cancelled[0]
        
        window.update()
        
        results_frame = ttk.LabelFrame(main_frame, text="Видео", padding=10)
        results_frame.pack(fill=tk.BOTH, expand=True)
        
        container = ttk.Frame(results_frame)
        container.pack(fill=tk.BOTH, expand=True)
        
        scrollbar = ttk.Scrollbar(container)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        columns = ("title", "duration", "date")
        tree = ttk.Treeview(container, columns=columns, show="headings", yscrollcommand=scrollbar.set)
        
        def sort_column(tree, col, reverse):
            data = [(tree.set(item, col), item) for item in tree.get_children('')]
            if col == "duration":
                def parse_duration(dur):
                    try:
                        parts = dur.split(':')
                        if len(parts) == 3:
                            h, m, s = map(int, parts)
                            return h * 3600 + m * 60 + s
                        elif len(parts) == 2:
                            m, s = map(int, parts)
                            return m * 60 + s
                        else:
                            return 0
                    except (ValueError, TypeError):
                        return 0
                data.sort(key=lambda x: parse_duration(x[0]), reverse=reverse)
            else:
                data.sort(key=lambda x: x[0].lower(), reverse=reverse)
            for index, (val, item) in enumerate(data):
                tree.move(item, '', index)
            tree.heading(col, command=lambda: sort_column(tree, col, not reverse))
        
        tree.heading("title", text="Название", command=lambda: sort_column(tree, "title", False))
        tree.heading("duration", text="Длительность", command=lambda: sort_column(tree, "duration", False))
        
        tree.column("title", width=500, anchor=tk.W)
        tree.column("duration", width=100, anchor=tk.CENTER)
        tree.column("date", width=150, anchor=tk.CENTER)
        
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=tree.yview)
        
        search_frame = ttk.Frame(main_frame)
        search_frame.pack(fill=tk.X, pady=5)
        search_var = tk.StringVar()
        search_entry = configure_entry(
            parent=search_frame,
            textvariable=search_var,
            label_text="Поиск по названию:",
            width=50,
            focus=True,
            entry_type="entry"
        )
        search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))
        
        valid_entries = []
        video_urls = {}
        video_descriptions = {}
        initial_durations = {}
        original_items = {}
        loaded_videos = 0
        
        ydl_opts = {
            'quiet': True,
            'extract_flat': True,
            'skip_download': True,
            'ignoreerrors': True,
            'no_color': True,
        }
        
        def load_playlist_info():
            nonlocal loaded_videos
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(playlist_url, download=False)
                    if not info:
                        log_message("ERROR Не удалось получить информацию о плейлисте")
                        return
                    
                    total_videos = info.get('playlist_count', 0) or len(info.get('entries', []))
                    loaded_videos = 0
                    
                    playlist_title = info.get('title', 'Плейлист YouTube')
                    if window.winfo_exists():
                        window.title(f"Видео из плейлиста: {playlist_title}")
                        ttk.Label(main_frame, text=f"Плейлист: {playlist_title}", font=("Arial", 12, "bold")).pack(pady=5)
                        total_label = ttk.Label(main_frame, text=f"Всего видео в плейлисте: {total_videos}")
                        total_label.pack(pady=5)
                    
                    entries = info.get('entries', [])
                    for entry in entries:
                        if check_cancelled():
                            break
                        if entry is None:
                            continue
                        try:
                            if (entry.get('title') != '[Private video]' and
                                not entry.get('is_premiere', False) and
                                entry.get('live_status', '') != 'is_upcoming'):
                                video_id = entry.get('id')
                                if video_id:
                                    valid_entries.append(entry)
                                    video_urls[video_id] = f"https://www.youtube.com/watch?v={video_id}"
                                    video_descriptions[video_id] = "Описание будет загружено при запросе"
                                    duration = entry.get('duration', None)
                                    initial_durations[video_id] = format_invidious_duration(duration) if duration else "Загрузка..."
                                    title = entry.get('title', 'Без названия')
                                    date = entry.get('upload_date', '-')[:8] if entry.get('upload_date') else "-"
                                    item_id = tree.insert('', tk.END, values=(title, initial_durations[video_id], date))
                                    original_items[item_id] = (title, initial_durations[video_id], date)
                                    video_urls[item_id] = f"https://www.youtube.com/watch?v={video_id}"
                                    video_descriptions[video_id] = "Описание будет загружено при запросе"
                                    
                                    loaded_videos += 1
                                    if total_videos > 0 and not cancelled[0]:
                                        progress_value = (loaded_videos / total_videos) * 100
                                        if progress_label.winfo_exists():
                                            progress_label.config(text=f"Загрузка списка видео: {int(progress_value)}%")
                                        if progress.winfo_exists():
                                            progress['value'] = progress_value
                                        if window.winfo_exists():
                                            window.update()
                                    elif not cancelled[0]:
                                        if progress_label.winfo_exists():
                                            progress_label.config(text=f"Загрузка списка видео: {loaded_videos} видео")
                                        if window.winfo_exists():
                                            window.update()
                        except Exception as e:
                            log_message(f"ERROR Ошибка при обработке видео: {e}")
                            continue
                    
                    if not cancelled[0]:
                        if total_videos > loaded_videos:
                            if total_label.winfo_exists():
                                total_label.config(text=f"Всего доступных видео: {loaded_videos} (Пропущено {total_videos - loaded_videos} премьер, приватных или недоступных видео)")
                        
                        if progress_label.winfo_exists():
                            progress_label.destroy()
                        if progress.winfo_exists():
                            progress.destroy()
                        if cancel_button.winfo_exists():
                            cancel_button.destroy()
                        if status_label.winfo_exists():
                            status_var.set(f"Отображается: {len(original_items)} видео")
                        
                        log_message(f"INFO Найдено {len(valid_entries)} доступных видео из {loaded_videos} в плейлисте")
            
            except Exception as e:
                log_message(f"ERROR Ошибка при загрузке информации о плейлисте: {e}")
                if not cancelled[0]:
                    try:
                        if progress_label.winfo_exists():
                            progress_label.config(text="Ошибка загрузки")
                        messagebox.showerror("Ошибка", "Не удалось получить информацию о плейлисте. Проверьте URL или соединение.")
                        window.destroy()
                    except tk.TclError:
                        log_message("DEBUG Окно уже закрыто")
        
        playlist_info_thread = threading.Thread(target=load_playlist_info)
        playlist_info_thread.daemon = True
        playlist_info_thread.start()
        
        def fetch_metadata(video_id):
            ydl_opts_full = {
                'quiet': True,
                'extract_flat': False,
                'skip_download': True,
                'ignoreerrors': True,
                'no_color': True,
            }
            try:
                with yt_dlp.YoutubeDL(ydl_opts_full) as ydl:
                    info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
                    if info:
                        title = info.get('title', 'Без названия')
                        duration = format_invidious_duration(info.get('duration', 0))
                        date = info.get('upload_date', '-')[:8] if info.get('upload_date') else "-"
                        return video_id, (title, duration, date)
            except Exception as e:
                log_message(f"DEBUG Ошибка при загрузке метаданных видео {video_id}: {e}")
            return video_id, None
        
        def load_full_metadata():
            to_fetch = [
                entry.get('id') for entry in valid_entries
                if initial_durations.get(entry.get('id')) == "Загрузка..."
            ]
            if not to_fetch:
                log_message("INFO Все метаданные уже загружены на этапе extract_flat=True")
                return
            
            with ThreadPoolExecutor(max_workers=5) as executor:
                future_to_video = {executor.submit(fetch_metadata, video_id): video_id for video_id in to_fetch}
                for future in as_completed(future_to_video):
                    if cancelled[0]:
                        log_message("INFO Загрузка метаданных остановлена")
                        return
                    video_id = future_to_video[future]
                    try:
                        result = future.result()
                        if result:
                            _, (title, duration, date) = result
                            for item_id in original_items:
                                if video_urls[item_id].endswith(video_id):
                                    original_items[item_id] = (title, duration, date)
                                    tree.item(item_id, values=(title, duration, date))
                                    if window.winfo_exists():
                                        window.update()
                                    break
                    except Exception as e:
                        log_message(f"DEBUG Ошибка в потоке для видео {video_id}: {e}")
        
        metadata_thread = threading.Thread(target=load_full_metadata)
        metadata_thread.daemon = True
        metadata_thread.start()
        
        def filter_videos(*args):
            search_text = search_var.get().lower()
            tree.delete(*tree.get_children())
            visible_count = 0
            for item_id, (title, duration, date) in original_items.items():
                if search_text in title.lower():
                    tree.insert('', tk.END, item_id, values=(title, duration, date))
                    visible_count += 1
            if not cancelled[0]:
                status_var.set(f"Отображается: {visible_count} из {len(original_items)} видео")
        
        search_var.trace("w", filter_videos)
        
        context_menu = tk.Menu(tree, tearoff=0)
        def copy_url():
            selected = tree.selection()[0] if tree.selection() else None
            if selected and selected in video_urls:
                url = video_urls[selected]
                window.clipboard_clear()
                window.clipboard_append(url)
                update_last_copy_time()  # Сбрасываем таймер для clipboard_monitor
                status_var.set("URL скопирован в буфер обмена")
                log_message(f"INFO Ссылка скопирована: {url}")
        
        def add_to_download_queue():
            selected_items = tree.selection()
            if not selected_items:
                status_var.set("Ничего не выбрано для добавления в очередь")
                return
            added_count = 0
            for selected in selected_items:
                if selected in video_urls:
                    url = video_urls[selected]
                    if add_to_queue(url):
                        added_count += 1
            status_var.set(f"Добавлено в очередь загрузки: {added_count} видео")
            log_message(f"INFO Добавлено {added_count} видео в очередь загрузки")
            if not is_downloading:
                threading.Thread(target=process_queue).start()
        
        def open_in_browser():
            selected = tree.selection()[0] if tree.selection() else None
            if selected and selected in video_urls:
                import webbrowser
                url = video_urls[selected]
                webbrowser.open(url)
                status_var.set("Открыто в браузере")
        
        context_menu.add_command(label="Копировать URL", command=copy_url)
        context_menu.add_command(label="Добавить в очередь загрузки", command=add_to_download_queue)
        context_menu.add_command(label="Открыть в браузере", command=open_in_browser)
        context_menu.add_command(label="Показать описание", command=lambda: show_description(
            tree, video_urls, window, status_var, status_label, video_descriptions))
        
        tree.bind("<Button-3>", lambda event: context_menu.post(event.x_root, event.y_root))
        
        def on_double_click(event):
            item = tree.selection()[0] if tree.selection() else None
            if item and item in video_urls:
                url = video_urls[item]
                add_to_queue(url)
                status_var.set(f"Видео добавлено в очередь: {url}")
                log_message(f"INFO Видео добавлено в очередь загрузки: {url}")
                if not is_downloading:
                    threading.Thread(target=process_queue).start()
        
        tree.bind("<Double-1>", on_double_click)
        
        status_var = tk.StringVar(value="Всего видео: 0")
        status_label = ttk.Label(results_frame, textvariable=status_var, foreground="blue", font=("Arial", 12))
        status_label.pack(anchor=tk.W, pady=(5, 0))
        
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=10)
        ttk.Button(button_frame, text="Загрузить выбранные", command=add_to_download_queue).pack(side=tk.RIGHT, padx=5)
        ttk.Button(button_frame, text="Выделить все", command=lambda: tree.selection_set(list(original_items.keys()))).pack(side=tk.RIGHT, padx=5)
        ttk.Button(button_frame, text="Убрать выделение", command=lambda: tree.selection_remove(tree.get_children())).pack(side=tk.RIGHT, padx=5)
        ttk.Button(button_frame, text="Остановить", command=lambda: stop_loading()).pack(side=tk.RIGHT, padx=5)
        
        def on_closing():
            try:
                cancelled[0] = True
                window.destroy()
                log_message("INFO Окно плейлиста закрыто")
            except tk.TclError:
                log_message("DEBUG Окно плейлиста уже уничтожено")
        
        window.protocol("WM_DELETE_WINDOW", on_closing)
        
        try:
            window.update()
            log_message("INFO Окно плейлиста успешно открыто")
        except tk.TclError:
            log_message("ERROR Не удалось обновить окно плейлиста, приложение завершено")
            return
    
    except Exception as e:
        log_message(f"ERROR Критическая ошибка при обработке плейлиста: {e}")
        log_message(f"DEBUG Трассировка: {traceback.format_exc()}")
        try:
            messagebox.showerror("Ошибка", f"Произошла ошибка при обработке плейлиста: {str(e)}")
        except tk.TclError:
            log_message("DEBUG Не удалось показать messagebox, приложение завершено")