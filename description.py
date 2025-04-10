import tkinter as tk
from tkinter import ttk
import requests

from fetch import fetch_description_with_bs
from logger import log_message

def show_description(tree, video_urls, search_window, status_var, use_alternative_api_var, api_key_var, video_descriptions):
    """Показывает описание выбранного видео в окне с возможностью копирования"""
    selected = tree.selection()[0] if tree.selection() else None
    if selected and selected in video_urls:
        video_url = video_urls[selected]
        video_id = video_url.split('v=')[1]

        # Создаем окно
        desc_window = tk.Toplevel(search_window)
        desc_window.title("Описание видео")
        desc_window.geometry("600x400")
        desc_window.withdraw()  # Скрываем до загрузки

        # Центрируем окно
        desc_window.update_idletasks()
        screen_width = desc_window.winfo_screenwidth()
        screen_height = desc_window.winfo_screenheight()
        x = (screen_width - 600) // 2
        y = (screen_height - 400) // 2
        desc_window.geometry(f"600x400+{x}+{y}")

        # Модальное окно
        desc_window.transient(search_window)
        desc_window.grab_set()

        # Текстовое поле
        frame = ttk.Frame(desc_window, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        url_label = ttk.Label(frame, text=f"URL видео: {video_url}")
        url_label.pack(anchor=tk.W, pady=(0, 10))

        text_frame = ttk.Frame(frame)
        text_frame.pack(fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(text_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        text_widget = tk.Text(text_frame, wrap=tk.WORD, yscrollcommand=scrollbar.set)
        text_widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=text_widget.yview)

        # Загрузка описания
        if video_id not in video_descriptions or video_descriptions[video_id] == "Описание будет загружено при запросе":
            status_var.set("Загрузка описания...")
            use_alternative = use_alternative_api_var.get()
            if use_alternative:
                description = fetch_description_with_bs(video_url)
                log_message(f"Описание загружено через BS: {description}")
            else:
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
            video_descriptions[video_id] = description
        else:
            description = video_descriptions[video_id]

        # Вставка описания
        text_widget.insert(tk.END, description)
        text_widget.config(state=tk.DISABLED)

        # Функции копирования
        def copy_all_text():
            desc_window.clipboard_clear()
            desc_window.clipboard_append(description)
            status_var.set("Текст скопирован в буфер обмена")

        def copy_selected_text():
            try:
                selected_text = text_widget.get(tk.SEL_FIRST, tk.SEL_LAST)
                if selected_text:
                    desc_window.clipboard_clear()
                    desc_window.clipboard_append(selected_text)
                    status_var.set("Выделенный текст скопирован")
            except tk.TclError:
                status_var.set("Ничего не выделено")

        # Кнопки
        button_frame = ttk.Frame(frame)
        button_frame.pack(fill=tk.X, pady=(10, 5))

        ttk.Button(button_frame, text="Копировать всё", command=copy_all_text).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Копировать выделенное", command=copy_selected_text).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Закрыть", command=desc_window.destroy).pack(side=tk.RIGHT, padx=5)

        # Статусная строка
        status_label = ttk.Label(frame, text="")
        status_label.pack(anchor=tk.W, pady=(5, 0))

        # Делаем текст выделяемым
        desc_window.after(100, lambda: [text_widget.config(state=tk.NORMAL), text_widget.config(state=tk.DISABLED)])

        # Контекстное меню
        context_menu = tk.Menu(text_widget, tearoff=0)
        context_menu.add_command(label="Копировать", command=copy_selected_text)
        context_menu.add_command(label="Копировать всё", command=copy_all_text)

        text_widget.bind("<Button-3>", lambda event: context_menu.post(event.x_root, event.y_root))
        text_widget.bind("<Control-c>", lambda e: copy_selected_text())
        text_widget.bind("<<Copy>>", lambda e: "break")

        # Показываем окно
        desc_window.deiconify()
        desc_window.wait_window()