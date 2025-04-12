import asyncio
import threading
import tkinter as tk
from tkinter import ttk
import requests

from fetch import fetch_description_with_bs
from logger import log_message

import tkinter as tk
from tkinter import ttk
from fetch import fetch_description_with_ytdlp  # Импортируем новую функцию
from logger import log_message

def show_description(tree, video_urls, search_window, status_var, video_descriptions):
    """Показывает описание выбранного видео в окне с возможностью копирования"""
    selected = tree.selection()[0] if tree.selection() else None
    if selected and selected in video_urls:
        video_url = video_urls[selected]
        video_id = video_url.split('v=')[1] if 'v=' in video_url else video_url.split('/')[-1]

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

        # Функция для асинхронной загрузки описания
        def load_description():
            if video_id not in video_descriptions or video_descriptions[video_id] == "Описание будет загружено при запросе":
                status_var.set("Загрузка описания...")
                description = fetch_description_with_ytdlp(video_url)
                if description:
                    video_descriptions[video_id] = description
                    log_message(f"INFO: Описание загружено через yt-dlp для {video_url}")
                else:
                    description = "Описание недоступно (ошибка загрузки или отсутствует)"
                    video_descriptions[video_id] = description
                    log_message(f"WARNING: Не удалось загрузить описание через yt-dlp для {video_url}")
            else:
                description = video_descriptions[video_id]
                log_message(f"DEBUG: Описание взято из кэша для {video_id}")

            # Обновляем UI в главном потоке
            search_window.after(0, lambda: update_description(description))

        def update_description(description):
            text_widget.insert(tk.END, description)
            text_widget.config(state=tk.DISABLED)
            status_var.set("Описание загружено")
            desc_window.deiconify()  # Показываем окно после загрузки

        # Запускаем загрузку в отдельном потоке
        threading.Thread(target=load_description, daemon=True).start()

        # Функции копирования
        def copy_all_text():
            desc_window.clipboard_clear()
            desc_window.clipboard_append(text_widget.get("1.0", tk.END).strip())
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

        # Контекстное меню
        context_menu = tk.Menu(text_widget, tearoff=0)
        context_menu.add_command(label="Копировать", command=copy_selected_text)
        context_menu.add_command(label="Копировать всё", command=copy_all_text)

        text_widget.bind("<Button-3>", lambda event: context_menu.post(event.x_root, event.y_root))
        text_widget.bind("<Control-c>", lambda e: copy_selected_text())
        text_widget.bind("<<Copy>>", lambda e: "break")

        desc_window.wait_window()
    else:
        status_var.set("Выберите видео для просмотра описания")
        log_message("INFO: Не выбрано видео для показа описания")