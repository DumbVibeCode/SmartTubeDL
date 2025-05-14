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
from utils import global_file_size, global_downloaded, download_speed, last_update_time, last_downloaded_bytes, format_speed, update_speed
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

invidious_url_var = ""

def download_video(url, from_queue=False):
    global is_downloading, global_file_size, global_downloaded, download_speed, last_update_time, last_downloaded_bytes

    if is_downloading:
        log_message(f"INFO Загрузка уже идет, добавляем URL в очередь: {url}")
        add_to_queue(url)
        return

    is_downloading = True

    # Ensure the queue is processed after the current download finishes
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

        # 'cookies-from-browser': True,
        # 'browser': 'firefox',

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

def download_channel_with_selection(channel_url):
    """Отображает окно выбора видео из канала для загрузки"""
    from clipboard import clear_clipboard
    log_message(f"INFO Обработка канала: {channel_url}")
    
    try:
        # Получаем общее количество видео на канале (для прогресса)
        try:
            with yt_dlp.YoutubeDL({'quiet': True, 'flatplaylist': True}) as ydl:
                log_message("DEBUG Определение общего количества видео на канале...")
                channel_info = ydl.extract_info(channel_url, process=False)
                if 'entries' in channel_info:
                    try:
                        if hasattr(channel_info['entries'], '__iter__') and not hasattr(channel_info['entries'], '__len__'):
                            entries_list = list(channel_info['entries'])
                            total_videos = len(entries_list)
                        else:
                            total_videos = len(channel_info['entries'])
                        log_message(f"DEBUG Предварительно обнаружено {total_videos} видео")
                    except TypeError:
                        log_message("DEBUG Не удалось определить количество видео, entries является генератором")
                        total_videos = "неизвестное количество"
                else:
                    total_videos = "неизвестное количество"
                    log_message("DEBUG Не удалось определить общее количество видео")
        except Exception as e:
            log_message(f"ERROR Ошибка при предварительном подсчете видео: {e}")
            total_videos = "неизвестное количество"
        
        # Проверяем корневое окно из tray.py
        from tray import root
        if root is None or not hasattr(root, 'winfo_exists') or not root.winfo_exists():
            log_message("DEBUG Создание нового корневого окна в download_channel_with_selection")
            root = tk.Tk()
            root.withdraw()
            import tray
            tray.root = root
        else:
            pass

        # Создаём окно прогресс-бара как Toplevel
        progress_window = tk.Toplevel(root)
        progress_window.title("Загрузка списка видео")
        progress_window.geometry("400x150")
        progress_window.resizable(False, False)
        
        # Центрируем окно прогресс-бара
        progress_window.update_idletasks()
        screen_width = progress_window.winfo_screenwidth()
        screen_height = progress_window.winfo_screenheight()
        x = (screen_width - 400) // 2
        y = (screen_height - 150) // 2
        progress_window.geometry(f"400x150+{x}+{y}")
        
        # Создаем метки и прогресс-бар
        tk.Label(progress_window, text="Загрузка списка видео с канала...", font=("Arial", 10)).pack(pady=(20, 5))
        
        if isinstance(total_videos, int):
            info_label = tk.Label(progress_window, text=f"Обнаружено предварительно: {total_videos} видео", font=("Arial", 9))
        else:
            info_label = tk.Label(progress_window, text="Получаем информацию о видео...", font=("Arial", 9))
        info_label.pack(pady=(0, 10))
        
        progress = ttk.Progressbar(progress_window, length=350, mode="indeterminate")
        progress.pack(pady=(0, 10))
        progress.start(10)
        
        cancel_button = ttk.Button(progress_window, text="Отмена", command=progress_window.destroy)
        cancel_button.pack(pady=(0, 20))
        
        # Переменная для отслеживания отмены
        cancelled = [False]
        channel_result = [None]
        
        def check_cancelled():
            if not progress_window.winfo_exists():
                cancelled[0] = True
                return True
            return False
        
        # Обновляем интерфейс
        progress_window.update()
        
        # Задаем параметры для загрузки всех видео с канала
        ydl_opts = {
            'quiet': True,
            'extract_flat': True,
            'skip_download': True,
            'ignoreerrors': True,
            'playlistend': 10000,
            'max_downloads': 10000,
            'lazy_playlist': False,
            'no_color': True,
        }
        
        # Функция для загрузки информации о канале в фоновом потоке
        def load_channel_info():
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(channel_url, download=False)
                    channel_result[0] = info
            except Exception as e:
                log_message(f"ERROR Ошибка при загрузке информации о канале: {e}")
                channel_result[0] = None
        
        # Запускаем загрузку в отдельном потоке
        channel_info_thread = threading.Thread(target=load_channel_info)
        channel_info_thread.daemon = True
        channel_info_thread.start()
        
        # Обновляем индикатор прогресса, пока идет загрузка
        loaded_videos = 0
        last_update_time = time.time()
        
        while channel_info_thread.is_alive():
            if check_cancelled():
                log_message("INFO Загрузка списка видео отменена пользователем")
                return
                
            current_time = time.time()
            if current_time - last_update_time >= 2:
                loaded_videos += 100
                info_label.config(text=f"Загружено примерно {loaded_videos}+ видео...")
                last_update_time = current_time
                
            try:
                progress_window.update()
            except tk.TclError:
                log_message("DEBUG Прогресс-бар закрыт во время обновления")
                cancelled[0] = True
                return
        
        # Закрываем окно прогресс-бара
        try:
            progress_window.destroy()
        except tk.TclError:
            log_message("DEBUG Прогресс-бар уже закрыт")
        
        if cancelled[0]:
            log_message("INFO Загрузка отменена пользователем")
            return
        
        channel_info = channel_result[0]
        
        if not channel_info:
            log_message("ERROR Не удалось получить информацию о канале")
            messagebox.showerror("Ошибка", "Не удалось получить информацию о канале.")
            return
            
        log_message("INFO Информация о канале получена")
        
        if 'entries' not in channel_info:
            log_message("ERROR В канале отсутствует ключ 'entries'")
            messagebox.showerror("Ошибка", "На канале нет видео или произошла ошибка при получении списка.")
            return
            
        try:
            if hasattr(channel_info['entries'], '__iter__') and not hasattr(channel_info['entries'], '__len__'):
                entries = list(channel_info['entries'])
                log_message("DEBUG Преобразован генератор entries в список")
            else:
                entries = channel_info.get('entries', [])
        except Exception as e:
            log_message(f"ERROR Ошибка при преобразовании entries: {e}")
            entries = []
            
        if not entries:
            log_message("ERROR Список видео пуст")
            messagebox.showerror("Ошибка", "На канале нет видео или произошла ошибка при получении списка.")
            return
        
        log_message(f"INFO Получено {len(entries)} видео с канала")

        entries = [entry for entry in entries if entry is not None]
        if not entries:
            log_message("ERROR После фильтрации None список видео пуст")
            messagebox.showerror("Ошибка", "На канале нет доступных для загрузки видео.")
            return
        
        channel_title = channel_info.get('title', 'Канал YouTube')
        log_message(f"INFO Название канала: {channel_title}")
        
        valid_entries = []
        for entry in entries:
            try:
                if (not entry.get('is_premiere', False) and 
                    entry.get('live_status', '') != 'is_upcoming' and 
                    entry.get('title') != '[Private video]'):
                    valid_entries.append(entry)
                else:
                    log_message(f"DEBUG Пропущено видео: {entry.get('title', 'Без названия')}, "
                               f"причина: {'премьера' if entry.get('is_premiere', False) else ''}"
                               f"{'предстоящее' if entry.get('live_status', '') == 'is_upcoming' else ''}"
                               f"{'приватное' if entry.get('title') == '[Private video]' else ''}")
            except Exception as e:
                log_message(f"ERROR Ошибка при проверке видео: {e}")
                continue
        
        if not valid_entries:
            log_message("ERROR После фильтрации список видео пуст")
            messagebox.showerror("Ошибка", "На канале нет доступных для загрузки видео.")
            return
            
        log_message(f"INFO Найдено {len(valid_entries)} доступных видео из {len(entries)} на канале")
        
        # Создаём основное окно как Toplevel
        window = tk.Toplevel(root)
        window.title(f"Выбор видео с канала: {channel_title}")
        window.geometry("800x600")
        window.resizable(True, True)
        
        # Центрируем окно
        window.update_idletasks()
        screen_width = window.winfo_screenwidth()
        screen_height = window.winfo_screenheight()
        x = (screen_width - 800) // 2
        y = (screen_height - 600) // 2
        window.geometry(f"800x600+{x}+{y}")
        
        # Делаем окно активным
        window.deiconify()
        window.lift()
        window.focus_force()
        if os.name == 'nt':
            window.attributes('-topmost', True)
            window.update()
            window.attributes('-topmost', False)
        
        # Добавляем фрейм с прокруткой
        main_frame = ttk.Frame(window, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Заголовок
        ttk.Label(main_frame, text=f"Канал: {channel_title}", font=("Arial", 12, "bold")).pack(pady=5)
        ttk.Label(main_frame, text=f"Всего доступных видео: {len(valid_entries)}").pack(pady=5)
        
        if len(entries) > len(valid_entries):
            ttk.Label(main_frame, text=f"(Пропущено {len(entries) - len(valid_entries)} премьер, приватных или недоступных видео)").pack(pady=(0, 5))
        
        # Фрейм для поиска
        search_frame = ttk.Frame(main_frame)
        search_frame.pack(fill=tk.X, pady=5)
        ttk.Label(search_frame, text="Поиск:").pack(side=tk.LEFT, padx=(0, 5))
        
        search_var = tk.StringVar()
        search_entry = ttk.Entry(search_frame, textvariable=search_var)
        search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        visible_label = ttk.Label(search_frame, text="")
        visible_label.pack(side=tk.RIGHT, padx=5)
        
        # Фрейм с прокруткой
        canvas = tk.Canvas(main_frame)
        scrollbar = ttk.Scrollbar(main_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Переменные для чекбоксов
        checkboxes = []
        check_vars = []
        
        # Функция для выделения/снять выделение
        def toggle_all():
            visible_selected = all(var.get() for i, var in enumerate(check_vars) if checkboxes[i].winfo_ismapped())
            new_state = not visible_selected
            for i, var in enumerate(check_vars):
                if checkboxes[i].winfo_ismapped():
                    var.set(new_state)
            log_message(f"DEBUG Переключено состояние видимых чекбоксов: {new_state}")
        
        # Добавляем чекбоксы
        for i, entry in enumerate(valid_entries):
            try:
                title = entry.get('title', f'Видео {i+1}')
                var = tk.BooleanVar(value=True)
                check_vars.append(var)
                cb = ttk.Checkbutton(scrollable_frame, text=title, variable=var)
                cb.grid(row=i, column=0, sticky="w", pady=2)
                checkboxes.append(cb)
            except Exception as e:
                log_message(f"ERROR Ошибка при создании чекбокса {i}: {e}")
        
        # Кнопка выделения
        ttk.Button(main_frame, text="Выделить/Снять (видимые)", command=toggle_all).pack(pady=5)
        
        # Функция фильтрации видео
        def filter_videos(*args):
            search_text = search_var.get().lower()
            visible_count = 0
            
            for i, entry in enumerate(valid_entries):
                try:
                    title = entry.get('title', f'Видео {i+1}').lower()
                    if search_text in title:
                        checkboxes[i].grid(row=i, column=0, sticky="w", pady=2)
                        visible_count += 1
                    else:
                        checkboxes[i].grid_remove()
                except Exception as e:
                    log_message(f"ERROR Ошибка при фильтрации видео {i}: {e}")
            
            visible_label.config(text=f"Отображается: {visible_count} из {len(valid_entries)}" if search_text else "")
            scrollable_frame.update_idletasks()
            canvas.configure(scrollregion=canvas.bbox("all"))
        
        search_var.trace("w", filter_videos)
        
        # Функция загрузки выбранных видео
        def download_selected():
            selected_urls = []
            for i, var in enumerate(check_vars):
                if var.get() and checkboxes[i].winfo_ismapped():
                    video_id = valid_entries[i].get('id')
                    if video_id:
                        url = f"https://www.youtube.com/watch?v={video_id}"
                        selected_urls.append(url)
                        log_message(f"DEBUG Выбрано видео: {url}")

            if not selected_urls:
                log_message("WARNING Ничего не выбрано для загрузки")
                messagebox.showinfo("Ошибка", "Выберите хотя бы одно видео для загрузки")
                return

            try:
                window.destroy()
            except tk.TclError:
                log_message("DEBUG Окно канала уже закрыто")

            log_message(f"INFO Выбрано {len(selected_urls)} видео для загрузки")

            for url in selected_urls:
                add_to_queue(url)
                # log_message(f"INFO Добавлено в очередь: {url}")

            clear_clipboard()
            log_message("INFO Буфер обмена очищен")

            if not is_downloading:
                threading.Thread(target=process_queue, daemon=True).start()
        
        # Кнопки управления
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=10)
        ttk.Button(button_frame, text="Загрузить выбранные", command=download_selected).pack(side=tk.RIGHT, padx=5)
        ttk.Button(button_frame, text="Отмена", command=window.destroy).pack(side=tk.RIGHT, padx=5)
        
        # Поддержка прокрутки колесом мыши
        def on_mousewheel(event):
            try:
                if hasattr(canvas, 'winfo_exists') and canvas.winfo_exists():
                    canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            except Exception as e:
                log_message(f"DEBUG Игнорирована ошибка прокрутки: {e}")
        
        window.bind("<MouseWheel>", on_mousewheel)
        
        # Обработчик закрытия
        def on_closing():
            try:
                window.unbind("<MouseWheel>")
                log_message("INFO Окно канала закрыто")
                window.destroy()
            except tk.TclError:
                log_message("DEBUG Окно канала уже уничтожено")
        
        window.protocol("WM_DELETE_WINDOW", on_closing)
        
        # Инициализируем интерфейс
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
    """Отображает окно выбора видео из плейлиста для загрузки"""
    log_message(f"INFO Обработка плейлиста: {playlist_url}")
    
    try:
        ydl_opts = {
            'quiet': True,
            'extract_flat': True,
            'skip_download': True,
            'no_color': True,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            playlist_info = ydl.extract_info(playlist_url, download=False)
            
            if 'entries' not in playlist_info or not playlist_info['entries']:
                log_message("ERROR Плейлист пуст или ошибка получения данных")
                messagebox.showinfo("Плейлист пуст", "В плейлисте нет видео или произошла ошибка.")
                return
            
            playlist_title = playlist_info.get('title', 'Плейлист')
            entries = [e for e in playlist_info['entries'] if e and not e.get('is_premiere', False) and e.get('live_status', '') != 'is_upcoming']
            
            if not entries:
                log_message("ERROR Нет доступных видео в плейлисте")
                messagebox.showinfo("Плейлист пуст", "Нет доступных для загрузки видео.")
                return
            
            log_message(f"INFO Найдено {len(entries)} видео в плейлисте")

        from tray import root
        if root is None or not hasattr(root, 'winfo_exists') or not root.winfo_exists():
            log_message("DEBUG Создание нового корневого окна в download_playlist_with_selection")
            root = tk.Tk()
            root.withdraw()
            import tray
            tray.root = root
        else:
            pass

        valid_entries = []
        for entry in entries:
            try:
                if entry.get('title') != '[Private video]':
                    valid_entries.append(entry)
                else:
                    log_message(f"DEBUG Пропущено приватное видео: {entry.get('title', 'Без названия')}")
            except Exception as e:
                log_message(f"ERROR Ошибка при проверке видео: {e}")
                continue
        
        if not valid_entries:
            log_message("ERROR После фильтрации приватных видео список пуст")
            messagebox.showinfo("Плейлист пуст", "Нет доступных для загрузки видео.")
            return

        log_message(f"INFO Найдено {len(valid_entries)} доступных видео из {len(entries)} в плейлисте")

        window = tk.Toplevel(root)
        window.title(f"Выбор видео: {playlist_title}")
        window.geometry("800x600")
        window.resizable(True, True)
        
        window.update_idletasks()
        screen_width = window.winfo_screenwidth()
        screen_height = window.winfo_screenheight()
        x = (screen_width - 800) // 2
        y = (screen_height - 600) // 2
        window.geometry(f"800x600+{x}+{y}")
        
        window.deiconify()
        window.lift()
        window.focus_force()
        if os.name == 'nt':
            window.attributes('-topmost', True)
            window.update()
            window.attributes('-topmost', False)

        main_frame = ttk.Frame(window, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(main_frame, text=f"Плейлист: {playlist_title}", font=("Arial", 12, "bold")).pack(pady=5)
        ttk.Label(main_frame, text=f"Видео: {len(valid_entries)}").pack(pady=5)
        
        if len(entries) > len(valid_entries):
            ttk.Label(main_frame, text=f"(Пропущено {len(entries) - len(valid_entries)} приватных видео)").pack(pady=(0, 5))

        # Фрейм для поиска
        search_frame = ttk.Frame(main_frame)
        search_frame.pack(fill=tk.X, pady=5)
        ttk.Label(search_frame, text="Поиск:").pack(side=tk.LEFT, padx=(0, 5))
        
        search_var = tk.StringVar()
        search_entry = ttk.Entry(search_frame, textvariable=search_var)
        search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        visible_label = ttk.Label(search_frame, text="")
        visible_label.pack(side=tk.RIGHT, padx=5)

        canvas = tk.Canvas(main_frame)
        scrollbar = ttk.Scrollbar(main_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        check_vars = []
        checkboxes = []

        for i, entry in enumerate(valid_entries):
            try:
                title = entry.get('title', f'Видео {i+1}')
                var = tk.BooleanVar(value=True)
                check_vars.append(var)
                cb = ttk.Checkbutton(scrollable_frame, text=title, variable=var)
                cb.grid(row=i, column=0, sticky="w", pady=2)
                checkboxes.append(cb)
            except Exception as e:
                log_message(f"ERROR Ошибка при создании чекбокса {i}: {e}")

        def toggle_all():
            visible_selected = all(var.get() for i, var in enumerate(check_vars) if checkboxes[i].winfo_ismapped())
            new_state = not visible_selected
            for i, var in enumerate(check_vars):
                if checkboxes[i].winfo_ismapped():
                    var.set(new_state)
            log_message(f"DEBUG Переключено состояние видимых чекбоксов: {new_state}")

        ttk.Button(main_frame, text="Выделить/Снять (видимые)", command=toggle_all).pack(pady=5)

        # Функция фильтрации видео
        def filter_videos(*args):
            search_text = search_var.get().lower()
            visible_count = 0
            
            for i, entry in enumerate(valid_entries):
                try:
                    title = entry.get('title', f'Видео {i+1}').lower()
                    if search_text in title:
                        checkboxes[i].grid(row=i, column=0, sticky="w", pady=2)
                        visible_count += 1
                    else:
                        checkboxes[i].grid_remove()
                except Exception as e:
                    log_message(f"ERROR Ошибка при фильтрации видео {i}: {e}")
            
            visible_label.config(text=f"Отображается: {visible_count} из {len(valid_entries)}" if search_text else "")
            scrollable_frame.update_idletasks()
            canvas.configure(scrollregion=canvas.bbox("all"))
        
        search_var.trace("w", filter_videos)

        def download_selected():
            selected_urls = []
            for i, var in enumerate(check_vars):
                if var.get() and checkboxes[i].winfo_ismapped():
                    video_id = valid_entries[i].get('id')
                    if video_id:
                        url = f"https://www.youtube.com/watch?v={video_id}"
                        selected_urls.append(url)
                        log_message(f"DEBUG Выбрано видео: {url}")

            if not selected_urls:
                log_message("WARNING Ничего не выбрано для загрузки")
                messagebox.showinfo("Ошибка", "Выберите хотя бы одно видео для загрузки")
                return

            try:
                window.destroy()
            except tk.TclError:
                log_message("DEBUG Окно плейлиста уже закрыто")

            log_message(f"INFO Выбрано {len(selected_urls)} видео для загрузки")

            for url in selected_urls:
                add_to_queue(url)
                # log_message(f"INFO Добавлено в очередь: {url}")

            clear_clipboard()
            log_message("INFO Буфер обмена очищен")

            if not is_downloading:
                threading.Thread(target=process_queue, daemon=True).start()

        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=10)
        ttk.Button(button_frame, text="Загрузить выбранные", command=download_selected).pack(side=tk.RIGHT, padx=5)
        ttk.Button(button_frame, text="Отмена", command=window.destroy).pack(side=tk.RIGHT, padx=5)

        def on_mousewheel(event):
            try:
                if hasattr(canvas, 'winfo_exists') and canvas.winfo_exists():
                    canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            except Exception as e:
                log_message(f"DEBUG Игнорирована ошибка прокрутки: {e}")

        window.bind("<MouseWheel>", on_mousewheel)

        def on_closing():
            try:
                window.unbind("<MouseWheel>")
                log_message("INFO Окно плейлиста закрыто")
                window.destroy()
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
        log_message(f"ERROR Ошибка при обработке плейлиста: {e}")
        try:
            messagebox.showerror("Ошибка", f"Не удалось обработать плейлист: {str(e)}")
        except tk.TclError:
            log_message("DEBUG Не удалось показать messagebox, приложение завершено")