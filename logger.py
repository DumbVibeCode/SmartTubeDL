import os
import time
import tkinter as tk

LOG_FILE = os.path.join(os.getcwd(), "log.txt")
_log_box_ref = None

def log_message(message):
    """Логирует сообщение в файл и отображает в интерфейсе"""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    full_message = f"{timestamp} - {message}"

    # Запись в файл
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as log:
            log.write(full_message + "\n")
    except Exception as e:
        print(f"DEBUG Ошибка записи в лог: {e}")

    # Отображение в log_box
    if _log_box_ref and hasattr(_log_box_ref, 'winfo_exists') and _log_box_ref.winfo_exists():
        def update_log_box():
            try:
                _log_box_ref.configure(state='normal')
                tag = "info"
                msg_lower = message.lower()
                if "error" in msg_lower or "ошибка" in msg_lower:
                    tag = "error"
                elif "успешно" in msg_lower or "готов" in msg_lower or "success" in msg_lower:
                    tag = "success"
                elif "предупреждение" in msg_lower or "warning" in msg_lower:
                    tag = "warning"
                elif "отладка" in msg_lower or "debug" in msg_lower:
                    tag = "debug"
                _log_box_ref.insert("end", full_message + "\n", tag)
                _log_box_ref.see("end")
                _log_box_ref.configure(state='normal')
                _log_box_ref.update()
            except Exception:
                pass
        # Вызываем обновление через главный поток
        _log_box_ref.after(0, update_log_box)

def set_log_box(widget):
    """Устанавливает ссылку на виджет логов"""
    global _log_box_ref
    _log_box_ref = widget
    # if widget is None:
    #     log_message("DEBUG log_box очищен")
    # else:
    #     log_message("DEBUG log_box установлен")

def clear_log():
    """Очищает лог-файл и лог-бокс"""
    global _log_box_ref
    try:
        with open(LOG_FILE, "w", encoding="utf-8") as log:
            log.write("")
        
        if _log_box_ref and hasattr(_log_box_ref, 'winfo_exists') and _log_box_ref.winfo_exists():
            _log_box_ref.configure(state='normal')
            _log_box_ref.delete("1.0", tk.END)
            _log_box_ref.configure(state='disabled')
            _log_box_ref.update()
        log_message("SUCCESS Лог очищен")
    except Exception as e:
        log_message(f"ERROR Ошибка при очистке лога: {e}")

def load_log_file():
    """Загружает содержимое лог-файла в лог-бокс"""
    try:
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 10 * 1024 * 1024:
            log_message("WARNING Лог-файл слишком большой, очищаем")
            clear_log()
            return

        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as log:
            lines = log.readlines()

        if _log_box_ref and hasattr(_log_box_ref, 'winfo_exists') and _log_box_ref.winfo_exists():
            _log_box_ref.configure(state='normal')
            _log_box_ref.delete("1.0", tk.END)
            for line in lines:
                tag = "info"
                lower = line.lower()
                tag = "error" if "error" in line.lower() else "unknown"
                tag = "info" if "info" in line.lower() else "unknown"
                tag = "warning" if "warning" in line.lower() else "unknown"
                tag = "success" if "success" in line.lower() else "unknown"
                tag = "debug" if "debug" in line.lower() else "unknown"
                _log_box_ref.insert("end", line, tag)
            _log_box_ref.see("end")
            _log_box_ref.configure(state='disabled')
            _log_box_ref.update()
            log_message("DEBUG Лог загружен в log_box")
        else:
            log_message("DEBUG log_box не активен, пропускаем загрузку")
    except FileNotFoundError:
        with open(LOG_FILE, "w", encoding="utf-8") as log:
            log.write("")
        log_message("INFO Файл лога создан")
    except Exception as e:
        log_message(f"ERROR Ошибка при загрузке лога: {e}")