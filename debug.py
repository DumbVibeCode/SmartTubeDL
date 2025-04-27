import os
import tkinter as tk
from tkinter import ttk, scrolledtext
import traceback
from logger import clear_log, log_message, set_log_box, LOG_FILE

debug_window = None
log_box = None
_window_creating = False

def show_debug_window(master=None):
    global debug_window, log_box, _window_creating

    if _window_creating:
        return
    if debug_window is not None and debug_window.winfo_exists():
        debug_window.deiconify()
        debug_window.lift()
        return

    _window_creating = True

    try:
        from tray import root

        if master is None:
            if root is None:
                root = tk.Tk()
                root.withdraw()
                import tray
                tray.root = root
            master = root

        debug_window = tk.Toplevel(master)
        if debug_window is None:
            raise RuntimeError("Не удалось создать окно отладки")

        debug_window.title("Режим отладки - Логи")

        screen_width = debug_window.winfo_screenwidth()
        screen_height = debug_window.winfo_screenheight()
        width, height = 800, 400
        x = (screen_width - width) // 2
        y = (screen_height - height) // 2
        debug_window.geometry(f"{width}x{height}+{x}+{y}")
        debug_window.update_idletasks()

        log_frame = ttk.Frame(debug_window, padding=10)
        log_frame.pack(fill=tk.BOTH, expand=True)

        log_box = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD, height=20)
        log_box.pack(fill=tk.BOTH, expand=True)

        # Настраиваем теги с видимым выделением
        log_box.tag_config("info", foreground="black", background="#f0f0f0", selectbackground="#3399ff", selectforeground="white")
        log_box.tag_config("success", foreground="green", background="#eaffea", selectbackground="#3399ff", selectforeground="white")
        log_box.tag_config("warning", foreground="orange", background="#fff4d6", selectbackground="#3399ff", selectforeground="white")
        log_box.tag_config("error", foreground="red", background="#ffecec", selectbackground="#3399ff", selectforeground="white")
        log_box.tag_config("debug", foreground="blue", background="#e6f3ff", selectbackground="#3399ff", selectforeground="white")

        # Разрешаем выделение и копирование, запрещаем редактирование
        log_box.configure(state='normal')
        def block_edit(event):
            # Разрешаем копирование и навигацию
            if (event.keysym.lower() == "c" or "с") and event.state & 0x4:  # Ctrl+C
                log_box.event_generate("<<Copy>>")
                return
            if (event.keysym.lower() == "a" or "ф") and event.state & 0x4:  # Ctrl+A
                log_box.tag_add("sel", "1.0", "end")
                return
            if event.keysym in ("Left", "Right", "Up", "Down"):  # Стрелки
                return
            return "break"  # Блокируем редактирование
        log_box.bind("<Key>", block_edit)

        # Контекстное меню для копирования
        context_menu = tk.Menu(log_box, tearoff=0)
        context_menu.add_command(label="Копировать", command=lambda: log_box.event_generate("<<Copy>>"))
        def show_context_menu(event):
            try:
                if log_box.tag_ranges("sel"):
                    context_menu.post(event.x_root, event.y_root)
            except tk.TclError:
                pass
        log_box.bind("<Button-3>", show_context_menu)

        set_log_box(log_box)

        # Функция для загрузки и фильтрации логов
        def filter_logs(level=None):
            log_box.configure(state='normal')
            log_box.delete("1.0", tk.END)
            try:
                with open(os.path.join(os.getcwd(), "log.txt"), "r", encoding="utf-8") as log:
                    for line in log:
                        if not level or level.upper() in line:
                            tag = "info"
                            line_lower = line.lower()
                            if "error" in line_lower or "ошибка" in line_lower:
                                tag = "error"
                            elif "успешно" in line_lower or "готов" in line_lower or "success" in line_lower:
                                tag = "success"
                            elif "предупреждение" in line_lower or "warning" in line_lower:
                                tag = "warning"
                            elif "debug" in line_lower:
                                tag = "debug"
                            log_box.insert(tk.END, line, tag)
                log_box.see(tk.END)
                log_box.update()
            except Exception as e:
                log_message(f"ERROR Ошибка при фильтрации логов: {e}")
            log_box.configure(state='normal')

        # Инициальная загрузка логов
        filter_logs()

        button_frame = ttk.Frame(debug_window)
        button_frame.pack(fill=tk.X, pady=5)
        clear_button = ttk.Button(button_frame, text="Очистить лог", command=lambda: clear_log_with_log())
        clear_button.pack(side=tk.RIGHT, padx=5)
        minimize_button = ttk.Button(button_frame, text="Свернуть", command=debug_window.iconify)
        minimize_button.pack(side=tk.RIGHT, padx=5)
        error_button = ttk.Button(button_frame, text="ERROR", command=lambda: filter_logs("ERROR"))
        error_button.pack(side=tk.LEFT, padx=5)
        info_button = ttk.Button(button_frame, text="INFO", command=lambda: filter_logs("INFO"))
        info_button.pack(side=tk.LEFT, padx=5)
        warning_button = ttk.Button(button_frame, text="WARNING", command=lambda: filter_logs("WARNING"))
        warning_button.pack(side=tk.LEFT, padx=5)
        success_button = ttk.Button(button_frame, text="SUCCESS", command=lambda: filter_logs("SUCCESS"))
        success_button.pack(side=tk.LEFT, padx=5)
        debug_button = ttk.Button(button_frame, text="DEBUG", command=lambda: filter_logs("DEBUG"))
        debug_button.pack(side=tk.LEFT, padx=5)
        reset_button = ttk.Button(button_frame, text="Reset", command=lambda: reset_logs())
        reset_button.pack(side=tk.LEFT, padx=5)

        def reset_logs():
            """Сбрасывает фильтры и показывает все логи"""
            filter_logs()
            

        def clear_log_with_log():
            clear_log()
            

        def on_closing():
            global debug_window, log_box, _window_creating
            try:
                if debug_window and debug_window.winfo_exists():
                    set_log_box(None)
                    debug_window.destroy()
            except Exception as e:
                log_message(f"DEBUG Ошибка при закрытии окна: {e}")
            finally:
                debug_window = None
                log_box = None
                _window_creating = False

        debug_window.protocol("WM_DELETE_WINDOW", on_closing)
        debug_window.bind("<Destroy>", lambda e: log_message("DEBUG Событие Destroy для debug_window") if debug_window and debug_window.winfo_exists() and e.widget is debug_window else None)

        debug_window.update()

    except Exception as e:
        log_message(f"ERROR Ошибка при открытии окна отладки: {e}")
        log_message(f"DEBUG Трассировка: {traceback.format_exc()}")
        if debug_window is not None and debug_window.winfo_exists():
            try:
                debug_window.destroy()
            except Exception:
                pass
        debug_window = None
        log_box = None
    finally:
        _window_creating = False
