import os
import re
import threading
import tkinter as tk
import tkinter
from tkinter import messagebox, scrolledtext, Tk, ttk, BOTH
import traceback
import webbrowser
import pyperclip
import requests
from ttkwidgets.autocomplete import AutocompleteEntry
from clipboard_utils import update_last_copy_time

from config import ensure_invidious_running, format_duration, format_invidious_duration, initialize_settings, save_settings, is_downloading
from config import is_downloading
from database import connect_to_database, insert_description, search_in_database, is_connected, clear_descriptions_table
from logger import clear_log, load_log_file, log_message, set_log_box
from queues import add_to_queue, process_queue
from fetch import fetch_description_with_bs, fetch_videos_from_invidious
from fetch import fetch_videos_from_youtube_api

settings = initialize_settings()
search_window = None

def prepare_tsquery(text):
    # Преобразуем в tsquery формат: концерт & 1991
    words = re.findall(r'\w+', text)
    return ' & '.join(words)

def search_youtube_videos():
    """Отображает окно для поиска видео через YouTube API"""
    global search_window  # Используем глобальную переменную

    # Проверяем, существует ли окно и активно ли оно
    if search_window is not None and search_window.winfo_exists():
        # Если окно уже существует, поднимаем его на передний план
        search_window.deiconify()  # Делаем окно видимым, если оно было свернуто
        search_window.lift()      # Поднимаем окно поверх других
        search_window.focus_force()  # Устанавливаем фокус
        if os.name == 'nt':       # Дополнительно для Windows
            search_window.attributes('-topmost', True)
            search_window.update()
            search_window.attributes('-topmost', False)
        return  # Выходим из функции, не создавая новое окно

    try:
        # Создаем новое окно, только если его еще нет
        search_window = tk.Tk()
        search_window.title("Расширенный поиск YouTube")
        search_window.geometry("1200x850")

        # Центрируем окно
        search_window.update_idletasks()
        screen_width = search_window.winfo_screenwidth()
        screen_height = search_window.winfo_screenheight()
        x = (screen_width - 1200) // 2
        y = (screen_height - 850) // 2
        search_window.geometry(f"1200x850+{x}+{y}")

        # Делаем окно видимым и выводим на передний план
        search_window.deiconify()
        search_window.lift()
        search_window.focus_force()

        if os.name == 'nt':
            search_window.attributes('-topmost', True)
            search_window.update()
            search_window.attributes('-topmost', False)

        # При закрытии окна очищаем ссылку на него
        def on_closing():
            global search_window
            search_window.destroy()
            search_window = None  # Сбрасываем переменную после закрытия

        search_window.protocol("WM_DELETE_WINDOW", on_closing)

        # Создаем фреймы
        main_frame = ttk.Frame(search_window, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Фрейм для верхней части с полями поиска
        search_frame = ttk.LabelFrame(main_frame, text="Параметры поиска", padding=10)
        search_frame.pack(fill=tk.X, pady=(0, 10))

        # Первая строка - поисковый запрос
        query_frame = ttk.Frame(search_frame)
        query_frame.pack(fill=tk.X, pady=5)

        ttk.Label(query_frame, text="Поисковый запрос:").pack(side=tk.LEFT, padx=(0, 5))
        search_var = tk.StringVar(value=settings.get("last_search_query", ""))
        autocomplete_list = []  # Пустой список для автозаполнения
        search_entry = AutocompleteEntry(query_frame, textvariable=search_var, width=50, completevalues=autocomplete_list)
        search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))
        search_entry.focus()  # Устанавливаем фокус на поле ввода

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

        set_log_box(log_box)
        load_log_file()

        paste_in_progress = False

        # Обработчик нажатия клавиш для вставки текста
        def on_key_press(event):
            nonlocal paste_in_progress
            # Проверяем комбинацию Ctrl/Cmd + V (код 86)
            if (event.state in (4, 8, 12) and event.keycode == 86):  # 4=Ctrl, 8=Command
                # Отменяем стандартную вставку для английской раскладки, используем нашу собственную функцию
                if not paste_in_progress:
                    paste_in_progress = True
                    search_window.after(10, lambda: handle_paste())
                    return "break"  # Отменяем стандартную обработку
            # Обработка Shift+Insert (код 118)
            elif event.keysym == "Insert" and event.state & 0x0001:
                if not paste_in_progress:
                    paste_in_progress = True
                    search_window.after(10, lambda: handle_paste())
                    return "break"  # Отменяем стандартную обработку

        def handle_paste():
            nonlocal paste_in_progress
            try:
                clipboard_text = search_window.clipboard_get()
                # Сохраняем текущее положение курсора
                cursor_pos = search_entry.index(tk.INSERT)

                # Получаем текущий текст из поля
                current_text = search_var.get()

                # Вставляем текст из буфера в текущую позицию
                new_text = current_text[:cursor_pos] + clipboard_text + current_text[cursor_pos:]
                search_var.set(new_text)

                # Перемещаем курсор после вставленного текста
                search_entry.icursor(cursor_pos + len(clipboard_text))
            except tk.TclError:
                log_message("Буфер обмена пуст")
            finally:
                paste_in_progress = False

        # Контекстное меню
        def show_context_menu(event):
            menu = tk.Menu(search_entry, tearoff=0)
            menu.add_command(label="Вставить", command=handle_paste)  # Исправлено: теперь без аргумента
            menu.add_command(label="Копировать",
                            command=lambda: search_entry.event_generate("<<Copy>>"))
            menu.tk_popup(event.x_search_window, event.y_search_window)

        # Привязка событий
        search_entry.bind("<Key>", on_key_press)  # Обработка всех клавиш
        search_entry.bind("<Button-3>", show_context_menu)  # Правый клик

        # Фрейм для дополнительных параметров поиска
        options_frame = ttk.Frame(search_frame)
        options_frame.pack(fill=tk.X, pady=5)

        # Фрейм для опций поиска
        search_options_frame = ttk.Frame(search_frame)
        search_options_frame.pack(fill=tk.X, pady=5)

        # Добавляем галочку "Поиск по описаниям"
        search_in_descriptions_var = tk.BooleanVar(value=False)
        search_in_descriptions_check = ttk.Checkbutton(
            search_options_frame,
            text="Поиск по описаниям",
            variable=search_in_descriptions_var,
            command=lambda: toggle_search_options(search_in_descriptions_var.get(), advanced_search_var.get())
        )
        search_in_descriptions_check.pack(side=tk.LEFT, padx=(0, 10))

        # Добавляем новую галочку "Искать альтернативным методом"
        use_alternative_api_var = tk.BooleanVar(value=False)
        use_alternative_api_check = ttk.Checkbutton(
            search_options_frame,
            text="Искать альтернативным методом (Invidious API)",
            variable=use_alternative_api_var
        )
        use_alternative_api_check.pack(side=tk.LEFT, padx=(0, 10))

        # Фрейм для расширенного поиска
        advanced_search_frame = ttk.Frame(search_frame)
        advanced_search_frame.pack(fill=tk.X, pady=5)

        # Галочка "Расширенный поиск по описаниям"
        advanced_search_var = tk.BooleanVar(value=False)
        advanced_search_check = ttk.Checkbutton(
            advanced_search_frame,
            text="Расширенный поиск по описаниям",
            variable=advanced_search_var,
            command=lambda: toggle_search_options(search_in_descriptions_var.get(), advanced_search_var.get())
        )
        advanced_search_check.pack(side=tk.LEFT, padx=(0, 10))

        # Функция для переключения состояния галочек
        def toggle_search_options(search_in_descriptions, advanced_search):
            if search_in_descriptions:
                advanced_search_var.set(False)
                advanced_search_check.config(state=tk.DISABLED)
                advanced_query_entry.config(state=tk.DISABLED)
            else:
                advanced_search_check.config(state=tk.NORMAL)
                if not advanced_search:
                    advanced_query_entry.config(state=tk.DISABLED)
            
            if advanced_search:
                search_in_descriptions_var.set(False)
                search_in_descriptions_check.config(state=tk.DISABLED)
                advanced_query_entry.config(state=tk.NORMAL)
            else:
                search_in_descriptions_check.config(state=tk.NORMAL)
                if not search_in_descriptions:
                    advanced_query_entry.config(state=tk.DISABLED)

        # Поле для ввода запроса
        ttk.Label(advanced_search_frame, text="Запрос для поиска по описаниям:").pack(side=tk.LEFT, padx=(0, 5))
        advanced_query_var = tk.StringVar()
        advanced_query_entry = ttk.Entry(advanced_search_frame, textvariable=advanced_query_var, width=50, state=tk.DISABLED)
        advanced_query_entry.pack(side=tk.LEFT, padx=(0, 10))

        # Тип контента
        ttk.Label(options_frame, text="Тип:").pack(side=tk.LEFT, padx=(0, 5))
        type_var = tk.StringVar(value="video")
        type_combo = ttk.Combobox(options_frame, textvariable=type_var, width=15,
                                  values=["video", "channel", "playlist"])
        type_combo.pack(side=tk.LEFT, padx=(0, 10))

        # Функция для обновления состояния галочки поиска по описаниям
        def update_search_in_descriptions_state(*args):
            selected_type = type_var.get()
            if selected_type in ["channel", "playlist"]:
                search_in_descriptions_var.set(False)
                search_in_descriptions_check.config(state=tk.DISABLED)
            else:
                search_in_descriptions_check.config(state=tk.NORMAL)

        # Привязываем функцию к изменению типа контента
        type_var.trace_add("write", update_search_in_descriptions_state)

        # Порядок сортировки
        ttk.Label(options_frame, text="Сортировка:").pack(side=tk.LEFT, padx=(0, 5))
        order_var = tk.StringVar(value="relevance")
        order_combo = ttk.Combobox(options_frame, textvariable=order_var, width=15,
                                   values=["relevance", "date", "rating", "viewCount", "title"])
        order_combo.pack(side=tk.LEFT, padx=(0, 10))

        # Максимальное количество результатов
        ttk.Label(options_frame, text="Результатов:").pack(side=tk.LEFT, padx=(0, 5))
        max_results_var = tk.StringVar(value="10")
        max_results_combo = ttk.Combobox(options_frame, textvariable=max_results_var, width=10,
                                         values=["10", "20", "30", "40", "50", "100", "200", "500", "1000"])
        max_results_combo.pack(side=tk.LEFT, padx=(0, 10))

        # API Key
        api_frame = ttk.Frame(search_frame)
        api_frame.pack(fill=tk.X, pady=5)

        ttk.Label(api_frame, text="API Key:").pack(side=tk.LEFT, padx=(0, 5))
        api_key_var = tk.StringVar(value=settings.get("youtube_api_key", ""))
        api_key_entry = ttk.Entry(api_frame, textvariable=api_key_var, width=50)
        api_key_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))

        # Invidious API URL
        ttk.Label(api_frame, text="Invidious URL:").pack(side=tk.LEFT, padx=(0, 5))
        invidious_url_var = tk.StringVar(value=settings.get("invidious_url", "http://localhost:3000"))
        invidious_url_entry = ttk.Entry(api_frame, textvariable=invidious_url_var, width=30)
        invidious_url_entry.pack(side=tk.LEFT, padx=(0, 10))

        # Фрейм для кнопок управления
        button_frame = ttk.Frame(search_frame)
        button_frame.pack(fill=tk.X, pady=(10, 5))

        # Функция для сохранения API ключа
        def save_api_key():
            api_key = api_key_var.get().strip()
            invidious_url = invidious_url_var.get().strip()

            if api_key:
                settings["youtube_api_key"] = api_key

            if invidious_url:
                settings["invidious_url"] = invidious_url

                save_settings(settings)
                messagebox.showinfo("Сохранение API Key", "API Key успешно сохранен")
                log_message("API Key сохранен в настройках")
            else:
                messagebox.showwarning("Сохранение API Key", "Введите API Key для сохранения")

        # Кнопка для сохранения API ключа
        ttk.Button(button_frame, text="Сохранить API Key", command=save_api_key).pack(side=tk.RIGHT, padx=5)

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

        ttk.Button(button_frame, text="Как получить API Key?", command=show_api_help).pack(side=tk.RIGHT, padx=5)

        def format_date(date_str):
            """Преобразует ISO 8601 дату в более читаемый формат"""
            try:
                # Дата в формате 2021-05-20T15:30:45Z
                date_part = date_str.split('T')[0]  # Берем только часть с датой
                year, month, day = date_part.split('-')
                return f"{day}.{month}.{year}"
            except Exception as e:
                log_message(f"Ошибка форматирования даты: {e}")
                return date_str

        # Функция для форматирования числа просмотров
        def format_views(views_count):
            """Форматирует число просмотров в более читабельный вид"""
            try:
                count = int(views_count)
                if count < 1000:
                    return str(count)
                elif count < 1000000:
                    return f"{count/1000:.1f}K"
                else:
                    return f"{count/1000000:.1f}M"
            except Exception as e:
                log_message(f"Ошибка форматирования просмотров: {e}")
                return views_count

        # Кнопка Поиск
        # search_button = ttk.Button(button_frame, text="Искать", command=lambda: [log_message("Кнопка 'Искать' нажата"), threading.Thread(target=perform_search).start()])
        # search_button.pack(side=tk.LEFT, padx=5)

        search_button = ttk.Button(button_frame, text="Искать", command=lambda: threading.Thread(target=perform_search).start())
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

        # Поле для вывода сообщений
        status_var = tk.StringVar(value="Введите поисковый запрос и нажмите 'Искать'")
        status_label = ttk.Label(results_frame, textvariable=status_var, foreground="blue")
        status_label.pack(anchor=tk.W, pady=(5, 0))

        # Treeview для отображения результатов
        columns = ("title", "channel", "duration")
        tree = ttk.Treeview(container, columns=columns, show="headings", yscrollcommand=scrollbar.set)

        # Настройка заголовков колонок с сортировкой
        tree.heading("title", text="Название", command=lambda: sort_column(tree, "title", False))
        tree.heading("channel", text="Канал", command=lambda: sort_column(tree, "channel", False))
        tree.heading("duration", text="Длительность", command=lambda: sort_column(tree, "duration", False))

        # Настройка ширины колонок
        tree.column("title", width=500, anchor=tk.W)
        tree.column("channel", width=200, anchor=tk.W)
        tree.column("duration", width=100, anchor=tk.CENTER)

        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=tree.yview)

        # Словарь для хранения URL видео
        video_urls = {}

        def sort_column(tree, col, reverse):
            """Сортирует таблицу по указанному столбцу"""
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
                            return int(dur)
                    except (ValueError, TypeError):
                        return 0
                data.sort(key=lambda x: parse_duration(x[0]), reverse=reverse)
            else:
                data.sort(key=lambda x: x[0].lower(), reverse=reverse)
            for index, (val, item) in enumerate(data):
                tree.move(item, '', index)
            tree.heading(col, command=lambda: sort_column(tree, col, not reverse))

        # Функции для контекстного меню
# Глобальный флаг для отключения мониторинга буфера обмена

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

        video_descriptions = {}

        def decode_html_entities(text):
            """Декодирует HTML-сущности в обычный текст"""
            import html
            if text:
                return html.unescape(text)
            return text

        def search_via_invidious(query):
            """Выполняет поиск видео через Invidious API"""
            # query = search_var.get().strip()
            if not query:
                status_var.set("Введите поисковый запрос")
                return None

            invidious_url = invidious_url_var.get().strip()
            if not invidious_url:
                status_var.set("Введите URL Invidious сервера")
                return None

            # Убираем trailing slash если есть
            if invidious_url.endswith('/'):
                invidious_url = invidious_url[:-1]

            max_results = int(max_results_var.get().strip())
            search_type = type_var.get().strip()
            sort_by = order_var.get().strip()
            search_in_descriptions = search_in_descriptions_var.get()

            # Преобразуем параметры сортировки в формат Invidious
            sort_map = {
                "relevance": "relevance",  # По релевантности
                "date": "date",            # По дате
                "rating": "rating",        # По рейтингу
                "viewCount": "views",      # По просмотрам
                "title": "alphabetical"    # По алфавиту
            }

            invidious_sort = sort_map.get(sort_by, "relevance")

            # Преобразуем тип поиска в формат Invidious
            type_map = {
                "video": "video",
                "channel": "channel",
                "playlist": "playlist",
            }

            invidious_type = type_map.get(search_type, "video")

            try:
                # Если используется локальный сервер Invidious — проверяем, запущен ли он
                if "localhost" in invidious_url or "127.0.0.1" in invidious_url:
                    ensure_invidious_running()

                api_endpoint = f"{invidious_url}/api/v1/search"
                params = {
                    'type': invidious_type,
                    'sort_by': invidious_sort,
                    'page': 1
                }

                params['q'] = query

                all_results = []
                while len(all_results) < max_results:
                    response = requests.get(api_endpoint, params=params)
                    if response.status_code != 200:
                        log_message(f"Ошибка Invidious API: {response.status_code}")
                        break

                    page_results = response.json()
                    if not page_results:
                        break

                    all_results.extend(page_results)
                    params['page'] += 1

                    # Проверяем, достигли ли мы лимита
                    if len(all_results) >= max_results:
                        break

                # Ограничиваем количество результатов
                results = all_results[:max_results] if len(all_results) > max_results else all_results

                # Обрабатываем результаты в зависимости от типа поиска
                filtered_items = []
                video_descriptions = {}  # Словарь для хранения описаний видео
                seen_urls = set()  # Множество для отслеживания уже добавленных URL
                seen_ids = set()   # Множество для отслеживания уже добавленных ID

                for item in results:
                    try:
                        if search_type == 'video':
                            if item.get('type') != 'video':
                                continue
                            video_id = item.get('videoId')
                            if video_id in seen_ids:  # Проверяем ID видео
                                continue
                            seen_ids.add(video_id)
                            title = decode_html_entities(item.get('title', 'Без названия'))
                            channel = decode_html_entities(item.get('author', 'Неизвестный канал'))
                            duration = format_invidious_duration(item.get('lengthSeconds', 0))
                            video_url = f"https://www.youtube.com/watch?v={video_id}"
                            
                            # Загружаем описание через BeautifulSoup
                            description = fetch_description_with_bs(video_url)
                            video_descriptions[video_id] = description
                        elif search_type == 'playlist':
                            if item.get('type') != 'playlist':
                                continue
                            playlist_id = item.get('playlistId')
                            if playlist_id in seen_ids:  # Проверяем ID плейлиста
                                continue
                            seen_ids.add(playlist_id)
                            title = decode_html_entities(item.get('title', 'Без названия'))
                            channel = decode_html_entities(item.get('author', 'Неизвестный канал'))
                            duration = 'Плейлист'
                            video_url = f"https://www.youtube.com/playlist?list={playlist_id}"
                        elif search_type == 'channel':
                            if item.get('type') != 'channel':
                                continue
                            channel_id = item.get('authorId')
                            if channel_id in seen_ids:  # Проверяем ID канала
                                continue
                            seen_ids.add(channel_id)
                            title = decode_html_entities(item.get('title', 'Без названия'))
                            channel = decode_html_entities(item.get('author', 'Неизвестный канал'))
                            duration = 'Канал'
                            video_url = f"https://www.youtube.com/channel/{channel_id}"

                        # Если включен поиск по описаниям, фильтруем результаты
                        if search_in_descriptions:
                            description = video_descriptions.get(video_id, '')
                            if query.lower() not in description.lower():
                                continue

                        # Проверяем, не был ли уже добавлен этот URL
                        if video_url not in seen_urls:
                            seen_urls.add(video_url)
                            filtered_items.append(item)
                            
                    except Exception as e:
                        log_message(f"ERROR Ошибка при обработке результата Invidious API: {e}")
                        log_message(f"DEBUG Данные элемента: {item}")

                log_message(f"INFO Всего найдено результатов: {len(results)}")
                log_message(f"INFO После фильтрации осталось: {len(filtered_items)}")
                log_message(f"INFO Добавлено {len(filtered_items)} элементов в таблицу (Invidious API)")
                status_var.set(f"Найдено результатов: {len(filtered_items)}")

                return filtered_items

            except Exception as e:
                log_message(f"Ошибка при поиске через Invidious API: {e}")
                log_message(f"Трассировка: {traceback.format_exc()}")
                status_var.set(f"Ошибка: {str(e)}")
                return None

        def search_via_youtube_api():
            """Выполняет поиск видео через официальный YouTube API с пагинацией"""

            query = search_var.get().strip()
            api_key = api_key_var.get().strip()

            if not query:
                status_var.set("Введите поисковый запрос")
                return None

            if not api_key:
                status_var.set("Введите API Key для поиска")
                return None

            status_var.set("Выполняется поиск...")
            log_message(f"INFO Поиск по запросу: {query}")

            # Параметры поиска
            search_type = type_var.get().strip()
            order = order_var.get().strip()
            max_results = int(max_results_var.get().strip())
            search_in_descriptions = search_in_descriptions_var.get()

            try:
                base_url = "https://www.googleapis.com/youtube/v3/search"
                params = {
                    'key': api_key,
                    'part': 'snippet',
                    'maxResults': min(50, max_results),  # Максимум 50 за один запрос
                    'type': search_type,
                    'order': order
                }

                params['q'] = query

                all_items = []
                next_page_token = None

                # Делаем запросы до тех пор, пока не достигнем максимального количества результатов
                # или пока API не вернет пустую страницу
                while len(all_items) < max_results:
                    if next_page_token:
                        params['pageToken'] = next_page_token

                    response = requests.get(base_url, params=params)
                    if response.status_code != 200:
                        log_message(f"Ошибка API: {response.status_code}")
                        break

                    data = response.json()
                    items = data.get('items', [])
                    all_items.extend(items)

                    # Проверяем, достигли ли мы максимального количества результатов
                    if len(all_items) >= max_results or not data.get('nextPageToken'):
                        break

                    # Получаем токен следующей страницы
                    next_page_token = data.get('nextPageToken')

                    log_message(f"Получен токен следующей страницы: {next_page_token}")

                log_message(f"INFO Всего найдено результатов: {len(all_items)}")

                # Обрабатываем результаты в зависимости от типа поиска
                filtered_items = []
                video_descriptions = {}  # Словарь для хранения описаний видео

                # Собираем результаты без отображения в таблице
                for item in all_items:
                    video_id = item['id']['videoId']
                    title = decode_html_entities(item['snippet']['title'])
                    channel = decode_html_entities(item['snippet']['channelTitle'])
                    video_url = f"https://www.youtube.com/watch?v={video_id}"
                    
                    # Если включен поиск по описаниям, загружаем описание
                    if search_in_descriptions:
                        description = item['snippet'].get('description', '')
                        video_descriptions[video_id] = description
                        # Фильтруем по описанию
                        if query.lower() not in description.lower():
                            continue
                    
                    filtered_items.append({
                        'id': {'videoId': video_id},
                        'snippet': {
                            'title': title,
                            'channelTitle': channel
                        },
                        'url': video_url
                    })

                log_message(f"INFO После фильтрации осталось: {len(filtered_items)}")
                log_message(f"INFO Добавлено {len(filtered_items)} элементов в таблицу (YouTube API)")

                return {'items': filtered_items, 'video_stats': {}}

            except Exception as e:
                log_message(f"ERROR Ошибка при выполнении поиска через YouTube API: {e}")
                log_message(f"Трассировка: {traceback.format_exc()}")
                status_var.set(f"Ошибка: {str(e)}")
                return None

        def show_description():
            """Показывает описание выбранного видео в окне с возможностью копирования"""
            selected = tree.selection()[0] if tree.selection() else None
            if selected and selected in video_urls:
                video_url = video_urls[selected]
                video_id = video_url.split('v=')[1]

                # Создаем окно сразу, но показываем его только после загрузки описания
                desc_window = tk.Toplevel(search_window)
                desc_window.title("Описание видео")
                desc_window.geometry("600x400")
                desc_window.withdraw()  # Скрываем окно до загрузки описания

                # Центрируем окно
                desc_window.update_idletasks()
                screen_width = desc_window.winfo_screenwidth()
                screen_height = desc_window.winfo_screenheight()
                x = (screen_width - 600) // 2
                y = (screen_height - 400) // 2
                desc_window.geometry(f"600x400+{x}+{y}")

                # Делаем окно модальным
                desc_window.transient(search_window)
                desc_window.grab_set()

                # Создаем текстовое поле с прокруткой
                frame = ttk.Frame(desc_window, padding=10)
                frame.pack(fill=tk.BOTH, expand=True)

                # Отображаем URL видео сверху
                url_label = ttk.Label(frame, text=f"URL видео: {video_url}")
                url_label.pack(anchor=tk.W, pady=(0, 10))

                # Текстовое поле с прокруткой
                text_frame = ttk.Frame(frame)
                text_frame.pack(fill=tk.BOTH, expand=True)

                scrollbar = ttk.Scrollbar(text_frame)
                scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

                text_widget = tk.Text(text_frame, wrap=tk.WORD, yscrollcommand=scrollbar.set)
                text_widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

                scrollbar.config(command=text_widget.yview)

                # Проверяем, загружено ли описание
                if video_id not in video_descriptions or video_descriptions[video_id] == "Описание будет загружено при запросе":
                    status_var.set("Загрузка описания...")
                    # Проверяем, какой метод поиска используется
                    use_alternative = use_alternative_api_var.get()
                    if use_alternative:
                        # Загружаем описание через BeautifulSoup
                        description = fetch_description_with_bs(video_url)
                        log_message(f"Описание загружено через BS: {description}")
                    else:
                        # Загружаем описание через YouTube API
                        api_key = api_key_var.get().strip()
                        videos_url = "https://www.googleapis.com/youtube/v3/videos"
                        videos_params = {
                            'key': api_key,
                            'part': 'snippet',
                            'id': video_id
                        }
                        response = requests.get(videos_url, params=videos_params)

                        if response.status_code == 200:
                            video_data = response.json()
                            description = video_data['items'][0]['snippet']['description']
                            log_message(f"Описание загружено через Youtube API")

                        else:
                            description = "Описание недоступно (ошибка загрузки)"
                            log_message(f"Ошибка при загрузке описания через YouTube API: {response.status_code}")

                    # Сохраняем описание
                    video_descriptions[video_id] = description
                else:
                    description = video_descriptions[video_id]

                # Вставляем описание в текстовое поле
                text_widget.insert(tk.END, description)

                # Делаем текстовое поле только для чтения, но с возможностью выделения и копирования
                text_widget.config(state=tk.DISABLED)

                # Функция для копирования всего текста
                def copy_all_text():
                    desc_window.clipboard_clear()
                    desc_window.clipboard_append(description)
                    status_label.config(text="Текст скопирован в буфер обмена")

                # Функция для копирования выделенного текста
                def copy_selected_text():
                    try:
                        selected_text = text_widget.get(tk.SEL_FIRST, tk.SEL_LAST)
                        if selected_text:
                            desc_window.clipboard_clear()
                            desc_window.clipboard_append(selected_text)
                            status_label.config(text="Выделенный текст скопирован")
                    except tk.TclError:
                        status_label.config(text="Ничего не выделено")

                # Добавляем кнопки и статусную строку
                button_frame = ttk.Frame(frame)
                button_frame.pack(fill=tk.X, pady=(10, 5))

                ttk.Button(button_frame, text="Копировать всё", command=copy_all_text).pack(side=tk.LEFT, padx=5)
                ttk.Button(button_frame, text="Копировать выделенное", command=copy_selected_text).pack(side=tk.LEFT, padx=5)
                ttk.Button(button_frame, text="Закрыть", command=desc_window.destroy).pack(side=tk.RIGHT, padx=5)

                # Статусная строка для сообщений
                status_label = ttk.Label(frame, text="")
                status_label.pack(anchor=tk.W, pady=(5, 0))

                # Включаем возможность выделения в текстовом поле, даже когда оно disabled
                def make_text_selectable():
                    text_widget.config(state=tk.NORMAL)
                    text_widget.config(state=tk.DISABLED)

                # Перенастраиваем текстовое поле после того, как оно появится
                desc_window.after(100, make_text_selectable)

                # Добавляем контекстное меню
                context_menu = tk.Menu(text_widget, tearoff=0)
                context_menu.add_command(label="Копировать", command=copy_selected_text)
                context_menu.add_command(label="Копировать всё", command=copy_all_text)

                def show_context_menu(event):
                    context_menu.post(event.x_root, event.y_root)

                text_widget.bind("<Button-3>", show_context_menu)

                # Разрешаем стандартные сочетания клавиш для копирования (Ctrl+C)
                text_widget.bind("<Control-c>", lambda e: copy_selected_text())

                # Делаем текст копируемым
                text_widget.bind("<<Copy>>", lambda e: "break")

                # Показываем окно после загрузки описания
                desc_window.deiconify()

                # Ждем, пока окно закроется
                desc_window.wait_window()

        # Определение контекстного меню
        context_menu = tk.Menu(tree, tearoff=0)
        context_menu.add_command(label="Копировать URL", command=copy_url)
        context_menu.add_command(label="Добавить в очередь загрузки", command=add_to_download_queue)
        context_menu.add_command(label="Открыть в браузере", command=open_in_browser)
        context_menu.add_command(label="Показать описание", command=show_description)

        # Привязываем контекстное меню к правому клику
        tree.bind("<Button-3>", lambda event: context_menu.post(event.x_root, event.y_root))

        # Обработка двойного клика по результату поиска
        def on_double_click(event):
            """Обрабатывает двойной клик по результату поиска"""
            item = tree.selection()[0] if tree.selection() else None
            if item:
                video_url = video_urls.get(item)
                if video_url:
                    log_message(f"Выбрано видео для загрузки: {video_url}")
                    # Добавляем в очередь загрузки
                    add_to_queue(video_url)
                    messagebox.showinfo("Добавлено в очередь",
                                       f"Видео добавлено в очередь загрузки.\n\nURL: {video_url}")

                    # Запускаем обработку очереди, если нет активной загрузки
                    if not is_downloading:
                        threading.Thread(target=process_queue).start()

        # Привязываем двойной клик
        tree.bind("<Double-1>", on_double_click)


        # Функция для выполнения поиска (выбирает API в зависимости от настроек)

        def perform_search():
            """Выполняет поиск видео через выбранный API"""
            log_message("DEBUG: Начало выполнения perform_search")
            try:
                # Сохраняем текущий поисковый запрос
                settings["last_search_query"] = search_var.get().strip()
                save_settings(settings)

                # Определяем, какой метод использовать
                use_alternative = use_alternative_api_var.get()
                search_in_descriptions = search_in_descriptions_var.get()
                advanced_search = advanced_search_var.get()

                query = search_var.get().strip()

                # Подключаемся к базе данных только для расширенного поиска
                if advanced_search:
                    if not is_connected():
                        log_message("DEBUG: Подключение к базе данных отсутствует, вызываем connect_to_database")
                        connect_to_database()
                        if not is_connected():
                            log_message("ERROR: Не удалось установить подключение к базе данных после попытки")
                            status_var.set("Ошибка подключения к базе данных")
                            return

                if advanced_search:
                    advanced_query = advanced_query_var.get().strip()
                    if not advanced_query:
                        status_var.set("Введите запрос для поиска по описаниям")
                        log_message(f"Введите запрос для поиска по описаниям")
                        return

                    log_message(f"INFO Выполняется расширенный поиск по описаниям: {advanced_query}")
                    
                    # Сначала делаем обычный поиск через API
                    if use_alternative:
                        log_message("INFO Выбран поиск через Invidious API")
                        results = search_via_invidious(query) or []

                        # Загружаем описания в базу данных
                        log_message("INFO Загрузка описаний в базу данных (Invidious API)")
                        clear_descriptions_table()  # Очищаем таблицу
                        log_message("DEBUG: Таблица описаний очищена")
                        for item in results:
                            video_id = item.get("videoId")
                            video_url = f"https://www.youtube.com/watch?v={video_id}"
                            log_message(f"DEBUG: Загрузка описания для видео {video_id}")
                            description = fetch_description_with_bs(video_url)
                            log_message(f"DEBUG: Получено описание длиной {len(description)} символов")
                            insert_description(video_id, description)
                            log_message(f"DEBUG: Описание сохранено в базу данных для видео {video_id}")

                    else:
                        log_message("INFO Выбран поиск через официальный YouTube API")
                        results = search_via_youtube_api() or {'items': []}

                        # Загружаем описания в базу данных
                        log_message("INFO Загрузка описаний в базу данных (YouTube API)")
                        clear_descriptions_table()  # Очищаем таблицу
                        log_message("DEBUG: Таблица описаний очищена")
                        
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
                                log_message(f"DEBUG: Загрузка описания для видео {video_id}")
                                log_message(f"DEBUG: Получено описание длиной {len(description)} символов")
                                insert_description(video_id, description)
                                log_message(f"DEBUG: Описание сохранено в базу данных для видео {video_id}")
                        else:
                            log_message(f"ERROR: Ошибка при загрузке описаний через YouTube API: {response.status_code}")

                    # Теперь ищем по базе данных
                    db_results = search_in_database(advanced_query)
                    log_message(f"INFO Найдено совпадений в базе: {len(db_results)}")

                    if not db_results:
                        status_var.set("По запросу ничего не найдено")
                        return

                    # Очищаем таблицу перед добавлением новых результатов
                    for item in tree.get_children():
                        tree.delete(item)
                    video_urls.clear()

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
                                video_id = video['id']
                                duration = format_duration(video['contentDetails']['duration'])
                                video_durations[video_id] = duration
                    except Exception as e:
                        log_message(f"Ошибка при получении длительностей видео: {e}")
                        video_durations = {}

                    # Отображаем результаты в интерфейсе
                    for video_id, description in db_results:
                        # Ищем видео в результатах API
                        video = None
                        if use_alternative:
                            for item in results:
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
                if use_alternative:
                    log_message("INFO Выбран поиск через Invidious API")
                    results = search_via_invidious(query) or []
                    log_message(f"DEBUG: Получено {len(results)} результатов, начинаем обработку...")

                    # Очищаем таблицу перед добавлением новых результатов
                    for item in tree.get_children():
                        tree.delete(item)
                    video_urls.clear()

                    # Отображаем результаты в интерфейсе
                    for item in results:
                        video_id = item.get("videoId")
                        title = decode_html_entities(item.get("title", "Без названия"))
                        channel = decode_html_entities(item.get("author", "Неизвестный канал"))
                        duration = format_invidious_duration(item.get("lengthSeconds", 0))
                        video_url = f"https://www.youtube.com/watch?v={video_id}"
                        item_id = tree.insert('', tk.END, values=(title, channel, duration))
                        video_urls[item_id] = video_url

                else:
                    log_message("INFO Выбран поиск через официальный YouTube API")
                    results = search_via_youtube_api() or {'items': []}

                    # Очищаем таблицу перед добавлением новых результатов
                    for item in tree.get_children():
                        tree.delete(item)
                    video_urls.clear()

                    # Получаем длительности всех видео одним запросом
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
                                video_id = video['id']
                                duration = format_duration(video['contentDetails']['duration'])
                                video_durations[video_id] = duration
                    except Exception as e:
                        log_message(f"Ошибка при получении длительностей видео: {e}")
                        video_durations = {}

                    # Отображаем результаты в интерфейсе
                    for item in results.get('items', []):
                        video_id = item['id']['videoId']
                        title = decode_html_entities(item['snippet']['title'])
                        channel = decode_html_entities(item['snippet']['channelTitle'])
                        duration = video_durations.get(video_id, 'N/A')
                        video_url = f"https://www.youtube.com/watch?v={video_id}"
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

        # Кнопки в нижней части окна
        bottom_button_frame = ttk.Frame(main_frame)
        bottom_button_frame.pack(fill=tk.X, pady=10)

        ttk.Button(bottom_button_frame, text="Загрузить выбранное",
                  command=add_to_download_queue).pack(side=tk.RIGHT, padx=5)
        ttk.Button(bottom_button_frame, text="Закрыть",
                  command=search_window.destroy).pack(side=tk.RIGHT, padx=5)

        # Запускаем главный цикл
        search_window.mainloop()

    except Exception as e:
            log_message(f"ERROR Ошибка в окне поиска YouTube: {e}")
            log_message(f"Трассировка: {traceback.format_exc()}")
            messagebox.showerror("Ошибка", f"Произошла ошибка: {e}")
            if search_window is not None and search_window.winfo_exists():
                search_window.destroy()
            search_window = None  # Сбрасываем переменную в случае ошибки