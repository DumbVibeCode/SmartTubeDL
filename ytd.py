import os
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
import pyperclip
import yt_dlp
import time
import threading
import json
import pystray
from pystray import MenuItem as item, Icon
from PIL import Image, ImageDraw
import ffmpeg
import subprocess
import re
from plyer import notification
from tkinter import Tk
from tkinter import ttk, messagebox
import ctypes
import win32clipboard
import traceback
import webbrowser
from datetime import datetime
import requests
from PyQt5 import QtWidgets
import sys
from ttkwidgets.autocomplete import AutocompleteEntry
from bs4 import BeautifulSoup
import re
import json

class AutocompleteEntry(ttk.Entry):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Здесь можно добавить логику автозаполнения, если необходимо


SETTINGS_FILE = os.path.join(os.getcwd(), "settings.json")
LOG_FILE = os.path.join(os.getcwd(), "log.txt")
DOWNLOAD_HISTORY_FILE = os.path.join(os.getcwd(), "download_history.json")
global last_clipboard
global_file_size = 0  # Общий размер файла в байтах
global_downloaded = 0  # Загружено байтов
current_format = ""
QUEUE_FILE = os.path.join(os.getcwd(), "download_queue.txt")
log_box = None  # сюда мы потом передадим tkinter-виджет
_log_box_ref = None  # ссылка на виджет логов
# message = ""


def ensure_invidious_running():
    try:
        response = requests.get("http://localhost:3000/api/v1/stats", timeout=2)
        if response.status_code == 200:
            log_message("SUCCESS Локальный сервер Invidious уже запущен")
            return
    except requests.exceptions.ConnectionError:
        log_message("INFO Запускаем локальный сервер Invidious...")
        try:
            # Запускаем напрямую без alias
            subprocess.Popen(["wsl", "bash", "-c", "/home/ksr123/start_invidious.sh"])

            # Ждём до 10 сек
            for i in range(20):
                time.sleep(0.5)
                try:
                    r = requests.get("http://localhost:3000/api/v1/stats", timeout=1)
                    if r.status_code == 200:
                        log_message("SUCCESS Локальный сервер Invidious успешно запущен")
                        return
                except requests.exceptions.ConnectionError:
                    continue

            log_message("Ошибка: Invidious не ответил после запуска (timeout 10 сек)")
        except Exception as e:
            log_message(f"ERROR Ошибка при запуске локального Invidious: {e}")
    except Exception as e:
        log_message(f"ERROR Ошибка при проверке Invidious: {e}")




def ensure_queue_file_exists():
    """Убеждается, что файл очереди существует"""
    if not os.path.exists(QUEUE_FILE):
        try:
            # Создаем директорию если нужно
            queue_dir = os.path.dirname(QUEUE_FILE)
            if queue_dir and not os.path.exists(queue_dir):
                os.makedirs(queue_dir, exist_ok=True)
                
            # Создаем пустой файл
            with open(QUEUE_FILE, "w", encoding="utf-8") as f:
                pass  # Создаем пустой файл
            log_message(f"INFO Создан пустой файл очереди: {QUEUE_FILE}")
        except Exception as e:
            log_message(f"ERROR Ошибка при создании файла очереди: {e}")

# Гарантированно создаем лог-файл
try:
    with open(LOG_FILE, "a", encoding="utf-8") as log:
        # log.write("\n--- Программа запущена ---\n")
        pass
except Exception as e:
    print(f"ERROR Ошибка при создании лога: {e}")

def set_log_box(widget):
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

def log_message(message):
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
    except Exception as e:
        print(f"Ошибка отображения лога в интерфейсе: {e}")

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

# log_message("Программа запущена")

def add_to_history(url, title, format_type):
    """Добавляет информацию о загруженном видео в историю"""
    history = load_download_history()
    
    # Создаем запись о загрузке
    download_record = {
        "url": url,
        "title": title,
        "format": format_type,
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


from bs4 import BeautifulSoup

def fetch_description_with_bs(video_url):
    """Получает полное описание видео с YouTube с помощью BeautifulSoup"""
    try:
        # Отправляем GET-запрос к странице видео
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        response = requests.get(video_url, headers=headers, timeout=10)
        
        if response.status_code != 200:
            log_message(f"Ошибка при запросе страницы видео: {response.status_code}")
            return "Описание недоступно (ошибка загрузки страницы)"
        
        # Парсим HTML-страницу
        soup = BeautifulSoup(response.text, "html.parser")
        
        # Ищем скрипт с ytInitialPlayerResponse
        scripts = soup.find_all("script")
        for script in scripts:
            if "ytInitialPlayerResponse" in script.text:
                try:
                    # Извлекаем JSON-данные с помощью регулярного выражения
                    match = re.search(r"ytInitialPlayerResponse\s*=\s*({.*?});", script.text, re.DOTALL)
                    if not match:
                        log_message("ytInitialPlayerResponse не найден в скрипте")
                        continue
                    
                    json_data = match.group(1)
                    data = json.loads(json_data)
                    
                    # Извлекаем описание из поля "shortDescription"
                    description = data.get("videoDetails", {}).get("shortDescription", "").strip()
                    if description:
                        return description
                except Exception as e:
                    log_message(f"Ошибка при извлечении shortDescription из JSON: {e}")
        
        # Попытка 2: Найти описание в теге <meta name="description">
        description_meta = soup.find("meta", {"name": "description"})
        if description_meta and "content" in description_meta.attrs:
            description = description_meta["content"].strip()
            if description:
                return description
        
        # Если описание не найдено
        return "Описание недоступно (не найдено на странице)"
    
    except Exception as e:
        log_message(f"Ошибка при парсинге описания: {e}")
        return "Описание недоступно (ошибка парсинга)"
    
def download_channel_with_selection(channel_url):
    """Отображает окно выбора видео из канала для загрузки"""
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


# Функция для отображения истории и выбора видео для повторной загрузки
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


def download_playlist_with_selection(playlist_url):
    """Отображает окно выбора видео из плейлиста для загрузки"""
    log_message(f"Обработка плейлиста: {playlist_url}")
    
    try:
        # Сначала получаем информацию о плейлисте
        ydl_opts = {
            'quiet': True,
            'extract_flat': True,  # Не загружать видео, только получить информацию
            'skip_download': True,
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

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "r") as f:
            return json.load(f)
    return {"download_folder": os.path.expanduser("~")}

def save_settings(settings):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f)

settings = load_settings()
settings.setdefault("auto_capture_enabled", True)  # Устанавливаем автоперехват по умолчанию
settings.setdefault("download_format", "mp4")
settings.setdefault("video_quality", "1080p")
settings.setdefault("conversion_enabled", True)

download_status = "Ожидание..."

settings.setdefault("download_format", "mp4")
save_settings(settings)
auto_capture_enabled = settings.get("auto_capture_enabled", True)

download_speed = "0 KB/s"
last_update_time = time.time()
last_downloaded_bytes = 0

def format_size(size_bytes):
    """Форматирует размер в читаемый вид (КБ, МБ, ГБ)"""
    if size_bytes < 1024:
        return f"{size_bytes} Б"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} КБ"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} МБ"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} ГБ"

def format_speed(speed_bytes):
    """Форматирует скорость в читаемый вид"""
    if speed_bytes < 1024:
        return f"{speed_bytes} B/s"
    elif speed_bytes < 1024 * 1024:
        return f"{speed_bytes / 1024:.1f} KB/s"
    else:
        return f"{speed_bytes / (1024 * 1024):.1f} MB/s"

def update_speed(downloaded_bytes):
    """Обновляет скорость загрузки"""
    global download_speed, last_update_time, last_downloaded_bytes
    
    current_time = time.time()
    time_diff = current_time - last_update_time
    
    if time_diff > 0.5:  # Обновляем скорость каждые 0.5 секунды
        speed_bytes = (downloaded_bytes - last_downloaded_bytes) / time_diff
        download_speed = format_speed(speed_bytes)
        last_update_time = current_time
        last_downloaded_bytes = downloaded_bytes

if "download_format" not in settings:
    settings["download_format"] = "mp4"
    save_settings(settings)

def add_to_queue(url):
    """Добавляет URL в файловую очередь загрузок"""
    # Проверяем, есть ли URL уже в очереди
    urls = get_queue_urls()
    if url in urls:
        log_message(f"URL уже в очереди: {url}")
        return False
    
    # Создаем директорию для файла очереди, если она не существует
    queue_dir = os.path.dirname(QUEUE_FILE)
    if queue_dir and not os.path.exists(queue_dir):
        try:
            os.makedirs(queue_dir, exist_ok=True)
        except Exception as e:
            log_message(f"Ошибка при создании директории для очереди: {e}")
    
    # Добавляем URL в файл
    try:
        with open(QUEUE_FILE, "a", encoding="utf-8") as f:
            f.write(f"{url}\n")
        log_message(f"Добавлено в очередь: {url} (файл: {QUEUE_FILE})")
        return True
    except Exception as e:
        log_message(f"Ошибка при добавлении в очередь: {e}")
        # Выводим дополнительную отладочную информацию
        log_message(f"Путь к файлу очереди: {QUEUE_FILE}")
        log_message(f"Текущий рабочий каталог: {os.getcwd()}")
        log_message(f"Права доступа: {os.access(os.getcwd(), os.W_OK)}")
        return False
    
def get_queue_urls():
    """Получает список URL из очереди"""
    if not os.path.exists(QUEUE_FILE):
        log_message(f"Файл очереди не существует: {QUEUE_FILE}")
        return []
    
    try:
        with open(QUEUE_FILE, "r", encoding="utf-8") as f:
            # Фильтруем пустые строки и строки, содержащие только пробелы
            urls = [line.strip() for line in f if line.strip()]
        
        # Добавим отладочную информацию
        # log_message(f"Получено {len(urls)} URL из очереди")
        return urls
    except Exception as e:
        log_message(f"Ошибка при чтении очереди: {e}")
        return []
        
def remove_from_queue(url):
    """Удаляет URL из очереди после загрузки"""
    urls = get_queue_urls()
    if url in urls:
        urls.remove(url)
        
        try:
            with open(QUEUE_FILE, "w", encoding="utf-8") as f:
                for u in urls:
                    f.write(f"{u}\n")
            log_message(f"URL удален из очереди: {url}")
        except Exception as e:
            log_message(f"Ошибка при обновлении очереди: {e}")
    else:
        log_message(f"URL не найден в очереди: {url}")

def clear_queue_file():
    """Очищает файл очереди полностью"""
    try:
        # Открываем файл для записи, что автоматически очищает его содержимое
        with open(QUEUE_FILE, "w", encoding="utf-8") as f:
            pass
        log_message("SUCCESS Файл очереди полностью очищен")
    except Exception as e:
        log_message(f"ERROR Ошибка при очистке файла очереди: {e}")


def get_next_url():
    """Возвращает следующий URL из очереди"""
    urls = get_queue_urls()
    return urls[0] if urls else None

def get_queue_count():
    """Возвращает количество URL в очереди"""
    urls = get_queue_urls()
    return len(urls)

# Функция для запуска обработки очереди
def process_queue():
    """Запускает обработку очереди загрузок"""
    global is_downloading
    
    if is_downloading:
        log_message("Обработка очереди отложена: идёт загрузка")
        return
    
    url = get_next_url()
    if not url:
        log_message("Очередь пуста")
        # Очищаем файл очереди, чтобы избежать проблем с пустыми строками
        clear_queue_file()
        return
    
    log_message(f"INFO Начало загрузки URL из очереди: {url}")
    threading.Thread(target=download_video, args=(url, True)).start()


def toggle_conversion(icon, item):
    settings["conversion_enabled"] = not settings["conversion_enabled"]
    save_settings(settings)
    log_message(f"Конвертация {'включена' if settings['conversion_enabled'] else 'выключена'}")

def set_convert_original(icon, item):
    settings["convert_format"] = "original"
    save_settings(settings)
    log_message("Формат конвертации установлен: исходный формат")

def set_convert_mp3(icon, item):
    settings["convert_format"] = "mp3"
    save_settings(settings)
    log_message("Формат конвертации установлен: MP3")

def set_convert_mp4(icon, item):
    settings["convert_format"] = "mp4"
    save_settings(settings)
    log_message("Формат конвертации установлен: MP4")

def set_quality_1080p(icon, item):
    settings["video_quality"] = "1080p"
    save_settings(settings)
    log_message("Качество видео установлено: 1080p")

def set_quality_720p(icon, item):
    settings["video_quality"] = "720p"
    save_settings(settings)
    log_message("Качество видео установлено: 720p")

def set_quality_480p(icon, item):
    settings["video_quality"] = "480p"
    save_settings(settings)
    log_message("Качество видео установлено: 480p")    

def show_history(icon, item):
    """Функция-обработчик для пункта меню 'История загрузок'"""
    log_message("Вызвана функция отображения истории загрузок")
    threading.Thread(target=show_download_history).start()

def generate_menu():
    queue_count = get_queue_count()
    queue_info = f" ({queue_count})" if queue_count > 0 else ""
    
    menu_items = [
        item('Выбрать папку', show_settings),
        item('История загрузок', show_history),
        item('Поиск на YouTube', search_youtube_videos),  # Новый пункт меню
        item('────────────', lambda icon, item: None),
        item('Формат:', lambda icon, item: None, enabled=False),
        item('  Музыка (MP3)', set_format_mp3, checked=lambda item: settings["download_format"] == "mp3"),
        item('  Видео (MP4)', set_format_mp4, checked=lambda item: settings["download_format"] == "mp4"),
        item('────────────', lambda icon, item: None),
        item('Качество:', lambda icon, item: None, enabled=False),
        item('  1080p', set_quality_1080p, checked=lambda item: settings["video_quality"] == "1080p"),
        item('  720p', set_quality_720p, checked=lambda item: settings["video_quality"] == "720p"),
        item('  480p', set_quality_480p, checked=lambda item: settings["video_quality"] == "480p"),
        item('────────────', lambda icon, item: None),
        item('Автоперехват ссылок', toggle_auto_capture, checked=lambda item: auto_capture_enabled),
        item('Конвертация', toggle_conversion, checked=lambda item: settings["conversion_enabled"]),
        item('────────────', lambda icon, item: None),
        item(f'Статус: {download_status}', lambda icon, item: None),
    ]
    
    # Добавляем информацию о очереди только если в ней есть элементы
    if queue_count > 0:
        menu_items.append(item(f'В очереди{queue_info}', lambda icon, item: None))
        
    menu_items.append(item('Выход', exit_app))
    
    return tuple(menu_items)

is_downloading = False

def check_queue_on_startup():
    """Проверяет наличие URL в очереди при запуске программы"""
    count = get_queue_count()
    if count > 0:
        log_message(f"При запуске обнаружено {count} URL в очереди")
        time.sleep(3)  # Даем программе время инициализироваться
        threading.Thread(target=process_queue).start()

def get_unique_filename(file_path):
    """Проверяет, существует ли файл, и добавляет цифры для уникальности"""
    if not os.path.exists(file_path):
        return file_path
    
    base, ext = os.path.splitext(file_path)
    counter = 1
    new_file_path = f"{base}_{counter}{ext}"
    
    # Ищем уникальное имя, добавляя суффикс _1, _2 и т.д.
    while os.path.exists(new_file_path):
        counter += 1
        new_file_path = f"{base}_{counter}{ext}"
    
    return new_file_path


def update_download_status(status, progress=None):
    global download_status, tray_icon
    download_status = status

    try:
        # Обновляем иконку только если progress указан
        if progress is not None:
            if progress >= 100:
                # Возвращаем стандартную иконку
                tray_icon.icon = Image.open("icon.ico")
            else:
                # Показываем прогресс-бар
                tray_icon.icon = create_progress_icon(progress)
        
        # Обновляем меню
        tray_icon.menu = generate_menu()
    except Exception as e:
        log_message(f"Ошибка при обновлении статуса: {e}")
        # В случае ошибки возвращаем стандартную иконку
        try:
            tray_icon.icon = Image.open("icon.ico")
        except Exception as e:
            log_message(f"Ошибка при восстановлении иконки: {e}")



def create_progress_icon(progress):
    size = 64
    image = Image.new("RGBA", (size, size), (255, 255, 255, 0))
    draw = ImageDraw.Draw(image)

    # Рисуем круговой индикатор прогресса
    draw.arc([4, 4, size - 4, size - 4], start=0, end=int(3.6 * progress), fill="blue", width=6)

    return image


def update_tray_icon(progress):
    global tray_icon
    if progress >= 100:
        icon_path = os.path.join(os.getcwd(), "icon.ico")
        if os.path.exists(icon_path):
            tray_icon.icon = Image.open(icon_path)  # Возвращаем обычную иконку
        else:
            tray_icon.icon = create_image()  # Возвращаем стандартную
    else:
        tray_icon.icon = create_progress_icon(progress)  # Прогресс-бар


# Функции Windows API для работы с буфером
# OpenClipboard = ctypes.windll.user32.OpenClipboard
# CloseClipboard = ctypes.windll.user32.CloseClipboard
# GetClipboardData = ctypes.windll.user32.GetClipboardData
# CF_UNICODETEXT = 13  # Формат текста в буфере

def is_clipboard_available():
    return ctypes.windll.user32.OpenClipboard(None) != 0

def detect_clipboard_change():
    """Отслеживание буфера обмена с обработкой ошибок"""
    global last_clipboard

    try:
        current_clipboard = pyperclip.paste()
        if current_clipboard and current_clipboard != last_clipboard and is_youtube_link(current_clipboard):
            return current_clipboard
    except Exception as e:
        log_message(f"Ошибка доступа к буферу через pyperclip: {e}")
        time.sleep(1)  # Ждем перед следующей попыткой

    return None  # Если буфер не изменился или данные не удалось получить

def get_clipboard_link():
    try:
        text = pyperclip.paste()
        if text and re.match(r'^https?://(www\.)?(youtube\.com|youtu\.be)/', text):
            return text
    except Exception as e:
        log_message(f"Ошибка при получении буфера обмена через pyperclip: {e}")
    
    # Альтернативный метод через Tkinter
    try:
        root = tk.Tk()
        root.withdraw()
        text = root.clipboard_get()
        root.destroy()
        if re.match(r'^https?://(www\.)?(youtube\.com|youtu\.be)/', text):
            return text
    except Exception as e:
        log_message(f"Ошибка при получении буфера обмена через Tkinter: {e}")
    
    return ""

def set_format_mp3(icon, item):
    settings["download_format"] = "mp3"
    save_settings(settings)
    log_message("Формат загрузки изменён на MP3")

def set_format_mp4(icon, item):
    settings["download_format"] = "mp4"
    save_settings(settings)
    log_message("Формат загрузки изменён на MP4")



def set_download_folder():
    root = tk.Tk()
    root.withdraw()
    folder = filedialog.askdirectory()
    if folder:
        settings["download_folder"] = folder
        save_settings(settings)
        messagebox.showinfo("Настройки", f"Папка сохранения изменена на: {folder}")
        log_message(f"Выбрана папка: {folder}")
    root.destroy()

def show_notification(icon, title, message):
    try:
        icon.notify(message, title)
        time.sleep(2)  # Даем уведомлению отобразиться 2 секунды
        icon.notify("", "")  # Очищаем уведомление, чтобы имитировать скрытие
    except Exception as e:
        log_message(f"Ошибка при отображении уведомления: {e}")

def toggle_auto_capture(icon, item):
    global auto_capture_enabled, last_clipboard, last_copy_time, clipboard_snapshot
    auto_capture_enabled = not auto_capture_enabled
    settings["auto_capture_enabled"] = auto_capture_enabled
    save_settings(settings)
    
    if auto_capture_enabled:
        root = tk.Tk()
        root.withdraw()
        clipboard_snapshot = root.clipboard_get()  # Делаем снимок текущего буфера
        root.destroy()
        
        last_clipboard = ""  # Очищаем переменную
        last_copy_time = 0   # Сбрасываем время копирования
    
    log_message(f"Автоперехват ссылок {'включен' if auto_capture_enabled else 'выключен'}")

import subprocess
import json

def get_audio_bitrate(input_file):
    """Определяет битрейт аудио файла"""
    try:
        # Настройка для скрытия консоли
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE

        # Вызов ffprobe через subprocess
        command = [
            "ffprobe",
            "-v", "error",  # Отключаем лишний вывод
            "-select_streams", "a",  # Только аудио
            "-show_entries", "stream=bit_rate",  # Получаем битрейт
            "-of", "json",  # Формат вывода JSON
            input_file
        ]

        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            startupinfo=startupinfo,
            creationflags=subprocess.CREATE_NO_WINDOW,
            universal_newlines=True
        )

        # Парсим вывод
        probe_data = json.loads(result.stdout)
        if 'streams' in probe_data and probe_data['streams']:
            audio_bitrate_bps = int(probe_data['streams'][0]['bit_rate'])
            return f"{audio_bitrate_bps // 1000}k"  # Преобразуем в килобиты

    except Exception as e:
        log_message(f'Не удалось определить битрейт, используем 192k: {e}')
        return '192k'  # Значение по умолчанию

def get_audio_duration(input_file):
    try:
        result = subprocess.run(
            ["ffmpeg", "-i", input_file, "-hide_banner"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            # stdout=subprocess.DEVNULL,  # Скрываем консольное окно
            creationflags=subprocess.CREATE_NO_WINDOW,  # Скрываем окно ffmpeg в Windows
        )
        match = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", result.stderr)
        if match:
            h, m, s = map(float, match.groups())
            return h * 3600 + m * 60 + s
    except Exception as e:
        log_message(f"Ошибка определения длительности аудио: {e}")
    return 180  # Если не получилось получить длину, ставим 3 минуты по умолчанию

def estimate_progress(current_time, total_duration):
    h, m, s = map(float, current_time.split(":"))
    total_seconds = h * 3600 + m * 60 + s
    return min(int((total_seconds / total_duration) * 100), 100)

def convert_to_mp3(input_file):
    """Конвертирует аудио в MP3 с сохранением исходного битрейта"""
    output_file = os.path.splitext(input_file)[0] + '.mp3'
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW  # Скрываем консольное окно

    try:
        total_duration = get_audio_duration(input_file)  # Получаем длину файла
        bitrate = get_audio_bitrate(input_file)  # Получаем битрейт исходного файла
        log_message(f'Битрейт: {bitrate}')

        # Используем кодировку UTF-8 для аргументов командной строки
        with subprocess.Popen(
            [
                "ffmpeg",
                "-i", input_file,  # Входной файл
                "-threads", "4",  # Используем 4 потока (можно увеличить)
                "-f", "mp3",  # Формат выходного файла
                "-acodec", "libmp3lame",  # Кодек MP3
                "-preset", "fast",  # Используем более быстрый пресет
                "-b:a", bitrate,  # Используем исходный битрейт
                "-y",  # Перезаписать выходной файл, если он существует
                output_file
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            startupinfo=startupinfo,
            universal_newlines=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            encoding='utf-8'  # Явно указываем кодировку
        ) as process:

            for line in process.stderr:
                if "time=" in line:
                    match = re.search(r'time=(\d+:\d+:\d+\.\d+)', line)
                    if match:
                        time_value = match.group(1)
                        percent = estimate_progress(time_value, total_duration)
                        update_download_status("Конвертация...", percent)

            process.wait()

            if process.returncode == 0:
                os.remove(input_file)
                update_download_status("Готово!", 100)
                log_message(f'Конвертация завершена: {output_file}')
                return output_file
            else:
                update_download_status("Ошибка!")
                log_message(f"Ошибка при конвертации в MP3: код {process.returncode}")
                return None

    except Exception as e:
        update_download_status("Ошибка!")
        log_message(f'Ошибка конвертации в MP3: {e}')
        return None

def convert_to_mp4(input_file):
    output_file = input_file.rsplit(".", 1)[0] + ".mp4"
    
    # Создаём объект startupinfo для скрытия консольного окна
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW  # Скрыть консольное окно

    try:
        # Теперь используем subprocess для выполнения команды ffmpeg с указанием кодировки
        process = subprocess.run(
            ["ffmpeg", "-i", input_file, "-vcodec", "copy", "-acodec", "copy", "-y", output_file],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, 
            startupinfo=startupinfo,
            encoding='utf-8',  # Явно указываем кодировку
            creationflags=subprocess.CREATE_NO_WINDOW
        )

        # Если процесс завершился без ошибок, удаляем исходный файл
        if process.returncode == 0:
            os.remove(input_file)
            log_message(f'Конвертация завершена: {output_file}')
            return output_file
        else:
            log_message(f"Ошибка при конвертации в MP4: {process.stderr}")
            return None

    except Exception as e:
        log_message(f"Ошибка конвертации: {e}")
        log_message('Конвертация не удалась')
        return None

last_copied_url = None  # Глобальная переменная для отслеживания последней скопированной ссылки

def is_youtube_link(text):
    return re.match(r'^https?://(www\.)?(youtube\.com|youtu\.be)/', text) is not None

def clear_clipboard():
    """Очистка буфера обмена с повторными попытками"""
    current_content = pyperclip.paste()
    if not is_youtube_link(current_content):  # Проверяем, является ли содержимое ссылкой
        return
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            pyperclip.copy("")  # Очищаем буфер обмена
            log_message("Буфер обмена успешно очищен.")
            return
        except Exception as e:
            log_message(f"Ошибка при очистке буфера (попытка {attempt + 1}): {e}")
            time.sleep(0.5)  # Ждем перед следующей попыткой
    log_message("Не удалось очистить буфер обмена после нескольких попыток.")

download_stages = 0
current_stage = 0

def progress_hook(d):
    global download_stages, current_stage, download_speed, global_file_size, global_downloaded, current_format
    
    if d["status"] == "downloading":
        # При первом вызове или смене файла обновляем информацию об этапах
        if download_stages == 0 or current_stage == 0:
            # Определяем количество этапов на основе запрошенного формата
            if settings["download_format"] == "mp3":
                # Для mp3 обычно скачивается только аудио
                download_stages = 1
            else:
                # Для других форматов может быть 1 или 2 этапа в зависимости от качества
                # Высокое качество обычно требует раздельной загрузки аудио и видео
                download_stages = 2 if '+' in current_format else 1
            current_stage = 1
            # log_message(f"Установлено количество этапов: {download_stages}")
        
        # Получаем общий размер файла
        if "total_bytes" in d and d["total_bytes"] is not None:
            global_file_size = d["total_bytes"]
        elif "total_bytes_estimate" in d and d["total_bytes_estimate"] is not None:
            global_file_size = d["total_bytes_estimate"]
            
        # Получаем количество загруженных байтов
        if "downloaded_bytes" in d and d["downloaded_bytes"] is not None:
            global_downloaded = d["downloaded_bytes"]
            
        # Получаем скорость
        if "speed" in d and d["speed"] is not None:
            download_speed = format_speed(d["speed"])
            
        percent = d["_percent_str"].strip().replace("%", "")
        # Для одноэтапной загрузки просто берем процент, для многоэтапной учитываем текущий этап
        if download_stages == 1:
            normalized_progress = int(float(percent))
        else:
            normalized_progress = (int(float(percent)) + (current_stage - 1) * 100) / download_stages
        
        update_download_status(f"Загрузка ({current_stage}/{download_stages})...", int(normalized_progress))
    
    elif d["status"] == "finished":
        # log_message(f"Завершен этап {current_stage} из {download_stages}")
        
        if current_stage < download_stages:
            current_stage += 1
            # log_message(f"Переход к этапу {current_stage}")
        else:
            update_download_status("Готово!", 100)
            # Сбрасываем счетчики
            global_file_size = 0
            global_downloaded = 0
            download_stages = 0
            current_stage = 0
            # log_message("SUCCSESS Загрузка завершена")

def download_video(url, from_queue=False):
    global last_clipboard, download_stages, current_stage, global_file_size, global_downloaded, current_format, is_downloading
    
    # Проверяем, не идет ли уже загрузка
    if is_downloading:
        log_message(f"INFO Загрузка уже идет, добавляем URL в очередь: {url}")
        add_to_queue(url)
        return
    
    is_downloading = True
    
    # Если загрузка из очереди, не добавляем URL снова
    if not from_queue:
        # Проверяем, есть ли URL уже в очереди
        if url in get_queue_urls():
            log_message(f"URL уже в очереди")
            is_downloading = False
            return    
        
    # Сбрасываем счетчики перед новой загрузкой
    download_stages = 0
    current_stage = 0
    global_file_size = 0
    global_downloaded = 0
    
    if not url:
        is_downloading = False
        return

    save_path = settings["download_folder"]
    
    try:
        with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
            info = ydl.extract_info(url, download=False)

            # Проверяем, является ли видео премьерой
            if info.get('is_premiere', False) or info.get('live_status', '') == 'is_upcoming':
                log_message(f"Пропуск премьеры: {url}")
                messagebox.showinfo("Премьера", "Это видео еще не вышло (премьера). Загрузка невозможна.")
                is_downloading = False
                
                # Если URL был из очереди, удаляем его
                if from_queue:
                    remove_from_queue(url)
                
                return
            
            # Получаем заголовок видео, сохраняя UTF-8 символы
            # но заменяем недопустимые символы для имени файла
            video_title = info.get("title", "video")
            safe_title = re.sub(r'[\\/*?:"<>|]', "_", video_title)  # Заменяем запрещенные символы в Windows
            
            if settings["conversion_enabled"]:
                video_ext = settings["download_format"]
            else:
                video_ext = info.get("ext", "mp4")

            file_name = f"{safe_title}.{video_ext}"
            file_path = os.path.join(save_path, file_name)
            
            log_message(f"INFO Планируется загрузка файла: {file_path}")
            
            if os.path.exists(file_path):
                root = tk.Tk()
                root.withdraw()
                response = messagebox.askyesno("Повторная загрузка", f"Файл '{safe_title}.{video_ext}' уже существует. Хотите загрузить его снова?")
                root.destroy()
                if not response:
                    log_message(f"Пользователь отменил повторную загрузку: {url}")
                    is_downloading = False
                    return
                
    except yt_dlp.utils.DownloadError as e:
        log_message(f"Видео недоступно: {url}. Ошибка: {e}")
        if from_queue:
            remove_from_queue(url)  # Удаляем недоступное видео из очереди
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
    tray_icon.icon = Image.open("icon.ico")
    
    # log_message(f"Попытка загрузки видео: {url}")
    # log_message(f"Путь сохранения: {save_path}")
    
    quality_map = {
        "1080p": "bestvideo[height<=1080]+bestaudio/best",
        "720p": "bestvideo[height<=720]+bestaudio/best",
        "480p": "bestvideo[height<=480]+bestaudio/best"
    }
    selected_quality = quality_map.get(settings["video_quality"], "best")

    ydl_opts = {
        # Сохраняем UTF-8 символы в имени файла
        'outtmpl': os.path.join(save_path, '%(title)s.%(ext)s'),
        'cookies': 'cookies.txt',
        'cookies-from-browser': True,
        'browser': 'chrome',
        # Добавляем опции для корректной обработки не-ASCII символов
        'restrict_filenames': False,  # Отключаем ограничение на имена файлов
        'windowsfilenames': False,    # Не используем Windows-специфичные ограничения
    }
    
    # Устанавливаем правильный формат в зависимости от настроек
    if settings["download_format"] == "mp3":
        # Для MP3 нужен только аудио поток
        ydl_opts['format'] = 'bestaudio[ext=m4a]/best[ext=mp3]'
    else:
        # Для видео используем выбранное качество
        ydl_opts['format'] = quality_map.get(settings["video_quality"], "best")
    
    # Сохраняем выбранный формат в глобальной переменной
    current_format = ydl_opts['format']
    # log_message(f"Установлен формат загрузки: {current_format}")
                
    if settings["conversion_enabled"]:
        ydl_opts['merge_output_format'] = 'mp4'
    
    try:
        threading.Thread(target=show_notification, args=(tray_icon, "YouTube Downloader", "Видео загружается...")).start()
        
        ydl_opts["progress_hooks"] = [progress_hook]

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            downloaded_file = ydl.prepare_filename(info)
            
            # Добавляем в историю загрузок после успешной загрузки
            add_to_history(
                url=url,
                title=info.get('title', 'Неизвестное видео'),
                format_type=settings["download_format"]
            )
            
            log_message(f"Файл загружен: {downloaded_file}")
        
        if settings["conversion_enabled"]:
            if settings["download_format"] == "mp3" and downloaded_file.endswith((".m4a", ".webm", ".mp4")):
                log_message("INFO Конвертация в MP3...")
                converted_file = convert_to_mp3(downloaded_file)
                if converted_file:
                    log_message(f"SUCCESS Конвертация завершена: {converted_file}")
            elif settings["download_format"] == "mp4" and downloaded_file.endswith(".webm"):
                log_message("INFO Конвертация в MP4...")
                converted_file = convert_to_mp4(downloaded_file)
                if converted_file:
                    log_message(f"SUCCESSКонвертация завершена: {converted_file}")
        else:
            log_message("SUCCESS Файл сохранен в исходном формате")

        # Если URL был из очереди, удаляем его после успешной загрузки
        if from_queue:
            remove_from_queue(url)    
        
        threading.Thread(target=show_notification, args=(tray_icon, "YouTube Downloader", "Видео загружено успешно!")).start()

        log_message(f"SUCCESS Загрузка завершена: {url}")
        last_clipboard = ""
        time.sleep(1)
        clear_clipboard()

    except Exception as e:
        error_message = f"Ошибка загрузки: {e}"
        threading.Thread(target=show_notification, args=(tray_icon, "YouTube Downloader", error_message)).start()
        log_message(error_message)

        # Удаляем URL из очереди в случае ошибки
        if from_queue:
            remove_from_queue(url)
    finally:
        is_downloading = False
        
        # После завершения загрузки проверяем очередь
        queue_count = get_queue_count()
        if queue_count > 0:
            log_message(f"INFO В очереди остались URL ({queue_count}), запускаем обработку")
            # Даем системе немного времени отдохнуть
            time.sleep(1)
            threading.Thread(target=process_queue).start()
        else:
            # Если очередь пуста, явно это фиксируем
            # log_message("Очередь пуста после завершения загрузки")
            clear_queue_file()
            # Обновляем иконку и меню
            update_download_status("Ожидание...", 100)

def update_download_status(status, progress=None):
    global download_status, tray_icon, download_speed, global_file_size, global_downloaded
    download_status = status

    try:
        # Обновляем иконку только если progress указан
        if progress is not None:
            if progress >= 100:
                # Возвращаем стандартную иконку
                tray_icon.icon = Image.open("icon.ico")
            else:
                # Показываем прогресс-бар
                tray_icon.icon = create_progress_icon(progress)
        
        # Формируем текст при наведении
        hover_text = "YouTube Downloader"
        
        if status.startswith("Загрузка") and download_speed != "0 KB/s":
            # Добавляем прогресс в виде "Скорость: X MB/s (Y MB из Z MB)"
            downloaded_str = format_size(global_downloaded)
            total_str = format_size(global_file_size) if global_file_size > 0 else "???"
            
            hover_text = f"Скорость: {download_speed} ({downloaded_str} из {total_str})"
        else:
            hover_text = f"{hover_text} - {status}"
        
        tray_icon.title = hover_text  # Обновляем текст при наведении
        
        # Обновляем меню
        tray_icon.menu = generate_menu()
    except Exception as e:
        log_message(f"Ошибка при обновлении статуса: {e}")
        # В случае ошибки возвращаем стандартную иконку
        try:
            tray_icon.icon = Image.open("icon.ico")
        except Exception as e:
            log_message(f"Ошибка при восстановлении иконки: {e}")   

last_copy_time = 0

def clipboard_monitor():
    global last_clipboard
    last_clipboard = ""
    # log_message("Мониторинг буфера обмена запущен")

    while True:
        time.sleep(1)  # Проверяем буфер каждую секунду
        if not auto_capture_enabled:
            continue

        try:
            current_clipboard = detect_clipboard_change()
            if not current_clipboard or current_clipboard == last_clipboard:
                continue  # Если буфер не изменился или ссылка уже обработана, ничего не делаем

            # Обновляем last_clipboard, чтобы избежать повторной обработки
            last_clipboard = current_clipboard

            if current_clipboard.startswith("https://www.youtube.com"):
                log_message(f"INFO Обнаружена новая ссылка: {current_clipboard}")
                
                # Проверяем, является ли ссылка плейлистом
                if "playlist?list=" in current_clipboard:
                    log_message("INFO Обнаружена ссылка на плейлист")
                    # Запускаем функцию выбора видео из плейлиста в новом потоке
                    threading.Thread(target=download_playlist_with_selection, args=(current_clipboard,)).start()
                # Проверяем, является ли ссылка каналом (варианты URL каналов YouTube)
                elif "/channel/" in current_clipboard or "/c/" in current_clipboard or "/user/" in current_clipboard or "/@" in current_clipboard:
                    log_message("INFO Обнаружена ссылка на канал")
                    # Запускаем функцию выбора видео с канала в новом потоке
                    threading.Thread(target=download_channel_with_selection, args=(current_clipboard,)).start()
                else:
                    # Обычная ссылка на видео
                    if is_downloading:
                        log_message("INFO Загрузка уже идет, добавляем в очередь")
                        add_to_queue(current_clipboard)
                    else:
                        log_message("INFO Запускаем загрузку напрямую")
                        threading.Thread(target=download_video, args=(current_clipboard,)).start()
                
                if current_clipboard.startswith("https://www.youtube.com"):
                    time.sleep(1)  # Пауза для стабилизации
                    # last_clipboard = ""  # Сброс последнего значения

        except Exception as e:
            log_message(f"ERROR Ошибка при мониторинге буфера обмена: {e}")



def start_monitoring():
    threading.Thread(target=clipboard_monitor, daemon=True).start()

def create_image():
    image = Image.new('RGB', (64, 64), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle([16, 16, 48, 48], fill="red", outline="black")
    return image

def show_settings(icon, item):
    # log_message("Открыто окно выбора папки")
    threading.Thread(target=set_download_folder).start()

def exit_app(icon, item):
    # log_message("Выход из программы")
    icon.stop()
    os._exit(0)

def toggle_download_format(icon, item):
    settings["download_format"] = "mp3" if settings["download_format"] == "mp4" else "mp4"
    save_settings(settings)
    messagebox.showinfo("Настройки", f"Формат загрузки изменен на: {settings['download_format']}")
    log_message(f"Формат загрузки изменен на: {settings['download_format']}")


from ttkwidgets.autocomplete import AutocompleteEntry

def search_youtube_videos():
    """Отображает окно для поиска видео через YouTube API"""
        
    try:
        # Создаем окно поиска
        root = tk.Tk()
        root.title("Расширенный поиск YouTube")
        root.geometry("900x700")
        
        # Центрируем окно
        root.update_idletasks()
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        x = (screen_width - 900) // 2
        y = (screen_height - 700) // 2
        root.geometry(f"900x700+{x}+{y}")
        
        # Делаем окно видимым и выводим на передний план
        root.deiconify()
        root.lift()
        root.focus_force()
        
        # На Windows также можно использовать:
        if os.name == 'nt':
            root.attributes('-topmost', True)
            root.update()
            root.attributes('-topmost', False)
        
        # Создаем фреймы
        main_frame = ttk.Frame(root, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Фрейм для верхней части с полями поиска
        search_frame = ttk.LabelFrame(main_frame, text="Параметры поиска", padding=10)
        search_frame.pack(fill=tk.X, pady=(0, 10))

        # Первая строка - поисковый запрос
        query_frame = ttk.Frame(search_frame)
        query_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(query_frame, text="Поисковый запрос:").pack(side=tk.LEFT, padx=(0, 5))
        search_var = tk.StringVar()
        autocomplete_list = []  # Пустой список для автозаполнения
        search_entry = AutocompleteEntry(query_frame, textvariable=search_var, width=50, completevalues=autocomplete_list)
        search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))
        search_entry.focus()  # Устанавливаем фокус на поле ввода

        button_frame = tk.Frame(root)
        button_frame.pack(fill=tk.X, padx=5, pady=(0, 5))

        clear_button = tk.Button(button_frame, text="🗑 Очистить лог", command=clear_log)
        clear_button.pack(side=tk.RIGHT, padx=5)

        log_frame = ttk.Frame(root)
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
                    root.after(10, lambda: handle_paste())
                    return "break"  # Отменяем стандартную обработку
            # Обработка Shift+Insert (код 118)
            elif event.keysym == "Insert" and event.state & 0x0001:
                if not paste_in_progress:
                    paste_in_progress = True
                    root.after(10, lambda: handle_paste())
                    return "break"  # Отменяем стандартную обработку
        
        def handle_paste():
            nonlocal paste_in_progress
            try:
                clipboard_text = root.clipboard_get()
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
            menu.tk_popup(event.x_root, event.y_root)

        # Привязка событий
        search_entry.bind("<Key>", on_key_press)  # Обработка всех клавиш
        search_entry.bind("<Button-3>", show_context_menu)  # Правый клик
        
        
        # Фрейм для дополнительных параметров поиска
        options_frame = ttk.Frame(search_frame)
        options_frame.pack(fill=tk.X, pady=5)

        # Добавляем галочку "Поиск по описаниям"
        search_options_frame = ttk.Frame(search_frame)
        search_options_frame.pack(fill=tk.X, pady=5)
        
        search_in_descriptions_var = tk.BooleanVar(value=False)
        search_in_descriptions_check = ttk.Checkbutton(
            search_options_frame, 
            text="Поиск по описаниям", 
            variable=search_in_descriptions_var
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
        
        # Тип контента
        ttk.Label(options_frame, text="Тип:").pack(side=tk.LEFT, padx=(0, 5))
        type_var = tk.StringVar(value="video")
        type_combo = ttk.Combobox(options_frame, textvariable=type_var, width=15, 
                                  values=["video", "channel", "playlist"])
        type_combo.pack(side=tk.LEFT, padx=(0, 10))
        
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
                                         values=["10", "20", "30", "40", "50", "100"])
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
        
        # Настройка заголовков колонок
        tree.heading("title", text="Название")
        tree.heading("channel", text="Канал")
        tree.heading("duration", text="Длительность")
        
        # Настройка ширина колонок
        tree.column("title", width=500, anchor=tk.W)
        tree.column("channel", width=200, anchor=tk.W)
        tree.column("duration", width=100, anchor=tk.CENTER)
        
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=tree.yview)
        
        # Словарь для хранения URL видео
        video_urls = {}

        def format_duration(duration):
            """Преобразует ISO 8601 длительность в читаемый формат"""
            if not duration:
                return 'N/A'
                
            try:
                # Убираем 'PT' в начале
                duration = duration[2:]
                
                # Инициализируем переменные
                hours = "00"
                minutes = "00"
                seconds = "00"
                
                # Обрабатываем часы
                if 'H' in duration:
                    hours, duration = duration.split('H')
                    hours = hours.zfill(2)
                
                # Обрабатываем минуты
                if 'M' in duration:
                    minutes, duration = duration.split('M')
                    minutes = minutes.zfill(2)
                
                # Обрабатываем секунды
                if 'S' in duration:
                    seconds = duration.replace('S', '')
                    seconds = seconds.zfill(2)
                
                # Если длительность больше часа, возвращаем полный формат
                if hours != "00":
                    return f"{hours}:{minutes}:{seconds}"
                # Иначе возвращаем только минуты и секунды
                else:
                    return f"{minutes}:{seconds}"
                    
            except Exception as e:
                log_message(f"Ошибка форматирования длительности '{duration}': {e}")
                return 'N/A'


        # Функции для контекстного меню
        def copy_url():
            """Копирует URL выбранного видео в буфер обмена"""
            selected = tree.selection()[0] if tree.selection() else None
            if selected and selected in video_urls:
                url = video_urls[selected]
                pyperclip.copy(url)
                status_var.set("URL скопирован в буфер обмена")

        def add_to_download_queue():
            """Добавляет выбранное видео в очередь загрузки"""
            selected = tree.selection()[0] if tree.selection() else None
            if selected and selected in video_urls:
                url = video_urls[selected]
                add_to_queue(url)
                status_var.set("Добавлено в очередь загрузки")
                
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

        def format_invidious_duration(seconds):
            """Преобразует секунды в формат ЧЧ:ММ:СС"""
            try:
                if not seconds:
                    return "00:00:00"
                    
                seconds = int(seconds)
                hours = seconds // 3600
                minutes = (seconds % 3600) // 60
                secs = seconds % 60
                return f"{hours:02d}:{minutes:02d}:{secs:02d}"
            except Exception as e:
                log_message(f"Ошибка форматирования времени Invidious: {e}")
                return "00:00:00"    
            

        def search_via_invidious():
            """Выполняет поиск видео через Invidious API"""
            query = search_var.get().strip()
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
             
                # Формируем URL API запроса
                api_endpoint = f"{invidious_url}/api/v1/search?"
                
                # Определяем максимальное число страниц для запроса
                # Обычно Invidious возвращает ~20 результатов на страницу, но это может варьироваться
                pages_to_fetch = min(10, (max_results + 9) // 10)  # Максимум 10 страниц, чтобы избежать длительных запросов
                # log_message(f"Запланировано запросить до {pages_to_fetch} страниц результатов Invidious")
                
                all_results = []
                empty_pages_count = 0  # Счетчик пустых страниц подряд
                
                for page in range(1, pages_to_fetch + 1):
                    # Если у нас уже достаточно результатов, останавливаемся
                    if len(all_results) >= max_results:
                        log_message(f"Достигнуто требуемое количество результатов ({max_results}), прекращаем запросы")
                        break
                        
                    # Подготавливаем параметры запроса для текущей страницы
                    params = {
                        'q': query,                 # Поисковый запрос
                        'type': invidious_type,     # Тип контента
                        'sort_by': invidious_sort,  # Порядок сортировки
                        'page': page,               # Текущая страница результатов
                    }
                    
                    # log_message(f"Invidious API запрос страницы {page}: {api_endpoint} с параметрами {params}")
                    status_var.set(f"Отправка запроса к Invidious API (страница {page}/{pages_to_fetch})...")
                    
                    # Выполняем запрос с увеличенным таймаутом
                    response = requests.get(api_endpoint, params=params, timeout=15)
                    
                    # log_message(f"Ответ от Invidious API получен (страница {page}). Код: {response.status_code}")
                    
                    if response.status_code != 200:
                        status_var.set(f"Ошибка Invidious API: {response.status_code}")
                        log_message(f"Ошибка Invidious API (страница {page}): {response.status_code}, {response.text}")
                        break  # Прекращаем запросы при ошибке
                    
                    # Разбираем JSON ответ
                    page_data = response.json()
                    
                    if not page_data:
                        log_message(f"Страница {page} не содержит результатов")
                        empty_pages_count += 1
                        
                        # Если получили 2 пустые страницы подряд, прекращаем запросы
                        if empty_pages_count >= 2:
                            log_message("Получено 2 пустые страницы подряд, завершаем пагинацию")
                            break
                            
                        # Иначе продолжаем запросы
                        continue
                    else:
                        # Сбрасываем счетчик пустых страниц, если получены данные
                        empty_pages_count = 0
                    
                    # Добавляем результаты страницы к общему списку
                    all_results.extend(page_data)
                    # log_message(f"Получено {len(page_data)} результатов со страницы {page}, всего: {len(all_results)}")
                    
                    # Делаем небольшую паузу между запросами, чтобы не перегружать сервер
                    if page < pages_to_fetch:
                        time.sleep(0.3)
                
                # Ограничиваем количество результатов
                results = all_results[:max_results] if len(all_results) > max_results else all_results
                
                # log_message(f"Всего получено {len(all_results)} результатов от Invidious API, возвращается {len(results)}")
                return results
                
            except Exception as e:
                log_message(f"Ошибка при поиске через Invidious API: {e}")
                log_message(f"Трассировка: {traceback.format_exc()}")
                status_var.set(f"Ошибка: {str(e)}")
                return None
        
        # Функция для поиска через официальный YouTube API

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
                    'q': query,
                    'part': 'snippet',
                    'maxResults': min(50, max_results),  # Максимум 50 за один запрос
                    'type': search_type,
                    'order': order
                }
                
                all_items = []
                next_page_token = None
                total_results = 0
                
                # Делаем запросы до тех пор, пока не достигнем максимального количества результатов
                # или пока API не вернет пустую страницу
                
                max_iterations = 10
                iteration_count = 0
                api_request_count = 0  # Глобальная переменная для подсчёта запросов
                
                while True:
                    # log_message("Начало итерации цикла")
                    iteration_count += 1

                    if iteration_count > max_iterations:
                        log_message("Цикл завершён из-за превышения максимального количества итераций")
                        break

                    if next_page_token:
                        params['pageToken'] = next_page_token

                    response = requests.get(base_url, params=params)
                    api_request_count += 1  # Увеличиваем счётчик запросов
                    # log_message(f"Запрос #{api_request_count} к API: {response.url}")
                    log_message(f"SUCCESS Ответ от API получен. Код статуса: {response.status_code}")

                    if response.status_code != 200:
                        log_message(f"Ошибка API: {response.status_code}")
                        break

                    data = response.json()
                    items = data.get('items', [])
                    total_results = data.get('pageInfo', {}).get('totalResults', 0)

                    # Добавляем текущие результаты
                    all_items.extend(items)

                    # Проверяем, достигли ли мы максимального количества результатов
                    if len(all_items) >= max_results or not data.get('nextPageToken'):
                        # log_message("Цикл завершён: достигнуто максимальное количество результатов или отсутствует токен следующей страницы")
                        break

                    # Получаем токен следующей страницы
                    next_page_token = data.get('nextPageToken')
                    log_message(f"Получен токен следующей страницы: {next_page_token}")

                log_message(f"INFO Всего выполнено запросов к API: {api_request_count}")
                
                # Получаем описания и длительности для всех найденных видео
                video_descriptions.clear()
                video_durations = {}
                
                # Разбиваем video_ids на части по 50, так как API имеет ограничение
                video_ids = [item['id']['videoId'] for item in all_items if item['id'].get('kind') == 'youtube#video']
                for i in range(0, len(video_ids), 50):
                    chunk = video_ids[i:i+50]
                    videos_url = "https://www.googleapis.com/youtube/v3/videos"
                    videos_params = {
                        'key': api_key,
                        'part': 'snippet,contentDetails',
                        'id': ','.join(chunk),
                        'maxResults': len(chunk)
                    }
                    videos_response = requests.get(videos_url, params=videos_params)
                    if videos_response.status_code == 200:
                        videos_data = videos_response.json()
                        for video in videos_data.get('items', []):
                            video_id = video['id']
                            description = video['snippet']['description']
                            duration = video['contentDetails']['duration']
                            video_descriptions[video_id] = description
                            video_durations[video_id] = duration
                            
                
                # Фильтруем результаты
                filtered_items = []
                for item in all_items:
                    if item['id'].get('kind') != 'youtube#video':
                        continue
                        
                    video_id = item['id']['videoId']
                    if search_in_descriptions:
                        description = video_descriptions.get(video_id, '')
                        if query.lower() not in description.lower():
                            continue
                            
                    # Добавляем длительность в данные о видео
                    duration = video_durations.get(video_id)
                    item['duration'] = format_duration(duration) if duration else 'N/A'
                    filtered_items.append(item)
                    if len(filtered_items) >= max_results:
                        break
                
                total_results = data.get('pageInfo', {}).get('totalResults', 0)
                log_message(f"INFO Всего найдено результатов: {min(total_results, max_results)}")
                log_message(f"INFO После фильтрации осталось: {len(filtered_items)}")
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
                
                # Проверяем, загружено ли описание
                if video_id not in video_descriptions or video_descriptions[video_id] == "Описание будет загружено при запросе":
                    status_var.set("Загрузка описания...")
                    # log_message(f"Загрузка описания для видео {video_id}")
                    # Проверяем, какой метод поиска используется
                    use_alternative = use_alternative_api_var.get()
                    if use_alternative:
                        # Загружаем описание через BeautifulSoup
                        description = fetch_description_with_bs(video_url)
                        log_message(f"Описание загружено через BS")
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
        
                
                # Создаем новое окно для отображения описания
                desc_window = tk.Toplevel(root)
                desc_window.title("Описание видео")
                desc_window.geometry("600x400")
                
                # Центрируем окно
                desc_window.update_idletasks()
                screen_width = desc_window.winfo_screenwidth()
                screen_height = desc_window.winfo_screenheight()
                x = (screen_width - 600) // 2
                y = (screen_height - 400) // 2
                desc_window.geometry(f"600x400+{x}+{y}")
                
                # Делаем окно модальным
                desc_window.transient(root)
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
                
                # Ждем, пока окно закроется
                desc_window.wait_window()

        # Обновляем контекстное меню с новой функцией
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

        # Функция для выполнения поиска
# Функция для выполнения поиска (выбирает API в зависимости от настроек)
        def perform_search():
            """Выполняет поиск видео через выбранный API"""
            # Очищаем предыдущие результаты
            for item in tree.get_children():
                tree.delete(item)
            video_urls.clear()
            video_descriptions.clear()
            # log_message("Очистка предыдущих результатов")
            
            # Определяем, какой метод поиска использовать
            use_alternative = use_alternative_api_var.get()
            
            if use_alternative:
                log_message("INFO Выбран поиск через Invidious API")
                results = search_via_invidious()
            else:
                log_message("INFO Выбран поиск через официальный YouTube API")
                results = search_via_youtube_api()
            
            if not results:
                status_var.set("Поиск не дал результатов или произошла ошибка")
                log_message("ERROR Поиск не дал результатов или произошла ошибка")
                return
            
            # В зависимости от API разные форматы результатов
            if use_alternative:
                # Обработка результатов Invidious API
                added_count = 0
                for item in results:
                    try:
                        # log_message(f"Полный ответ API для видео: {item}")
                        if item.get('type') != 'video':
                            continue
                            
                        video_id = item.get('videoId')
                        title = decode_html_entities(item.get('title', 'Без названия'))
                        channel = decode_html_entities(item.get('author', 'Неизвестный канал'))
                        
                        # В Invidious API длительность в секундах
                        duration = format_invidious_duration(item.get('lengthSeconds', 0))
                        
                        # Сохраняем URL видео
                        video_url = f"https://www.youtube.com/watch?v={video_id}"
                        added_count = added_count + 1
                        
                        # Получаем описание через BeautifulSoup
                        # description = fetch_description_with_bs(video_url)
                        # video_descriptions[video_id] = description
                        # log_message(f"Описание для видео {video_id}: {description}")
        
                        
                        # Добавляем в таблицу
                        item_id = tree.insert('', tk.END, values=(title, channel, duration))
                        video_urls[item_id] = video_url
                    except Exception as e:
                        log_message(f"ERROR Ошибка при обработке результата Invidious: {e}")
                
                status_var.set(f"Найдено результатов: {added_count}")
                # log_message(f"Добавлено {added_count} видео в таблицу (Invidious API)")
            else:
                # Обработка результатов YouTube API
                youtube_items = results.get('items', [])
                video_stats = results.get('video_stats', {})
                
                filtered_items = []
                for item in youtube_items:
                    try:
                        if item['id'].get('kind') != 'youtube#video':
                            continue
                            
                        video_id = item['id']['videoId']
                        title = decode_html_entities(item['snippet']['title'])
                        channel = decode_html_entities(item['snippet']['channelTitle'])
                        
                        # Если включен поиск по описаниям, проверяем наличие запроса
                        query = search_var.get().strip().lower()
                        if search_in_descriptions_var.get():
                            description = decode_html_entities(video_descriptions.get(video_id, ''))
                            if not description or query not in description.lower():
                                continue
                                
                        # Получаем длительность
                        # duration = format_duration(video_stats.get(video_id, {}).get('duration', ''))
                        duration = item.get('duration', 'N/A')
                        
                        # Сохраняем URL видео
                        video_url = f"https://www.youtube.com/watch?v={video_id}"
                        item_id = tree.insert('', tk.END, values=(title, channel, duration))
                        video_urls[item_id] = video_url
                        filtered_items.append(item)
                    except Exception as e:
                        log_message(f"ERROR Ошибка при обработке результата YouTube API: {e}")

                status_var.set(f"Найдено результатов: {len(filtered_items)}")
                log_message(f"INFO Добавлено {len(filtered_items)} видео в таблицу (YouTube API)")

  

        # Кнопки в нижней части окна
        bottom_button_frame = ttk.Frame(main_frame)
        bottom_button_frame.pack(fill=tk.X, pady=10)
        
        ttk.Button(bottom_button_frame, text="Загрузить выбранное", 
                  command=add_to_download_queue).pack(side=tk.RIGHT, padx=5)
        ttk.Button(bottom_button_frame, text="Закрыть", 
                  command=root.destroy).pack(side=tk.RIGHT, padx=5)
        
        # Запускаем главный цикл
        root.mainloop()
        
    except Exception as e:
        log_message(f"ERROR Ошибка в окне поиска YouTube: {e}")
        log_message(f"Трассировка: {traceback.format_exc()}")
        messagebox.showerror("Ошибка", f"Произошла ошибка: {e}")

def run_tray():
    global tray_icon
    icon_path = os.path.join(os.getcwd(), "icon.ico")

    if not os.path.exists(icon_path):
        log_message("Файл icon.ico не найден, используется стандартная иконка")
        image = create_image()
    else:
        image = Image.open(icon_path)  # Загружаем реальную иконку

    # Инициализируем с начальным hover_text
    tray_icon = Icon("YouTube Downloader", image, "YouTube Downloader - Ожидание...", menu=generate_menu())
    # log_message("Иконка в трее запущена")
    tray_icon.run()



# Очищаем буфер обмена при запуске программы
clear_clipboard()
start_monitoring()
ensure_queue_file_exists()
threading.Thread(target=run_tray, daemon=True).start()  # Трей в отдельном потоке
threading.Thread(target=check_queue_on_startup).start()


# Принудительно держим программу открытой, чтобы она не завершалась
while True:
    time.sleep(10)
