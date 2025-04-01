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
import requests
from bs4 import BeautifulSoup
import json
import re
from queues import add_to_queue, clear_queue_file, get_queue_count, get_queue_urls, process_queue, remove_from_queue
from tray import show_notification, tray_icon, update_download_status  # Убедимся, что импорт правильный
from config import initialize_settings, settings, is_downloading
from utils import global_file_size, global_downloaded, download_speed, last_update_time, last_downloaded_bytes, format_speed, update_speed
import utils

invidious_url_var = ""

def download_video(url, from_queue=False):
    global is_downloading, global_file_size, global_downloaded, download_speed, last_update_time, last_downloaded_bytes

    if is_downloading:
        log_message(f"INFO Загрузка уже идет, добавляем URL в очередь: {url}")
        add_to_queue(url)
        return

    is_downloading = True

    if not from_queue:
        if url in get_queue_urls():
            log_message(f"URL уже в очереди")
            is_downloading = False
            return

    # Сбрасываем перед новой загрузкой
    globals()['global_file_size'] = 0
    globals()['global_downloaded'] = 0
    globals()['download_speed'] = "0 KB/s"
    globals()['last_update_time'] = time.time()
    globals()['last_downloaded_bytes'] = 0


    if not url:
        is_downloading = False
        return

    save_path = settings["download_folder"]

    try:
        with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
            info = ydl.extract_info(url, download=False)

            if info.get('is_premiere', False) or info.get('live_status', '') == 'is_upcoming':
                log_message(f"Пропуск премьеры: {url}")
                messagebox.showinfo("Премьера", "Это видео еще не вышло (премьера). Загрузка невозможна.")
                is_downloading = False
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

            # if os.path.exists(file_path):
            #     root = tk.Tk()
            #     root.withdraw()
            #     response = messagebox.askyesno("Повторная загрузка", f"Файл '{safe_title}.{video_ext}' уже существует. Хотите загрузить его снова?")
            #     root.destroy()
            #     if not response:
            #         log_message(f"Пользователь отменил повторную загрузку: {url}")
            #         is_downloading = False
            #         return

    except yt_dlp.utils.DownloadError as e:
        log_message(f"Видео недоступно: {url}. Ошибка: {e}")
        if from_queue:
            remove_from_queue(url)
        is_downloading = False
        return
    except Exception as e:
        log_message(f"Ошибка при проверке существующего файла: {e}")
        if "Premieres in" in str(e):
            log_message(f"Пропуск премьеры: {url}")
            if from_queue:
                remove_from_queue(url)
        is_downloading = False
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
        'cookies-from-browser': True,
        'browser': 'chrome',
        'restrict_filenames': False,
        'windowsfilenames': False,
        'no_color': True,
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
            if settings["download_format"] == "mp3" and downloaded_file.endswith((".m4a", ".webm", ".mp4")):
                log_message("INFO Конвертация в MP3...")
                converted_file = convert_to_mp3(downloaded_file, update_download_status)
                if converted_file:
                    log_message(f"SUCCESS Конвертация завершена: {converted_file}")
            elif settings["download_format"] == "mp4" and downloaded_file.endswith(".webm"):
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
        error_message = f"Ошибка загрузки: {e}"
        threading.Thread(target=show_notification, args=(tray_icon, "YouTube Downloader", error_message)).start()
        log_message(error_message)

        if from_queue:
            remove_from_queue(url)
    finally:
        is_downloading = False
        queue_count = get_queue_count()
        if queue_count > 0:
            log_message(f"INFO В очереди остались URL ({queue_count}), запускаем обработку")
            time.sleep(1)
            threading.Thread(target=process_queue).start()
        else:
            log_message("INFO Очередь пуста после завершения загрузки")
            clear_queue_file()
            update_download_status("Ожидание...", 100)

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
                update_speed(global_downloaded)  # Обновляем скорость только через update_speed

            # Убрали прямое обновление download_speed из d["speed"]
            # if "speed" in d and d["speed"] is not None:
            #     globals()['download_speed'] = format_speed(d["speed"])
            #     log_message(f"DEBUG: download_speed from yt-dlp = {download_speed}")

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
    log_message(f"Обработка канала: {channel_url}")
    
    try:
        # Получаем общее количество видео на канале (для прогресса)
        try:
            with yt_dlp.YoutubeDL({'quiet': True, 'flatplaylist': True}) as ydl:
                log_message("Определение общего количества видео на канале...")
                channel_info = ydl.extract_info(channel_url, process=False)
                if 'entries' in channel_info:
                    # Проверяем, является ли entries генератором или списком
                    try:
                        if hasattr(channel_info['entries'], '__iter__') and not hasattr(channel_info['entries'], '__len__'):
                            # Если entries - генератор, преобразуем его в список
                            entries_list = list(channel_info['entries'])
                            total_videos = len(entries_list)
                        else:
                            total_videos = len(channel_info['entries'])
                        log_message(f"Предварительно обнаружено {total_videos} видео")
                    except TypeError:
                        log_message("Не удалось определить количество видео, entries является генератором")
                        total_videos = "неизвестное количество"
                else:
                    total_videos = "неизвестное количество"
                    log_message("Не удалось определить общее количество видео")
        except Exception as e:
            log_message(f"Ошибка при предварительном подсчете видео: {e}")
            total_videos = "неизвестное количество"
        
        # Показываем индикатор прогресса
        progress_root = tk.Tk()
        progress_root.title("Загрузка списка видео")
        progress_root.geometry("400x150")
        progress_root.resizable(False, False)
        
        # Центрируем окно
        progress_root.update_idletasks()
        screen_width = progress_root.winfo_screenwidth()
        screen_height = progress_root.winfo_screenheight()
        x = (screen_width - 400) // 2
        y = (screen_height - 150) // 2
        progress_root.geometry(f"400x150+{x}+{y}")
        
        # Создаем метки и прогресс-бар
        tk.Label(progress_root, text=f"Загрузка списка видео с канала...", font=("Arial", 10)).pack(pady=(20, 5))
        
        if isinstance(total_videos, int):
            info_label = tk.Label(progress_root, text=f"Обнаружено предварительно: {total_videos} видео", font=("Arial", 9))
        else:
            info_label = tk.Label(progress_root, text="Получаем информацию о видео...", font=("Arial", 9))
        info_label.pack(pady=(0, 10))
        
        progress = ttk.Progressbar(progress_root, length=350, mode="indeterminate")
        progress.pack(pady=(0, 10))
        progress.start(10)
        
        cancel_button = ttk.Button(progress_root, text="Отмена", command=progress_root.destroy)
        cancel_button.pack(pady=(0, 20))
        
        # Переменная для отслеживания отмены
        cancelled = [False]  # Используем список для изменения значения в функциях
        channel_result = [None]  # Для хранения результата из потока
        
        def check_cancelled():
            if not progress_root.winfo_exists():
                cancelled[0] = True
                return True
            return False
        
        # Обновляем интерфейс
        progress_root.update()
        
        # Задаем параметры для загрузки всех видео с канала
        ydl_opts = {
            'quiet': True,
            'extract_flat': True,     # Не загружать видео, только получить информацию
            'skip_download': True,
            'ignoreerrors': True,     # Игнорировать ошибки при получении информации о видео
            'playlistend': 10000,     # Устанавливаем очень большое значение, чтобы получить все видео
            'max_downloads': 10000,   # Также увеличиваем максимальное число загрузок
            'lazy_playlist': False,   # Отключаем ленивую загрузку для получения полного списка
            'no_color': True,  # Отключаем цветной вывод
        }
        
        # Функция для загрузки информации о канале в фоновом потоке
        def load_channel_info():
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(channel_url, download=False)
                    channel_result[0] = info
            except Exception as e:
                log_message(f"Ошибка при загрузке информации о канале: {e}")
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
                log_message("Загрузка списка видео отменена пользователем")
                return
                
            # Обновляем информацию с промежутком в 2 секунды
            current_time = time.time()
            if current_time - last_update_time >= 2:
                loaded_videos += 100  # Примерная оценка прогресса
                info_label.config(text=f"Загружено примерно {loaded_videos}+ видео...")
                last_update_time = current_time
                
            progress_root.update()
            time.sleep(0.1)
        
        # Закрываем индикатор прогресса
        progress_root.destroy()
        
        # Если пользователь отменил загрузку
        if cancelled[0]:
            log_message("Загрузка отменена пользователем")
            return
        
        # Получаем результат
        channel_info = channel_result[0]
        
        if not channel_info:
            log_message("Не удалось получить информацию о канале")
            messagebox.showinfo("Ошибка канала", "Не удалось получить информацию о канале.")
            return
            
        log_message("Информация о канале получена")
        
        if 'entries' not in channel_info:
            log_message("В канале отсутствует ключ 'entries'")
            messagebox.showinfo("Канал пуст", "На канале нет видео или произошла ошибка при получении списка.")
            return
            
        try:
            if hasattr(channel_info['entries'], '__iter__') and not hasattr(channel_info['entries'], '__len__'):
                entries = list(channel_info['entries'])
                log_message("Преобразован генератор entries в список")
            else:
                entries = channel_info.get('entries', [])
        except Exception as e:
            log_message(f"Ошибка при преобразовании entries: {e}")
            entries = []
            
        if not entries:
            log_message("Список видео пуст")
            messagebox.showinfo("Канал пуст", "На канале нет видео или произошла ошибка при получении списка.")
            return
        
        log_message(f"Получено {len(entries)} видео с канала")

        # Отфильтровываем None из entries (видео, которые не удалось обработать)
        entries = [entry for entry in entries if entry is not None]
        if not entries:
            log_message("После фильтрации None список видео пуст")
            messagebox.showinfo("Канал пуст", "На канале нет доступных для загрузки видео.")
            return
        
        channel_title = channel_info.get('title', 'Канал YouTube')
        log_message(f"Название канала: {channel_title}")
        
        # Фильтруем премьеры (видео, которые еще не вышли)
        valid_entries = []
        for entry in entries:
            try:
                if not entry.get('is_premiere', False) and not entry.get('live_status', '') == 'is_upcoming':
                    valid_entries.append(entry)
            except Exception as e:
                log_message(f"Ошибка при проверке видео на премьеру: {e}")
                # Пропускаем проблемные видео
                continue
        
        if not valid_entries:
            log_message("После фильтрации премьер список видео пуст")
            messagebox.showinfo("Нет доступных видео", "На канале нет доступных для загрузки видео (возможно, только премьеры).")
            return
            
        log_message(f"Найдено {len(valid_entries)} доступных видео из {len(entries)} на канале")
        
        # Создаем окно с выбором видео
        root = tk.Tk()
        root.title(f"Выбор видео с канала: {channel_title}")
        root.geometry("800x600")
        
        # Явно делаем окно видимым и выводим его на передний план
        root.deiconify()
        root.lift()
        root.focus_force()
        
        # На Windows также можно использовать:
        if os.name == 'nt':
            root.attributes('-topmost', True)
            root.update()
            root.attributes('-topmost', False)
        
        # Добавляем фрейм с прокруткой
        main_frame = tk.Frame(root)
        main_frame.pack(fill=tk.BOTH, expand=1, padx=10, pady=10)
        
        # Заголовок
        tk.Label(main_frame, text=f"Канал: {channel_title}", font=("Arial", 12, "bold")).pack(pady=(0, 10))
        tk.Label(main_frame, text=f"Всего доступных видео: {len(valid_entries)}", font=("Arial", 10)).pack(pady=(0, 5))
        
        # Информация о количестве загружаемых видео
        if len(entries) > len(valid_entries):
            tk.Label(main_frame, text=f"(Пропущено {len(entries) - len(valid_entries)} премьер или недоступных видео)", 
                    fg="gray").pack(pady=(0, 5))
        
        # Фрейм для поиска
        search_frame = ttk.Frame(main_frame)
        search_frame.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(search_frame, text="Поиск:").pack(side=tk.LEFT, padx=(0, 5))
        
        search_var = tk.StringVar()
        search_entry = ttk.Entry(search_frame, textvariable=search_var, width=40)
        search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        
        # Метка для отображения количества видимых элементов
        visible_label = ttk.Label(search_frame, text="")
        visible_label.pack(side=tk.RIGHT, padx=5)
        
        # Контейнер для списка с прокруткой
        container = ttk.Frame(main_frame)
        container.pack(fill=tk.BOTH, expand=1)
        
        # Полоса прокрутки
        scrollbar = ttk.Scrollbar(container)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Холст для прокрутки
        canvas = tk.Canvas(container, yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=1)
        
        scrollbar.config(command=canvas.yview)
        
        # Фрейм внутри холста для размещения чекбоксов
        checkbox_frame = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=checkbox_frame, anchor='nw')
        
        # Переменные для хранения состояния чекбоксов
        checkboxes = []
        check_vars = []

        # Функция для выделения/снятия выделения со всех видео
        def toggle_all():
            # Проверяем, все ли видимые элементы выбраны
            visible_selected = True
            for i, (var, checkbox) in enumerate(zip(check_vars, checkboxes)):
                if checkbox.winfo_ismapped() and not var.get():
                    visible_selected = False
                    break
            
            # Устанавливаем противоположное состояние
            new_state = not visible_selected
            for i, (var, checkbox) in enumerate(zip(check_vars, checkboxes)):
                if checkbox.winfo_ismapped():  # Только для видимых элементов
                    var.set(new_state)
        
        # Кнопка выделить/снять выделение
        toggle_button = ttk.Button(main_frame, text="Выделить/Снять выделение (только видимые)", command=toggle_all)
        toggle_button.pack(pady=5)
        
        # Функция для фильтрации списка видео по поисковому запросу
        def filter_videos(*args):
            search_text = search_var.get().lower()
            visible_count = 0
            
            for i, entry in enumerate(valid_entries):
                try:
                    title = entry.get('title', f'Видео {i+1}').lower()
                    if search_text in title:
                        checkboxes[i].grid()  # Показываем совпадающие
                        visible_count += 1
                    else:
                        checkboxes[i].grid_remove()  # Скрываем несовпадающие
                except Exception as e:
                    log_message(f"Ошибка при фильтрации видео {i}: {e}")
                    continue
            
            # Обновляем заголовок с информацией о количестве видимых видео
            if search_text:
                visible_label.config(text=f"Отображается: {visible_count} из {len(valid_entries)}")
            else:
                visible_label.config(text="")
            
            # Обновляем холст после фильтрации
            checkbox_frame.update_idletasks()
            canvas.config(scrollregion=canvas.bbox("all"))
        
        # Привязываем изменение поискового поля к фильтрации
        search_var.trace("w", filter_videos)
        
        # Добавляем чекбоксы для каждого видео
        for i, entry in enumerate(valid_entries):
            try:
                var = tk.BooleanVar(value=True)  # По умолчанию все выбраны
                check_vars.append(var)
                
                # Получаем заголовок видео
                title = entry.get('title', f'Видео {i+1}')
                
                # Создаем чекбокс с названием видео
                checkbox = ttk.Checkbutton(checkbox_frame, text=title, variable=var)
                checkbox.grid(row=i, column=0, sticky='w', pady=2)
                checkboxes.append(checkbox)
            except Exception as e:
                log_message(f"Ошибка при создании чекбокса для видео {i}: {e}")
                # Создаем заглушку
                var = tk.BooleanVar(value=False)
                check_vars.append(var)
                checkbox = ttk.Checkbutton(checkbox_frame, text=f"[Ошибка] Видео {i+1}", variable=var)
                checkbox.grid(row=i, column=0, sticky='w', pady=2)
                checkbox.grid_remove()  # Скрываем по умолчанию
                checkboxes.append(checkbox)
        
        # Обновляем размеры холста после добавления всех элементов
        checkbox_frame.update_idletasks()
        canvas.config(scrollregion=canvas.bbox("all"))
        
        # Привязываем прокрутку колесиком мыши
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        
        canvas.bind_all("<MouseWheel>", _on_mousewheel)
        
        # Функция для загрузки выбранных видео
        def download_selected():
            # Собираем только видимые и выбранные элементы
            selected_indices = []
            for i, (var, checkbox) in enumerate(zip(check_vars, checkboxes)):
                if checkbox.winfo_ismapped() and var.get():
                    selected_indices.append(i)
            
            if not selected_indices:
                messagebox.showinfo("Ничего не выбрано", "Выберите хотя бы одно видео для загрузки")
                return
            
            # Закрываем окно
            root.destroy()
            
            log_message(f"Выбрано {len(selected_indices)} видео с канала")
            
            # Добавляем выбранные видео в очередь загрузки
            for i in selected_indices:
                try:
                    video_id = valid_entries[i].get('id')
                    if not video_id:
                        log_message(f"Пропуск видео {i}: отсутствует ID")
                        continue
                        
                    video_url = f"https://www.youtube.com/watch?v={video_id}"
                    add_to_queue(video_url)
                    log_message(f"INFO Добавлено в очередь: {video_url}")
                except Exception as e:
                    log_message(f"Ошибка при добавлении видео {i} в очередь: {e}")
            
            # Очищаем буфер обмена после добавления видео в очередь
            clear_clipboard()
            log_message("Буфер обмена очищен после обработки плейлиста")
                                        
            # Запускаем обработку очереди, если нет активной загрузки
            if not is_downloading:
                threading.Thread(target=process_queue).start()
        
        # Кнопки действия
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(pady=10, fill=tk.X)
        
        ttk.Button(button_frame, text="Загрузить выбранные", command=download_selected).pack(side=tk.RIGHT, padx=5)
        ttk.Button(button_frame, text="Отмена", command=root.destroy).pack(side=tk.RIGHT, padx=5)
        
        # Повторно выводим окно на передний план после создания всех элементов
        root.update()
        root.lift()
        root.focus_force()
        
        # Запускаем главный цикл
        root.mainloop()
        
    except Exception as e:
        log_message(f"Критическая ошибка при обработке канала: {e}")
        log_message(f"Трассировка: {traceback.format_exc()}")
        messagebox.showerror("Ошибка", f"Произошла ошибка при обработке канала:\n{e}")

def download_playlist_with_selection(playlist_url):
    from clipboard import clear_clipboard
    """Отображает окно выбора видео из плейлиста для загрузки"""
    log_message(f"Обработка плейлиста: {playlist_url}")
    
    try:
        # Сначала получаем информацию о плейлисте
        ydl_opts = {
            'quiet': True,
            'extract_flat': True,  # Не загружать видео, только получить информацию
            'skip_download': True,
            'no_color': True,  # Отключаем цветной вывод

        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            playlist_info = ydl.extract_info(playlist_url, download=False)
            
            if 'entries' not in playlist_info or not playlist_info['entries']:
                messagebox.showinfo("Плейлист пуст", "В плейлисте нет видео или произошла ошибка при получении списка.")
                return
            
            playlist_title = playlist_info.get('title', 'Плейлист')
            entries = playlist_info['entries']
            
            # Создаем окно с выбором видео
            root = tk.Tk()
            root.title(f"Выбор видео из плейлиста: {playlist_title}")
            root.geometry("800x600")
            
            # Явно делаем окно видимым и выводим его на передний план
            root.deiconify()
            root.lift()
            root.focus_force()
            
            # На Windows также можно использовать:
            if os.name == 'nt':
                root.attributes('-topmost', True)
                root.update()
                root.attributes('-topmost', False)
            
            # Добавляем фрейм с прокруткой
            main_frame = tk.Frame(root)
            main_frame.pack(fill=tk.BOTH, expand=1, padx=10, pady=10)
            
            # Заголовок
            tk.Label(main_frame, text=f"Плейлист: {playlist_title}", font=("Arial", 12, "bold")).pack(pady=(0, 10))
            tk.Label(main_frame, text=f"Всего видео: {len(entries)}").pack(pady=(0, 10))
            
            # Контейнер для списка с прокруткой
            container = ttk.Frame(main_frame)
            container.pack(fill=tk.BOTH, expand=1)
            
            # Полоса прокрутки
            scrollbar = ttk.Scrollbar(container)
            scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
            
            # Холст для прокрутки
            canvas = tk.Canvas(container, yscrollcommand=scrollbar.set)
            canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=1)
            
            scrollbar.config(command=canvas.yview)
            
            # Фрейм внутри холста для размещения чекбоксов
            checkbox_frame = ttk.Frame(canvas)
            canvas.create_window((0, 0), window=checkbox_frame, anchor='nw')
            
            # Переменные для хранения состояния чекбоксов
            checkboxes = []
            check_vars = []
            
            # Функция для выделения/снятия выделения со всех видео
            def toggle_all():
                new_state = not all(var.get() for var in check_vars)
                for var in check_vars:
                    var.set(new_state)
            
            # Кнопка выделить/снять выделение
            toggle_button = ttk.Button(main_frame, text="Выделить/Снять выделение", command=toggle_all)
            toggle_button.pack(pady=5)
            
            # Добавляем чекбоксы для каждого видео
            for i, entry in enumerate(entries):
                var = tk.BooleanVar(value=True)  # По умолчанию все выбраны
                check_vars.append(var)
                
                # Получаем заголовок видео
                title = entry.get('title', f'Видео {i+1}')
                
                # Создаем чекбокс с названием видео
                checkbox = ttk.Checkbutton(checkbox_frame, text=title, variable=var)
                checkbox.grid(row=i, column=0, sticky='w', pady=2)
                checkboxes.append(checkbox)
            
            # Обновляем размеры холста после добавления всех элементов
            checkbox_frame.update_idletasks()
            canvas.config(scrollregion=canvas.bbox("all"))
            
            # Привязываем прокрутку колесиком мыши
            def _on_mousewheel(event):
                canvas.yview_scroll(int(-1*(event.delta/120)), "units")
            
            canvas.bind_all("<MouseWheel>", _on_mousewheel)
            
            # Функция для загрузки выбранных видео
            def download_selected():
                selected_indices = [i for i, var in enumerate(check_vars) if var.get()]
                if not selected_indices:
                    messagebox.showinfo("Ничего не выбрано", "Выберите хотя бы одно видео для загрузки")
                    return
                
                # Закрываем окно
                root.destroy()
                
                log_message(f"Выбрано {len(selected_indices)} видео из плейлиста")
                
                # Добавляем выбранные видео в очередь загрузки
                for i in selected_indices:
                    video_id = entries[i].get('id')
                    video_url = f"https://www.youtube.com/watch?v={video_id}"
                    add_to_queue(video_url)

                # Очищаем буфер обмена после добавления видео в очередь
                clear_clipboard()
                log_message("Буфер обмена очищен после обработки плейлиста")
                
                
                # Запускаем обработку очереди, если нет активной загрузки
                if not is_downloading:
                    threading.Thread(target=process_queue).start()
            
            # Кнопки действия
            button_frame = ttk.Frame(main_frame)
            button_frame.pack(pady=10, fill=tk.X)
            
            ttk.Button(button_frame, text="Загрузить выбранные", command=download_selected).pack(side=tk.RIGHT, padx=5)
            ttk.Button(button_frame, text="Отмена", command=root.destroy).pack(side=tk.RIGHT, padx=5)
            
            # Повторно выводим окно на передний план после создания всех элементов
            root.update()
            root.lift()
            root.focus_force()
            
            # Запускаем главный цикл
            root.mainloop()
            
    except Exception as e:
        log_message(f"Ошибка при обработке плейлиста: {e}")
        messagebox.showerror("Ошибка", f"Произошла ошибка при обработке плейлиста:\n{e}")


    
            