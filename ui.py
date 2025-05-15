import json
import os
import subprocess
import time
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk
import traceback
from ttkwidgets.autocomplete import AutocompleteEntry
import webbrowser
import pyperclip
import threading
import vlc
import yt_dlp
from clipboard_utils import update_last_copy_time
from config import settings, save_settings, is_downloading
from fetch import fetch_description_with_ytdlp
from logger import clear_log, load_log_file, log_message, set_log_box
from queues import add_to_queue, process_queue
from search import perform_search
# from utils import decode_html_entities, format_views, format_date, sort_column
from config import initialize_settings, save_settings, is_downloading, update_single_setting, bind_var_to_settings
from description import show_description
from search import perform_search

# settings = initialize_settings()
is_programmatic_update = False
last_slider_value = 0
search_window = None
log_box = None
video_descriptions = {}
save_settings_var = None  # Объявляем как глобальную переменную на уровне модуля

def configure_entry(parent, textvariable, label_text=None, width=50, focus=False, entry_type="entry", completevalues=None):
    if label_text:
        ttk.Label(parent, text=label_text).pack(side=tk.LEFT, padx=(0, 5))
    
    if entry_type == "autocomplete" and completevalues is not None:
        entry = AutocompleteEntry(parent, textvariable=textvariable, width=width, completevalues=completevalues)
    else:
        entry = ttk.Entry(parent, textvariable=textvariable, width=width)
    
    if focus:
        entry.focus()
        
    # Восстанавливаем стандартное поведение клавиш
    def select_all(event):
        event.widget.select_range(0, tk.END)
        event.widget.icursor(tk.END)
        return "break"

    def handle_paste(event=None):
        try:
            if entry.selection_present():
                entry.delete(tk.SEL_FIRST, tk.SEL_LAST)
            clipboard = entry.clipboard_get()
            entry.insert(tk.INSERT, clipboard)
            return "break"
        except tk.TclError:
            log_message("WARNING: Буфер обмена пуст или недоступен")
            return None

    def handle_key_press(event):
        if event.keysym in ("Left", "Right"):
            return None
        if event.keysym.lower() == "a" and (event.state & 0x4 or event.state & 0x8):
            return select_all(event)
        if event.keysym.lower() == "v" and (event.state & 0x4 or event.state & 0x8):
            return handle_paste()
        if event.keysym == "Insert" and (event.state & 0x1):
            return handle_paste()
        return None

    def show_context_menu(event):
            menu = tk.Menu(entry, tearoff=0)
            menu.add_command(label="Вырезать", command=lambda: entry.event_generate("<<Cut>>"))
            menu.add_command(label="Копировать", command=lambda: entry.event_generate("<<Copy>>"))
            menu.add_command(label="Вставить", command=handle_paste)
            menu.add_command(label="Выделить всё", command=lambda: select_all(event))
            menu.tk_popup(event.x_root, event.y_root)

    entry.bind("<Key>", handle_key_press)
    entry.bind("<Button-3>", show_context_menu)
    entry.bind("<Control-a>", select_all)
    entry.bind("<Control-A>", select_all)
    entry.bind("<Command-a>", select_all)
    entry.bind("<Control-v>", handle_paste)
    entry.bind("<Command-v>", handle_paste)

    return entry         

# Справка по получению API Key
def show_api_help():
    help_text = """
    Для использования поиска необходимо получить API Key в Google Cloud Console:

    1. Перейдите на сайт: https://console.cloud.google.com/
    2. Создайте новый проект
    3. Включите YouTube Data API v3
    4. Создайте учетные данные (API Key)
    5. Скопируйте ключ и вставьте его в поле API Key

    Обратите внимание, что бесплатное использование API имеет суточные лимиты.

    Для альтернативного метода поиска:
    Установите галочку "Искать альтернативным методом" и укажите URL
    публичного Invidious экземпляра. Например: https://invidious.fdn.fr
    """

    messagebox.showinfo("Получение API Key", help_text)

def generate_vlc_cache(vlc_path):
    """Генерирует кэш плагинов VLC, если он отсутствует или устарел."""
    plugins_dir = os.path.join(vlc_path, "plugins")
    cache_gen_path = os.path.join(vlc_path, "vlc-cache-gen.exe")
    cache_file = os.path.join(os.getenv("APPDATA"), "vlc", "plugins.dat")

    if os.path.exists(cache_gen_path):
        if not os.path.exists(cache_file) or os.path.getmtime(cache_file) < max(os.path.getmtime(f) for f in os.listdir(plugins_dir) if f.endswith(".dll")):
            log_message(f"INFO: Генерирую новый кэш для VLC в {cache_file}")
            try:
                subprocess.run([cache_gen_path, plugins_dir], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                log_message("INFO: Кэш успешно сгенерирован")
            except subprocess.CalledProcessError as e:
                log_message(f"ERROR: Ошибка при генерации кэша: {e}")
        else:
            log_message("INFO: Кэш актуален, пропускаю генерацию")
    else:
        log_message("WARNING: vlc-cache-gen.exe не найден, кэш не сгенерирован")

def play_video(tree, video_urls, search_window, status_var, status_label, video_descriptions):
    """Открывает окно для воспроизведения видео и отображения описания с прокруткой"""
    selected = tree.selection()[0] if tree.selection() else None
    if not selected or selected not in video_urls:
        status_var.set("Выберите видео для воспроизведения")
        status_label.config(foreground="red")
        log_message("WARNING: Не выбрано видео для воспроизведения")
        return

    video_url = video_urls[selected]
    description = video_descriptions.get(video_url, None)
    if not description:
        log_message(f"DEBUG: Описание для {video_url} отсутствует, загружаем...")
        description = fetch_description_with_ytdlp(video_url) or "Описание отсутствует"
        video_descriptions[video_url] = description
    log_message(f"DEBUG: Пытаемся воспроизвести видео: {video_url}, описание: {description[:50]}...")

    # Генерируем кэш VLC, если нужно
    vlc_path = r"C:\Program Files\VideoLAN\VLC"
    generate_vlc_cache(vlc_path)

    # Извлекаем прямой URL с помощью yt-dlp
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'format': 'bestvideo+bestaudio/best[protocol^=http][protocol!*=dash][ext=mp4]',
        'hls_prefer_native': False,
        'hls_use_mpegts': True,  # Используем MPEG-TS для лучшей синхронизации
        'cookies': 'cookies.txt' # if os.path.exists('cookies.txt') else None,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
            formats = info.get('formats', [])
            # Логируем доступные форматы для отладки
            for f in formats:
                log_message(f"DEBUG: Формат: protocol={f.get('protocol')}, ext={f.get('ext')}, url={f.get('url')}")
            stream_url = None
            for f in formats:
                if f.get('vcodec') != 'none' and f.get('acodec') != 'none' and 'hls' in f.get('protocol', '').lower():
                    stream_url = f.get('url')
                    log_message(f"DEBUG: Найден HLS-видеопоток: {stream_url}")
                    break
            if not stream_url:
                for f in formats:
                    if f.get('vcodec') != 'none' and f.get('acodec') != 'none' and f.get('ext') == 'mp4':
                        stream_url = f.get('url')
                        log_message(f"DEBUG: Найден MP4-видеопоток: {stream_url}")
                        break
            if not stream_url:
                log_message(f"WARNING: Видеопоток не найден, пытаемся взять первый доступный URL")
                stream_url = info.get('url') if 'url' in info else info['formats'][0]['url']
            if 'storyboard' in stream_url.lower():
                log_message(f"ERROR: Извлечён URL раскадровки вместо видео: {stream_url}")
                status_var.set("Ошибка: извлечён URL раскадровки вместо видео")
                return
            log_message(f"DEBUG: Извлечён прямой URL: {stream_url}")
    except Exception as e:
        log_message(f"ERROR: Не удалось извлечь прямой URL: {e}")
        status_var.set("Не удалось получить поток видео")
        status_label.config(foreground="red")
        return
    
    def on_resize(event):
        new_height = player_window.winfo_height()
        video_frame.config(height=new_height-250)

    player_window = tk.Toplevel(search_window)
    player_window.title("Воспроизведение видео")
    player_window.geometry("800x600")
    player_window.bind('<Configure>', on_resize)

    player_window.update_idletasks()
    screen_width = player_window.winfo_screenwidth()
    screen_height = player_window.winfo_screenheight()
    x = (screen_width - 800) // 2
    y = (screen_height - 700) // 2
    player_window.geometry(f"800x700+{x}+{y}")

    video_frame = ttk.Frame(player_window)
    video_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    # Изначальная высота фрейма
    video_height = 400
    video_frame.config(height=video_height)

    try:
        # Инициализация VLC с увеличенным буфером
        instance = vlc.Instance('--network-caching=10000', '--avcodec-hw=any', '--avcodec-skiploopfilter=1', '--avcodec-skip-frame=0')  # Буфер 3500 мс
        if instance is None:
            log_message("ERROR: Не удалось инициализировать VLC. Проверьте установку или укажите путь в настройках.")
            status_var.set("Ошибка инициализации VLC. Убедитесь, что VLC установлен.")
            return

        player = instance.media_player_new()
        media = instance.media_new(stream_url)
        player.set_media(media)

        video_canvas = tk.Frame(video_frame, bg="black")
        video_canvas.pack(fill=tk.BOTH, expand=True)

        if os.name == "nt":
            player.set_hwnd(video_canvas.winfo_id())
        else:
            player.set_xwindow(video_canvas.winfo_id())

        # Прокрутка видео
        controls_frame = ttk.Frame(player_window)
        controls_frame.pack(fill=tk.X, padx=5, pady=5)

        def update_slider():
            global is_programmatic_update, last_slider_value
            
            if player.is_playing():
                current_time = player.get_time()
                total_length = player.get_length()
                
                if total_length > 0:
                    new_value = (current_time / total_length) * 1000
                    
                    # Обновляем только если значение изменилось значительно
                    if abs(new_value - last_slider_value) > 1:  # Порог в 1 единицу
                        is_programmatic_update = True
                        slider.set(new_value)
                        last_slider_value = new_value
                        is_programmatic_update = False
            
            player_window.after(200, update_slider)

        def set_position(value):
            if not is_programmatic_update and player.get_length() > 0:
                new_time = int(player.get_length() * (float(value) / 1000))
                
                # Особый случай: если видео закончилось
                if player.get_state() == vlc.State.Ended:
                    media = player.get_media()
                    player.set_media(media)
                    player.play()
                    player.set_time(new_time)
                else:
                    player.set_time(new_time)
                    
                # Обновляем состояние кнопки
                if new_time < player.get_length() - 2000:  # Если не в последних 2 секундах
                    play_button.config(text="❚❚ Пауза")


        slider = ttk.Scale(
            controls_frame,
            from_=0,
            to=1000,
            orient=tk.HORIZONTAL,
            length=300,
            command=lambda val: set_position(val)
        )
        slider.pack(side=tk.LEFT, padx=5)
        player_window.after(200, update_slider)


        def toggle_play_pause():
            try:
                if player.get_state() == vlc.State.Ended:
                    # Полная перезагрузка медиа для корректного рестарта
                    media = player.get_media()
                    player.set_media(media)
                    player.play()
                    play_button.config(text="❚❚ Пауза")
                elif player.is_playing():
                    player.pause()
                    play_button.config(text="▶ Играть")
                else:
                    player.play()
                    play_button.config(text="❚❚ Пауза")
            except Exception as e:
                log_message(f"ERROR in toggle_play_pause: {str(e)}")

        play_button = ttk.Button(controls_frame, text="❚❚ Пауза", command=toggle_play_pause)
        play_button.pack(side=tk.LEFT, padx=5)

        description_frame = ttk.LabelFrame(player_window, text="Описание видео", padding=5)
        description_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        description_text = scrolledtext.ScrolledText(description_frame, wrap=tk.WORD, height=6, state='normal')
        description_text.insert(tk.END, description)
        description_text.config(state='disabled')
        description_text.pack(fill=tk.BOTH, expand=True)

        def copy_description():
            pyperclip.copy(description)
            status_var.set("Описание скопировано в буфер обмена")
            status_label.config(foreground="green")
            log_message("INFO Описание видео скопировано")

        copy_button = ttk.Button(controls_frame, text="Копировать описание", command=copy_description)
        copy_button.pack(side=tk.LEFT, padx=5)

        def download_video():
            if add_to_queue(video_url):
                status_var.set("Видео добавлено в очередь загрузки")
                status_label.config(foreground="green")
                log_message(f"INFO Видео добавлено в очередь загрузки: {video_url}")
                if not is_downloading:
                    threading.Thread(target=process_queue).start()
            else:
                status_var.set("Видео уже в очереди загрузки")
                status_label.config(foreground="orange")

        download_format = settings.get("download_format", "mp4")
        download_button = ttk.Button(controls_frame, text=f"Скачать ({download_format})", command=download_video)
        download_button.pack(side=tk.LEFT, padx=5)

        def on_player_window_close():
            player.stop()
            player.release()
            instance.release()
            player_window.destroy()

        player_window.protocol("WM_DELETE_WINDOW", on_player_window_close)

        player.play()
        search_window.after(1000, lambda: log_message(f"DEBUG: Статус воспроизведения: {player.get_state()}"))
        search_window.after(2000, lambda: log_message(f"DEBUG: Длительность: {player.get_length()}"))

    except Exception as e:
        log_message(f"ERROR Ошибка при воспроизведении видео: {e}")
        status_var.set(f"Ошибка воспроизведения: {e}")
        status_label.config(foreground="red")
        player_window.destroy()    
    
def search_youtube_videos():
    """Отображает окно для поиска видео через YouTube API"""
    global search_window, save_settings_var, log_box
    
    # Проверяем, существует ли окно и активно ли оно
    if search_window is not None and search_window.winfo_exists():
        search_window.deiconify()
        search_window.lift()
        search_window.focus_force()
        if os.name == 'nt':
            search_window.attributes('-topmost', True)
            search_window.update()
            search_window.attributes('-topmost', False)
        return

    try:
        
        from tray import root
        
        if root is None:
            log_message("DEBUG Создание root в search_youtube_videos")
            root = tk.Tk()
            root.withdraw()  # Скрываем корневое окно
            # Обновляем root в tray.py
            import tray
            tray.root = root

        # Создаём окно поиска как Toplevel
        search_window = tk.Toplevel(root)
        if search_window is None:
            raise RuntimeError("Не удалось создать окно поиска")

        search_window.title("Расширенный поиск YouTube")
        search_window.geometry("1200x850")
        
        # Центрирование окна
        search_window.update_idletasks()
        screen_width = search_window.winfo_screenwidth()
        screen_height = search_window.winfo_screenheight()
        x = (screen_width - 1200) // 2
        y = (screen_height - 850) // 2
        search_window.geometry(f"1200x850+{x}+{y}")

        search_window.deiconify()
        search_window.lift()
        search_window.focus_force()
        if os.name == 'nt':
            search_window.attributes('-topmost', True)
            search_window.update()
            search_window.attributes('-topmost', False)

        def sort_column(tree, col, reverse):
            """Сортирует таблицу по указанному столбцу"""
            data = [(tree.set(item, col), item) for item in tree.get_children('')]
            if col == "duration" and type_var.get() == "channel":
                # Сортировка по числу видео как числу
                data.sort(key=lambda x: int(x[0]) if x[0].isdigit() else 0, reverse=reverse)
            elif col == "duration":
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
                            return int(dur)
                    except (ValueError, TypeError):
                        return 0
                data.sort(key=lambda x: parse_duration(x[0]), reverse=reverse)
            else:
                data.sort(key=lambda x: x[0].lower(), reverse=reverse)
            for index, (val, item) in enumerate(data):
                tree.move(item, '', index)
            tree.heading(col, command=lambda: sort_column(tree, col, not reverse))                        

        def copy_url():
            """Копирует URL выбранного видео в буфер обмена"""
            selected = tree.selection()[0] if tree.selection() else None
            if selected and selected in video_urls:
                url = video_urls[selected]
                pyperclip.copy(url)
                status_var.set("URL скопирован в буфер обмена")
                update_last_copy_time()  # Обновляем время последнего копирования
                log_message(f"INFO Ссылка скопирована из контекстного меню: {url}")

        def add_to_download_queue():
            """Добавляет выбранные видео в очередь загрузки"""
            selected_items = tree.selection()  # Получаем все выделенные элементы
            if not selected_items:
                status_var.set("Ничего не выбрано для добавления в очередь")
                return

            added_count = 0
            for selected in selected_items:
                if selected in video_urls:
                    url = video_urls[selected]
                    if add_to_queue(url):  # Добавляем в очередь только если URL ещё не добавлен
                        added_count += 1

            status_var.set(f"Добавлено в очередь загрузки: {added_count} видео")
            log_message(f"INFO Добавлено {added_count} видео в очередь загрузки")

            # Запускаем обработку очереди, если нет активной загрузки
            if not is_downloading:
                threading.Thread(target=process_queue).start()

        def open_in_browser():
            """Открывает выбранное видео в браузере"""
            selected = tree.selection()[0] if tree.selection() else None
            if selected and selected in video_urls:
                url = video_urls[selected]
                webbrowser.open(url)
                status_var.set("Открыто в браузере")          
        
        
        def save_api_key():
            api_key = api_key_var.get().strip()
            invidious_url = invidious_url_var.get().strip()

            settings["youtube_api_key"] = api_key
            settings["invidious_url"] = invidious_url

            save_settings(settings)
            messagebox.showinfo("Сохранение API Key", "API Key и настройки успешно сохранены")
            log_message("INFO API Key и настройки сохранены в настройках")
        
        def on_double_click(event):
            """Обрабатывает двойной клик по результату поиска"""
            item = tree.selection()[0] if tree.selection() else None
            if item:
                video_url = video_urls.get(item)
                if video_url:
                    log_message(f"Выбрано видео для загрузки: {video_url}")
                    # Добавляем в очередь загрузки
                    add_to_queue(video_url)
                    # messagebox.showinfo("Добавлено в очередь",
                    #                     f"Видео добавлено в очередь загрузки.\n\nURL: {video_url}")
                    log_message(f"INFO Видео добавлено в очередь загрузки: {video_url}")

                    # Запускаем обработку очереди, если нет активной загрузки
                    if not is_downloading:
                        threading.Thread(target=process_queue).start()
                        
                        
        
        # При закрытии окна очищаем ссылку на него
        def on_closing():
            try:
                settings["invidious_url"] = invidious_url_var.get().strip()
                settings["last_search_query"] = search_var.get().strip()
                settings["search_type"] = type_var.get().strip()
                settings["sort_order"] = order_var.get().strip()
                settings["max_results"] = max_results_var.get().strip()
                settings["use_alternative_api"] = use_alternative_api_var.get()
                settings["search_in_descriptions"] = search_in_descriptions_var.get()
                settings["advanced_search"] = advanced_search_var.get()
                settings["advanced_query"] = advanced_query_var.get().strip()
                settings["save_settings_on_exit"] = save_settings_var.get()
                settings["use_ytdlp_search"] = use_ytdlp_search_var.get()
                
                # Сохраняем результаты поиска (tree и video_urls)
                search_results = []
                for item in tree.get_children():
                    values = tree.item(item, 'values')
                    url = video_urls.get(item, '')
                    search_results.append({
                        'title': values[0],
                        'channel': values[1],
                        'duration': values[2],
                        'url': url
                    })
                settings["last_search_results"] = search_results
                
            except Exception as e:
                log_message(f"Ошибка при сохранении настроек: {e}")
            
            # Очищаем ссылку на окно поиска
            global search_window
            search_window.destroy()
            search_window = None
            
        search_var = tk.StringVar(value=settings.get("last_search_query", ""))
        api_key_var = tk.StringVar(value=settings.get("youtube_api_key", ""))
        invidious_url_var = tk.StringVar(value=settings.get("invidious_url", "http://localhost:3000"))
        type_var = tk.StringVar(value=settings.get("search_type", "video"))
        order_var = tk.StringVar(value=settings.get("sort_order", "relevance"))
        max_results_var = tk.StringVar(value=settings.get("max_results", "10"))
        use_alternative_api_var = tk.BooleanVar(value=settings.get("use_alternative_api", False))
        use_ytdlp_search_var = tk.BooleanVar(value=settings.get("use_ytdlp_search", False))
        search_in_descriptions_var = tk.BooleanVar(value=settings.get("search_in_descriptions", False))
        advanced_search_var = tk.BooleanVar(value=settings.get("advanced_search", False))
        advanced_query_var = tk.StringVar(value=settings.get("advanced_query", ""))
        save_settings_var = tk.BooleanVar(value=settings.get("save_settings_on_exit", False))
        # search_results = settings["last_search_results"]
        
        bind_var_to_settings(type_var, "search_type")
        bind_var_to_settings(order_var, "sort_order")
        bind_var_to_settings(max_results_var, "max_results")
        bind_var_to_settings(api_key_var, "youtube_api_key")
        bind_var_to_settings(invidious_url_var, "invidious_url")
        bind_var_to_settings(use_alternative_api_var, "use_alternative_api")
        bind_var_to_settings(search_in_descriptions_var, "search_in_descriptions")
        bind_var_to_settings(use_ytdlp_search_var, "use_ytdlp_search")
        bind_var_to_settings(advanced_search_var, "advanced_search")
        bind_var_to_settings(advanced_query_var, "advanced_query")
        bind_var_to_settings(save_settings_var, "save_settings_on_exit")
        bind_var_to_settings(search_var, "last_search_query")

        search_window.protocol("WM_DELETE_WINDOW", on_closing)
        
        # Словарь для управления состоянием элементов
        ui_state = {
            "search_in_descriptions_check": {"state": tk.NORMAL},
            "advanced_search_check": {"state": tk.NORMAL},
            "advanced_query_entry": {"state": tk.DISABLED},
            "use_alternative_api_check": {"state": tk.NORMAL},
            "use_ytdlp_search_check": {"state": tk.NORMAL},
        }

        # Функция для применения состояния к элементам интерфейса
        def apply_ui_state():
            search_in_descriptions_check.config(state=ui_state["search_in_descriptions_check"]["state"])
            advanced_search_check.config(state=ui_state["advanced_search_check"]["state"])
            advanced_query_entry.config(state=ui_state["advanced_query_entry"]["state"])
            use_alternative_api_check.config(state=ui_state["use_alternative_api_check"]["state"])
            use_ytdlp_search_check.config(state=ui_state["use_ytdlp_search_check"]["state"])

        # Функция для обновления состояния UI на основе текущих переменных
        def update_ui_state(*args):
            # Сбрасываем состояния в начальные значения
            ui_state.update({
                "search_in_descriptions_check": {"state": tk.NORMAL},
                "advanced_search_check": {"state": tk.NORMAL},
                "advanced_query_entry": {"state": tk.DISABLED},
                "use_alternative_api_check": {"state": tk.NORMAL},
                "use_ytdlp_search_check": {"state": tk.NORMAL},
            })

            # Правило 1: При типах "канал" или "плейлист" блокируем поиск по описаниям и расширенный поиск
            if type_var.get() in ["channel", "playlist"]:
                ui_state["search_in_descriptions_check"]["state"] = tk.DISABLED
                ui_state["advanced_search_check"]["state"] = tk.DISABLED
                search_in_descriptions_var.set(False)
                advanced_search_var.set(False)

            # Правило 2: "Поиск по описаниям" и "Расширенный поиск" взаимоисключающие
            if search_in_descriptions_var.get():
                ui_state["advanced_search_check"]["state"] = tk.DISABLED
                advanced_search_var.set(False)
            elif advanced_search_var.get():
                ui_state["search_in_descriptions_check"]["state"] = tk.DISABLED
                search_in_descriptions_var.set(False)

            # Правило 3: Поле "Запрос для поиска по описаниям" активно только при включенном "Расширенный поиск"
            if advanced_search_var.get():
                ui_state["advanced_query_entry"]["state"] = tk.NORMAL

            # Правило 4: "Искать альтернативным методом" и "Искать через yt-dlp" взаимоисключающие
            if use_alternative_api_var.get():
                ui_state["use_ytdlp_search_check"]["state"] = tk.DISABLED
                use_ytdlp_search_var.set(False)
            elif use_ytdlp_search_var.get():
                ui_state["use_alternative_api_check"]["state"] = tk.DISABLED
                use_alternative_api_var.set(False)

            # Применяем обновленные состояния
            apply_ui_state()

        # Привязываем обновление состояния ко всем переменным
        type_var.trace_add("write", update_ui_state)
        search_in_descriptions_var.trace_add("write", update_ui_state)
        advanced_search_var.trace_add("write", update_ui_state)
        use_alternative_api_var.trace_add("write", update_ui_state)
        use_ytdlp_search_var.trace_add("write", update_ui_state)

        # Создаем фреймы
        main_frame = ttk.Frame(search_window, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Фрейм для верхней части с полями поиска
        search_frame = ttk.LabelFrame(main_frame, text="Параметры поиска", padding=10)
        search_frame.pack(fill=tk.X, pady=(0, 10))

        # Первая строка - поисковый запрос
        query_frame = ttk.Frame(search_frame)
        query_frame.pack(fill=tk.X, pady=5)

        # ttk.Label(query_frame, text="Поисковый запрос:").pack(side=tk.LEFT, padx=(0, 5))
        query_frame = ttk.Frame(search_frame)
        query_frame.pack(fill=tk.X, pady=5)
        search_entry = configure_entry(
            parent=query_frame,
            textvariable=search_var,
            label_text="Поисковый запрос:",
            width=150,
            focus=True,
            entry_type="entry"  # или "autocomplete", если нужно
        )
        search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))

        options_frame = ttk.Frame(search_frame)
        options_frame.pack(fill=tk.X, pady=5)

        # Галочка "Сохранить настройки"
        # save_settings_var = tk.BooleanVar(value=settings.get("save_settings_on_exit", False))
        # log_message(f"DEBUG Инициализация галочки save_settings_on_exit: {save_settings_var.get()}")
        
        def on_save_settings_change(*args):
            # Обновляем только save_settings_on_exit
            update_single_setting("save_settings_on_exit", save_settings_var.get())
            settings["save_settings_on_exit"] = save_settings_var.get()  # Обновляем также в памяти
            log_message(f"DEBUG Изменено значение save_settings_on_exit: {save_settings_var.get()}")
        
        save_settings_var.trace_add("write", on_save_settings_change)
        
        save_settings_check = ttk.Checkbutton(
            options_frame,
            text="Сохранить настройки при выходе из программы",
            variable=save_settings_var
        )
        save_settings_check.pack(side=tk.LEFT, padx=(0, 10))

        # Устанавливаем фокус на поле ввода

        button_frame = tk.Frame(search_window)
        button_frame.pack(fill=tk.X, padx=5, pady=(0, 5))

        clear_button = tk.Button(button_frame, text="🗑 Очистить лог", command=clear_log)
        clear_button.pack(side=tk.RIGHT, padx=5)

        log_frame = ttk.Frame(search_window)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        global log_box  # обязательно, иначе создаётся локальная переменная
        log_box = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD, height=10, state='disabled')
        log_box.pack(fill=tk.BOTH, expand=True)

        # Цветовая разметка
        log_box.tag_config("info", foreground="black", background="#f0f0f0")
        log_box.tag_config("success", foreground="green", background="#eaffea")
        log_box.tag_config("warning", foreground="orange", background="#fff4d6")
        log_box.tag_config("error", foreground="red", background="#ffecec")
        log_box.tag_config("debug", foreground="blue", background="#e6f3ff")


        set_log_box(log_box)
        search_window.after(0, load_log_file)

        # Обработчик нажатия клавиш для вставки текст

        # Галочка "Поиск по описаниям"
        search_options_frame = ttk.Frame(search_frame)
        search_options_frame.pack(fill=tk.X, pady=5)

        search_in_descriptions_check = ttk.Checkbutton(
            search_options_frame,
            text="Поиск по описаниям",
            variable=search_in_descriptions_var,
            )
        search_in_descriptions_check.pack(side=tk.LEFT, padx=(0, 10))

        # Галочка "Искать альтернативным методом"
        use_alternative_api_check = ttk.Checkbutton(
            search_options_frame,
            text="Искать альтернативным методом (Invidious API)",
            variable=use_alternative_api_var
        )
        use_alternative_api_check.pack(side=tk.LEFT, padx=(0, 10))
        
        use_ytdlp_search_check = ttk.Checkbutton(
            search_options_frame,
            text="Искать через yt-dlp",
            variable=use_ytdlp_search_var,
            )
        use_ytdlp_search_check.pack(side=tk.LEFT, padx=(0, 10))

        # Галочка "Расширенный поиск"
        advanced_search_frame = ttk.Frame(search_frame)
        advanced_search_frame.pack(fill=tk.X, pady=5)

        advanced_search_check = ttk.Checkbutton(
            advanced_search_frame,
            text="Расширенный поиск по описаниям",
            variable=advanced_search_var,
        )
        advanced_search_check.pack(side=tk.LEFT, padx=(0, 10))

        # ttk.Label(advanced_search_frame, text="Запрос для поиска по описаниям:").pack(side=tk.LEFT, padx=(0, 5))
        advanced_query_entry = configure_entry(
            parent=advanced_search_frame,
            textvariable=advanced_query_var,
            label_text="Запрос для поиска по описаниям:",
            width=50,
            focus=False,
            entry_type="entry"
        )
        advanced_query_entry.config(state=tk.DISABLED)
        advanced_query_entry.pack(side=tk.LEFT, padx=(0, 10))

        # Функция для переключения состояния галочек
        # Тип контента
        ttk.Label(options_frame, text="Тип:").pack(side=tk.LEFT, padx=(0, 5))
        type_combo = ttk.Combobox(options_frame, textvariable=type_var, width=15,
                                  values=["video", "channel", "playlist"])
        type_combo.pack(side=tk.LEFT, padx=(0, 10))

        # Функция для обновления состояния галочки поиска по описаниям
        def update_search_in_descriptions_state(*args):
            selected_type = type_var.get()
            if selected_type in ["channel", "playlist"]:
                search_in_descriptions_var.set(False)
                search_in_descriptions_check.config(state=tk.DISABLED)
                advanced_search_var.set(False)
                advanced_search_check.config(state=tk.DISABLED)
                advanced_query_entry.config(state=tk.DISABLED)
            else:
                search_in_descriptions_check.config(state=tk.NORMAL)
                advanced_search_check.config(state=tk.NORMAL)
                

        # Привязываем функцию к изменению типа контента
        type_var.trace_add("write", update_search_in_descriptions_state)

        # Порядок сортировки
        ttk.Label(options_frame, text="Сортировка:").pack(side=tk.LEFT, padx=(0, 5))
        order_combo = ttk.Combobox(options_frame, textvariable=order_var, width=15,
                                   values=["relevance", "date", "rating", "viewCount", "title"])
        order_combo.pack(side=tk.LEFT, padx=(0, 10))

        # Максимальное количество результатов
        ttk.Label(options_frame, text="Результатов:").pack(side=tk.LEFT, padx=(0, 5))
        max_results_combo = ttk.Combobox(options_frame, textvariable=max_results_var, width=10,
                                         values=["10", "20", "30", "40", "50", "100", "200", "500", "1000"])
        max_results_combo.pack(side=tk.LEFT, padx=(0, 10))

        # API Key
        api_frame = ttk.Frame(search_frame)
        api_frame.pack(fill=tk.X, pady=5)

        # ttk.Label(api_frame, text="API Key:").pack(side=tk.LEFT, padx=(0, 5))
        api_key_entry = configure_entry(
            parent=api_frame,
            textvariable=api_key_var,
            label_text="API Key:",
            width=50,
            focus=False,
            entry_type="entry"
        )
        api_key_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))


        # Invidious API URL
        # ttk.Label(api_frame, text="Invidious URL:").pack(side=tk.LEFT, padx=(0, 5))
        invidious_url_entry = configure_entry(
            parent=api_frame,
            textvariable=invidious_url_var,
            label_text="Invidious URL:",
            width=30,
            focus=False,
            entry_type="entry"
        )
        invidious_url_entry.pack(side=tk.LEFT, padx=(0, 10))

        # Фрейм для кнопок управления
        button_frame = ttk.Frame(search_frame)
        button_frame.pack(fill=tk.X, pady=(10, 5))
        


        ttk.Button(button_frame, text="Как получить API Key?", command=show_api_help).pack(side=tk.RIGHT, padx=5)

        def run_search_and_save():
            log_message(f"DEBUG: Тип tree перед вызовом perform_search: {type(tree)}")
            if not isinstance(tree, ttk.Treeview):
                log_message("ERROR: tree не является Treeview")
                messagebox.showerror("Ошибка", "Внутренняя ошибка: неверный объект таблицы")
                return
            
            perform_search(
                    search_var, type_var, order_var, max_results_var,
                    api_key_var, invidious_url_var, use_alternative_api_var,
                    use_ytdlp_search_var, search_in_descriptions_var,
                    advanced_search_var, advanced_query_var, tree,
                    video_urls, status_var, video_descriptions, settings,
                    progress_var, search_window
                )
            
            # Обновляем settings["last_search_results"] после поиска
            search_results = []
            for item in tree.get_children():
                values = tree.item(item, 'values')
                url = video_urls.get(item, '')
                search_results.append({
                    'title': values[0],
                    'channel': values[1],
                    'duration': values[2],
                    'url': url
                })
            settings["last_search_results"] = search_results
            log_message(f"DEBUG Обновлено last_search_results: {len(search_results)} элементов")

        # Заменяем кнопку
        search_button = ttk.Button(search_frame, text="Искать", 
            command=lambda: threading.Thread(target=run_search_and_save).start()
        )
        search_button.pack(side=tk.LEFT, padx=5)

        # Фрейм для результатов поиска
        results_frame = ttk.LabelFrame(main_frame, text="Результаты поиска", padding=10)
        results_frame.pack(fill=tk.BOTH, expand=True)

        # Создаем контейнер с прокруткой для результатов
        container = ttk.Frame(results_frame)
        container.pack(fill=tk.BOTH, expand=True)

        # Полоса прокрутки
        scrollbar = ttk.Scrollbar(container)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Прогресс-бар
        progress_frame = ttk.Frame(search_frame)
        progress_frame.pack(fill=tk.X, pady=(5, 0))
        progress_var = tk.DoubleVar()
        progress_bar = ttk.Progressbar(progress_frame, variable=progress_var, maximum=100, length=300)
        progress_bar.pack(fill=tk.X)

        # Поле для вывода сообщений
        status_var = tk.StringVar(value="Введите поисковый запрос и нажмите 'Искать'")
        status_label = ttk.Label(results_frame, textvariable=status_var, foreground="blue", font=("Arial", 12))
        status_label.pack(anchor=tk.W, pady=(5, 0))

        # Treeview для отображения результатов
        columns = ("title", "channel", "duration")
        tree = ttk.Treeview(container, columns=columns, show="headings", yscrollcommand=scrollbar.set)

        # Привязываем полосу прокрутки к Treeview
        scrollbar.config(command=tree.yview)

        # Настройка заголовков колонок с сортировкой
        tree.heading("title", text="Название", command=lambda: sort_column(tree, "title", False))
        tree.heading("channel", text="Канал", command=lambda: sort_column(tree, "channel", False))
        tree.heading("duration", text="Длительность", command=lambda: sort_column(tree, "duration", False))

        # Настройка ширины колонок
        tree.column("title", width=500, anchor=tk.W)
        tree.column("channel", width=200, anchor=tk.W)
        tree.column("duration", width=100, anchor=tk.CENTER)

        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Словарь для хранения URL видео
        video_urls = {}        
        
        last_results = settings.get("last_search_results", [])
        if last_results:
            for result in last_results:
                item_id = tree.insert('', tk.END, values=(result['title'], result['channel'], result['duration']))
                video_urls[item_id] = result['url']
            status_var.set(f"Восстановлено результатов: {len(last_results)}")
            status_label.config(foreground="green")
            log_message(f"INFO Восстановлено {len(last_results)} результатов поиска")



        # Определение контекстного меню
        # В search_youtube_videos, где создаётся контекстное меню
        context_menu = tk.Menu(tree, tearoff=0)
        context_menu.add_command(label="Копировать URL", command=copy_url)
        context_menu.add_command(label="Добавить в очередь загрузки", command=add_to_download_queue)
        context_menu.add_command(label="Открыть в браузере", command=open_in_browser)
        context_menu.add_command(label="Показать описание", command=lambda: show_description(tree, video_urls, search_window, 
                                                            status_var, status_label, video_descriptions))
        context_menu.add_command(label="Просмотреть видео", command=lambda: play_video(tree, video_urls, search_window, 
                                                    status_var, status_label, video_descriptions))
        # Привязываем контекстное меню к правому клику
        tree.bind("<Button-3>", lambda event: context_menu.post(event.x_root, event.y_root))

        # Обработка двойного клика по результату поиска


        # Привязываем двойной клик
        tree.bind("<Double-1>", on_double_click)

        
        bottom_button_frame = ttk.Frame(main_frame)
        bottom_button_frame.pack(fill=tk.X, pady=10)

        ttk.Button(bottom_button_frame, text="Загрузить выбранное", command=add_to_download_queue).pack(side=tk.RIGHT, padx=5)
        ttk.Button(bottom_button_frame, text="Закрыть", command=search_window.destroy).pack(side=tk.RIGHT, padx=5)
        
        update_ui_state()

        # search_window.mainloop()

    except Exception as e:
        log_message(f"ERROR Ошибка в окне поиска YouTube: {e}")
        log_message(f"Трассировка: {traceback.format_exc()}")
        messagebox.showerror("Ошибка", f"Произошла ошибка: {e}")
        if search_window is not None and search_window.winfo_exists():
            search_window.destroy()
        search_window = None