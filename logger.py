import os
import time
import tkinter as tk

LOG_FILE = os.path.join(os.getcwd(), "log.txt")
_log_box_ref = None  # Ссылка на виджет логов

def log_message(message):
    """Логирует сообщение в файл и отображает в интерфейсе, если подключён лог-бокс"""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    full_message = f"{timestamp} - {message}"

    # Запись в файл
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as log:
            log.write(full_message + "\n")
    except Exception as e:
        print(f"Ошибка записи в лог: {e}")

    # Отображение в tkinter, если лог-бокс подключён
    try:
        if _log_box_ref:
            _log_box_ref.configure(state='normal')

            # Определяем тег (цвет)
            tag = "info"
            msg_lower = message.lower()
            if "error" in msg_lower or "ошибка" in msg_lower:
                tag = "error"
            elif "успешно" in msg_lower or "готов" in msg_lower or "success" in msg_lower:
                tag = "success"
            elif "предупреждение" in msg_lower or "warning" in msg_lower:
                tag = "warning"

            _log_box_ref.insert("end", full_message + "\n", tag)
            _log_box_ref.see("end")
            _log_box_ref.configure(state='disabled')
    except Exception:
        pass  # Игнорируем ошибки отображения в интерфейсе

def set_log_box(widget):
    """Устанавливает ссылку на виджет логов"""
    global _log_box_ref
    _log_box_ref = widget

def clear_log():
    global _log_box_ref
    try:
        # Очищаем файл
        with open(LOG_FILE, "w", encoding="utf-8") as log:
            log.write("")
        
        # Очищаем окно
        if _log_box_ref:
            _log_box_ref.configure(state='normal')
            _log_box_ref.delete("1.0", tk.END)
            _log_box_ref.configure(state='disabled')
        
        # log_message("Лог очищен.")
    except Exception as e:
        log_message(f"ERROR Ошибка при очистке лога: {e}")    

def load_log_file():
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as log:
            lines = log.readlines()
        if _log_box_ref:
            _log_box_ref.configure(state='normal')
            for line in lines:
                # Определим тег по содержимому строки
                tag = "info"
                lower = line.lower()
                if "error" in lower or "ошибка" in lower:
                    tag = "error"
                elif "успешно" in lower or "готов" in lower or "success" in lower:
                    tag = "success"
                elif "предупреждение" in lower or "warning" in lower:
                    tag = "warning"

                _log_box_ref.insert("end", line, (tag,))

            _log_box_ref.see("end")
            _log_box_ref.configure(state='disabled')
    except FileNotFoundError:
        # Файл не существует — создаём пустой
        open(LOG_FILE, "w", encoding="utf-8").close()
        log_message("INFO Файл лога создан.")
    except Exception as e:
        log_message(f"ERROR Ошибка при загрузке лога: {e}")        