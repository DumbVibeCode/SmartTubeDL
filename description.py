import threading
import tkinter as tk
from tkinter import ttk

from fetch import fetch_description_with_ytdlp
from logger import log_message


def show_description(tree, video_urls, search_window, status_var, status_label, video_descriptions):
    selected = tree.selection()[0] if tree.selection() else None
    if selected and selected in video_urls:
        video_url = video_urls[selected]
        video_id = video_url.split('v=')[1] if 'v=' in video_url else video_url.split('/')[-1]

        desc_window = tk.Toplevel(search_window)
        desc_window.title("Описание видео")
        desc_window.geometry("600x400")
        desc_window.withdraw()

        desc_window.update_idletasks()
        screen_width = desc_window.winfo_screenwidth()
        screen_height = desc_window.winfo_screenheight()
        x = (screen_width - 600) // 2
        y = (screen_height - 400) // 2
        desc_window.geometry(f"600x400+{x}+{y}")

        desc_window.transient(search_window)
        desc_window.grab_set()

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

        def safe_set_status(text, color):
            def do_update():
                try:
                    if status_label.winfo_exists():
                        status_var.set(text)
                        status_label.config(foreground=color)
                except tk.TclError:
                    pass  # Элемент уже уничтожен или не существует
            try:
                search_window.after(0, do_update)
            except tk.TclError:
                pass  # Окно уже закрыто

        def update_description(description):
            text_widget.insert(tk.END, description)
            text_widget.config(state=tk.DISABLED)
            safe_set_status("Описание загружено", "green")
            desc_window.deiconify()

        def load_description():
            safe_set_status("Загрузка описания...", "blue")

            if video_id not in video_descriptions or video_descriptions[video_id] == "Описание будет загружено при запросе":
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

            search_window.after(0, lambda: update_description(description))

        threading.Thread(target=load_description, daemon=True).start()

        def copy_all_text():
            desc_window.clipboard_clear()
            desc_window.clipboard_append(text_widget.get("1.0", tk.END).strip())
            safe_set_status("Текст скопирован в буфер обмена", "green")

        def copy_selected_text():
            try:
                selected_text = text_widget.get(tk.SEL_FIRST, tk.SEL_LAST)
                if selected_text:
                    desc_window.clipboard_clear()
                    desc_window.clipboard_append(selected_text)
                    safe_set_status("Выделенный текст скопирован", "green")
            except tk.TclError:
                safe_set_status("Ничего не выделено", "red")

        def block_edit(event):
            # Ctrl+C (независимо от раскладки)
            if event.state & 0x4 and event.keycode == 67:
                copy_selected_text()
                return "break"

            # Ctrl+A
            if event.state & 0x4 and event.keycode == 65:
                text_widget.tag_add(tk.SEL, "1.0", tk.END)
                text_widget.mark_set(tk.INSERT, "1.0")
                text_widget.see(tk.INSERT)
                return "break"
            
            # Ctrl+Insert
            if event.state & 0x4 and event.keycode == 45:
                copy_selected_text()
                return "break"

            if event.keysym in ("Left", "Right", "Up", "Down"):
                return

            return "break"

        context_menu = tk.Menu(text_widget, tearoff=0)
        context_menu.add_command(label="Копировать", command=copy_selected_text)
        context_menu.add_command(label="Копировать всё", command=copy_all_text)

        text_widget.bind("<Button-3>", lambda event: context_menu.post(event.x_root, event.y_root))
        text_widget.bind("<Key>", block_edit)
        text_widget.bind("<<Copy>>", lambda e: copy_selected_text())

        button_frame = ttk.Frame(frame)
        button_frame.pack(fill=tk.X, pady=(10, 5))

        ttk.Button(button_frame, text="Копировать всё", command=copy_all_text).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Копировать выделенное", command=copy_selected_text).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Закрыть", command=desc_window.destroy).pack(side=tk.RIGHT, padx=5)

        desc_window.wait_window()
        safe_set_status("Введите поисковый запрос и нажмите 'Искать'", "blue")
    else:
        status_var.set("Выберите видео для просмотра описания")
        status_label.config(foreground="red")
        log_message("WARNING: Не выбрано видео для показа описания")
