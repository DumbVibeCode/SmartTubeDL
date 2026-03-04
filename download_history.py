import os
import json
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
from logger import log_message
from queues import add_to_queue, process_queue
from config import is_downloading

DOWNLOAD_HISTORY_FILE = os.path.join(os.getcwd(), "download_history.json")

def add_to_history(url, title, format_type, duration=0):
    """Добавляет информацию о загруженном видео в историю"""
    history = load_download_history()

    # Создаем запись о загрузке
    download_record = {
        "url": url,
        "title": title,
        "format": format_type,
        "duration": duration,  # Длительность в секундах
        "date": time.strftime("%Y-%m-%d %H:%M:%S")
    }

    # Добавляем в начало списка (новые загрузки сверху)
    history.insert(0, download_record)

    # Ограничиваем историю 1000 записями
    if len(history) > 1000:
        history = history[:1000]

    # Сохраняем историю
    save_download_history(history)
    log_message(f"INFO Добавлена запись в историю загрузок: {title}")

def load_download_history():
    """Загружает историю загрузок из файла"""
    if not os.path.exists(DOWNLOAD_HISTORY_FILE):
        log_message("INFO Файл истории загрузок не существует, создаем пустой список")
        return []
    
    try:
        with open(DOWNLOAD_HISTORY_FILE, "r", encoding="utf-8") as f:
            history = json.load(f)
            # log_message(f"Загружена история загрузок: {len(history)} записей")
            return history
    except Exception as e:
        log_message(f"ERROR Ошибка при загрузке истории: {e}")
        return []

def save_download_history(history):
    """Сохраняет историю загрузок в файл"""
    try:
        # Создаем директорию, если она не существует
        history_dir = os.path.dirname(DOWNLOAD_HISTORY_FILE)
        if history_dir and not os.path.exists(history_dir):
            os.makedirs(history_dir, exist_ok=True)
        
        # Сохраняем с указанием кодировки UTF-8 и ensure_ascii=False
        with open(DOWNLOAD_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        
        log_message(f"INFO История загрузок сохранена ({len(history)} записей)")
    except Exception as e:
        log_message(f"ERROR Ошибка при сохранении истории: {e}")


def show_history(icon, item):
    """Функция-обработчик для пункта меню 'История загрузок'"""
    log_message("Вызвана функция отображения истории загрузок")
    threading.Thread(target=show_download_history).start()

def show_download_history():
    """Показывает окно с историей загрузок в виде таблицы с возможностью сортировки"""
    history = load_download_history()
    
    if not history:
        messagebox.showinfo("История загрузок", "История загрузок пуста")
        return
    
    log_message(f"Загружено {len(history)} записей в истории")
    
    try:
        # Создаем окно с историей загрузок
        root = tk.Tk()
        root.title("История загрузок")
        root.geometry("1000x600")
        
        # Явно делаем окно видимым и выводим его на передний план
        root.deiconify()
        root.lift()
        root.focus_force()
        
        # На Windows также можно использовать:
        if os.name == 'nt':
            root.attributes('-topmost', True)
            root.update()
            root.attributes('-topmost', False)
        
        # Переменная для хранения всплывающей подсказки
        tooltip = None
        tooltip_id = None
        
        # Основной фрейм
        main_frame = tk.Frame(root)
        main_frame.pack(fill=tk.BOTH, expand=1, padx=10, pady=10)
        
        # Заголовок
        tk.Label(main_frame, text="История загрузок", font=("Arial", 12, "bold")).pack(pady=(0, 10))
        tk.Label(main_frame, text=f"Всего записей: {len(history)}").pack(pady=(0, 5))
        
        # Создаем фрейм для списка с прокруткой
        list_frame = tk.Frame(main_frame)
        list_frame.pack(fill=tk.BOTH, expand=1)
        
        # Полосы прокрутки
        vsb = ttk.Scrollbar(list_frame, orient="vertical")
        hsb = ttk.Scrollbar(main_frame, orient="horizontal")
        
        # Создаем Listbox с поддержкой прокрутки
        listbox = tk.Listbox(list_frame, selectmode=tk.MULTIPLE, font=("Arial", 10),
                            yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        
        vsb.config(command=listbox.yview)
        hsb.config(command=listbox.xview)
        
        listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=1)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        hsb.pack(fill=tk.X)
        
        # Словарь для хранения URL для каждого элемента
        url_map = {}
        
        # Заполняем список данными
        for i, entry in enumerate(history):
            title = entry.get("title", "Неизвестно")
            date = entry.get("date", "Неизвестно")
            format_type = entry.get("format", "Неизвестно")
            
            # Форматируем строку для отображения в списке
            display_text = f"{date} | {format_type} | {title}"
            
            listbox.insert(tk.END, display_text)
            url_map[i] = entry.get("url", "")
            
            # Добавляем подсветку для четных строк
            if i % 2 == 0:
                listbox.itemconfig(i, bg='#f0f0f0')
        
        # Функция для выделения/снятия выделения со всех элементов
        def toggle_all():
            # Проверяем, все ли элементы выбраны
            if len(listbox.curselection()) == listbox.size():
                # Снимаем выделение со всех
                listbox.selection_clear(0, tk.END)
            else:
                # Выделяем все
                listbox.selection_set(0, tk.END)
        
        # Кнопка выделить/снять выделение
        toggle_button = ttk.Button(main_frame, text="Выделить/Снять выделение", command=toggle_all)
        toggle_button.pack(pady=5)
        
        # Функция для очистки всей истории загрузок
        def clear_history():
            if not messagebox.askyesno("Очистка истории", "Вы уверены, что хотите удалить всю историю загрузок?"):
                return
                
            # Очищаем историю загрузок
            save_download_history([])
            log_message("История загрузок очищена")
            messagebox.showinfo("История загрузок", "История загрузок успешно очищена")
            root.destroy()  # Закрываем окно после очистки
        
        # Функция для загрузки выбранных видео
        def download_selected():
            selected_indices = listbox.curselection()
            
            if not selected_indices:
                messagebox.showinfo("Ничего не выбрано", "Выберите хотя бы одно видео для загрузки")
                return
            
            log_message(f"Выбрано {len(selected_indices)} видео из истории для повторной загрузки")
            
            # Собираем URL для выбранных записей
            selected_urls = []
            for idx in selected_indices:
                url = url_map.get(idx)
                if url:
                    selected_urls.append(url)
                    log_message(f"Выбрано для загрузки: {url}")
            
            # Добавляем выбранные видео в очередь загрузки
            for url in selected_urls:
                add_to_queue(url)
                log_message(f"Добавлено в очередь: {url}")
            
            # Закрываем окно после добавления в очередь
            root.destroy()
            
            # Запускаем обработку очереди
            if not is_downloading:
                threading.Thread(target=process_queue).start()
        
        # Функция для скрытия всплывающей подсказки
        def hide_tooltip():
            nonlocal tooltip, tooltip_id
            if tooltip:
                tooltip.destroy()
                tooltip = None
            if tooltip_id:
                root.after_cancel(tooltip_id)
                tooltip_id = None
        
        # Функция для отображения всплывающей подсказки при наведении
        def show_tooltip(event):
            nonlocal tooltip, tooltip_id
            
            # Получаем индекс элемента под курсором
            idx = listbox.nearest(event.y)
            if idx < 0 or idx >= len(history):
                hide_tooltip()
                return
            
            # Если курсор не над списком, скрываем подсказку
            if event.x < 0 or event.x > listbox.winfo_width() or event.y < 0 or event.y > listbox.winfo_height():
                hide_tooltip()
                return
                
            # Получаем полный заголовок
            title = history[idx].get('title', 'Неизвестно')
            
            # Если подсказка уже показана, обновляем только если изменился элемент
            if tooltip and hasattr(tooltip, 'item_idx') and tooltip.item_idx == idx:
                return
            
            # Скрываем текущую подсказку, если она есть
            hide_tooltip()
            
            # Показываем новую всплывающую подсказку
            x = root.winfo_pointerx() + 15
            y = root.winfo_pointery() + 10
            
            # Создаем всплывающее окно
            tooltip = tk.Toplevel(root)
            tooltip.wm_overrideredirect(True)  # Убираем рамку окна
            tooltip.wm_geometry(f"+{x}+{y}")
            tooltip.item_idx = idx  # Сохраняем индекс элемента
            
            # Добавляем метку с текстом
            label = tk.Label(tooltip, text=title, justify='left',
                           background="#ffffe0", relief="solid", borderwidth=1,
                           font=("Arial", 10), wraplength=400)
            label.pack(padx=2, pady=2)
            
            # Устанавливаем таймер для скрытия подсказки
            tooltip_id = root.after(3000, hide_tooltip)
        
        # Функция для обработки движения мыши
        def on_mouse_motion(event):
            # Если мышь движется, вызываем функцию показа подсказки
            show_tooltip(event)
        
        # Привязываем события
        listbox.bind("<Motion>", on_mouse_motion)
        listbox.bind("<Leave>", lambda e: hide_tooltip())
        
        # Двойной клик для быстрого выбора
        def on_double_click(event):
            # Получаем индекс элемента под курсором
            idx = listbox.nearest(event.y)
            if idx >= 0 and idx < len(history):
                # Если элемент уже выбран, снимаем выделение, иначе выбираем
                if idx in listbox.curselection():
                    listbox.selection_clear(idx)
                else:
                    listbox.selection_set(idx)
        
        listbox.bind("<Double-1>", on_double_click)
        
        # Кнопки действия
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(pady=10, fill=tk.X)
        
        ttk.Button(button_frame, text="Загрузить выбранные", command=download_selected).pack(side=tk.RIGHT, padx=5)
        ttk.Button(button_frame, text="Очистить историю", command=clear_history).pack(side=tk.RIGHT, padx=5)
        ttk.Button(button_frame, text="Закрыть", command=root.destroy).pack(side=tk.RIGHT, padx=5)
        
        # Обработчик события закрытия окна
        def on_close():
            hide_tooltip()
            root.destroy()
        
        root.protocol("WM_DELETE_WINDOW", on_close)
        
        # Запускаем главный цикл с обработкой исключений
        try:
            root.mainloop()
        except Exception as e:
            log_message(f"Ошибка в главном цикле окна истории: {e}")
    
    except Exception as e:
        log_message(f"Критическая ошибка при отображении истории загрузок: {e}")
        messagebox.showerror("Ошибка", f"Не удалось отобразить историю загрузок:\n{e}")