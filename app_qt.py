"""
YouTube Downloader - Новый интерфейс на PyQt6
Запуск: python app_qt.py
"""

import sys
import os

# Windows: устанавливаем AppUserModelID для работы уведомлений
if sys.platform == 'win32':
    import ctypes
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('YTD.YouTubeDownloader.1.0')

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QSystemTrayIcon, QMenu, QMessageBox,
    QFileDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGraphicsOpacityEffect,
    QTableWidget, QTableWidgetItem, QHeaderView, QPushButton, QAbstractItemView,
    QInputDialog
)
from PyQt6.QtGui import QIcon, QAction, QPixmap, QPainter, QColor, QFont, QPen
from PyQt6.QtCore import (
    Qt, QTimer, QObject, pyqtSignal, QMetaObject, Q_ARG,
    QPropertyAnimation, QEasingCurve, QPoint, QRect
)

import json
import styles as _styles
from styles import COLORS_MINIMAL as COLORS
from config import settings, save_settings, load_settings, SETTINGS_FILE, format_invidious_duration, atomic_write_json as _atomic_write_json
from logger import log_message
from queues import get_queue_count, ensure_queue_file_exists
from download_history import load_download_history

_CHANNEL_WINDOWS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "channel_windows_state.json")


# ============================================================
# Thread-safe UI-колбэки для download.py и queue_window_qt.py
# ============================================================

class _TraySignals(QObject):
    """Сигналы для thread-safe обновления трея из любого потока"""
    notify = pyqtSignal(str, str)           # (title, message)
    status = pyqtSignal(str, int)           # (status_text, progress)
    open_video_list = pyqtSignal(str, str)  # (url, mode: "channel"|"playlist")


_signals = None  # Инициализируется после создания QApplication
_last_status_time = 0  # Для throttling обновлений прогресса


def _bridge_show_notification(title, message):
    """Показывает уведомление (thread-safe)"""
    if _signals:
        _signals.notify.emit(str(title), str(message))


def _bridge_open_channel_window(url):
    """Открывает окно канала в главном потоке (thread-safe)."""
    if _signals:
        _signals.open_video_list.emit(str(url), "channel")


def _bridge_open_playlist_window(url):
    """Открывает окно плейлиста в главном потоке (thread-safe)."""
    if _signals:
        _signals.open_video_list.emit(str(url), "playlist")


def _bridge_update_download_status(status, progress=None, downloaded=0, total_size=0):
    """Обновляет статус загрузки (thread-safe, throttled)"""
    global _last_status_time
    import time
    now = time.time()
    prog = int(progress) if progress is not None else -1
    # Пропускаем 100% и ключевые статусы всегда, остальное — не чаще 2 раз/сек
    if prog < 100 and status == "Загрузка..." and (now - _last_status_time) < 0.5:
        return
    _last_status_time = now
    if _signals:
        _signals.status.emit(str(status), prog)


import utils as _utils
_utils.register_ui_callbacks(
    notify=_bridge_show_notification,
    status=_bridge_update_download_status,
    open_channel=_bridge_open_channel_window,
    open_playlist=_bridge_open_playlist_window,
)

from clipboard import start_monitoring


# ============================================================
# Кастомное всплывающее уведомление (toast)
# ============================================================

class ToastNotification(QWidget):
    """Всплывающее уведомление в правом нижнем углу экрана"""

    _active_toasts = []  # Стек активных уведомлений для смещения

    def __init__(self, title: str, message: str, duration: int = 2000):
        super().__init__(None, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedWidth(320)

        # Контент
        container = QWidget(self)
        container.setObjectName("toast_container")
        container.setStyleSheet(f"""
            #toast_container {{
                background-color: {COLORS["bg_secondary"]};
                border: 1px solid {COLORS["accent"]};
                border-radius: 10px;
            }}
        """)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(4)

        title_label = QLabel(title)
        title_label.setStyleSheet(f"color: {COLORS['accent']}; font-weight: bold; font-size: 13px; background: transparent;")
        layout.addWidget(title_label)

        msg_label = QLabel(message)
        msg_label.setWordWrap(True)
        msg_label.setStyleSheet(f"color: {COLORS['text_primary']}; font-size: 12px; background: transparent;")
        layout.addWidget(msg_label)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(container)

        self.adjustSize()

        # Позиция: правый нижний угол
        screen = QApplication.primaryScreen().availableGeometry()
        toast_index = len(ToastNotification._active_toasts)
        y_offset = toast_index * (self.height() + 8)
        self.move(screen.right() - self.width() - 16,
                  screen.bottom() - self.height() - 16 - y_offset)

        ToastNotification._active_toasts.append(self)

        # Автоскрытие
        QTimer.singleShot(duration, self._fade_out)

    def _fade_out(self):
        """Скрыть и удалить"""
        if self in ToastNotification._active_toasts:
            ToastNotification._active_toasts.remove(self)
        self.close()
        self.deleteLater()

    def mousePressEvent(self, event):
        """Закрыть по клику"""
        self._fade_out()


# ============================================================
# Окно истории загрузок
# ============================================================

class HistoryWindow(QWidget):
    """Окно истории загрузок"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("История загрузок")
        self.setGeometry(100, 100, 900, 600)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        # Заголовок
        header = QLabel("История загрузок")
        header_font = QFont()
        header_font.setPointSize(12)
        header_font.setBold(True)
        header.setFont(header_font)
        layout.addWidget(header)

        # Таблица
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Дата", "Название", "Формат", "Длительность"])

        # Настройка таблицы
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)

        # Растягиваем колонку "Название"
        table_header = self.table.horizontalHeader()
        table_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)  # Дата
        table_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)  # Название
        table_header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)  # Формат
        table_header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)  # Длительность

        layout.addWidget(self.table)

        # Кнопки
        btn_layout = QHBoxLayout()

        download_btn = QPushButton("Скачать выбранные")
        download_btn.clicked.connect(self._download_selected)
        btn_layout.addWidget(download_btn)

        btn_layout.addStretch()

        close_btn = QPushButton("Закрыть")
        close_btn.setProperty("secondary", True)
        close_btn.clicked.connect(self.close)
        btn_layout.addWidget(close_btn)

        layout.addLayout(btn_layout)

        # Словарь для хранения URL (row -> url)
        self.video_urls = {}

        # Загружаем данные
        self.load_history()

    def load_history(self):
        """Загружает историю загрузок в таблицу"""
        history = load_download_history()

        if not history:
            log_message("INFO История загрузок пуста")
            return

        self.table.setRowCount(len(history))
        self.video_urls.clear()

        for row, entry in enumerate(history):
            date = entry.get("date", "N/A")
            title = entry.get("title", "Без названия")
            format_type = entry.get("format", "N/A")
            duration_seconds = entry.get("duration", 0)
            url = entry.get("url", "")

            # Форматируем длительность
            duration_str = format_invidious_duration(duration_seconds) if duration_seconds else "N/A"

            self.table.setItem(row, 0, QTableWidgetItem(date))
            self.table.setItem(row, 1, QTableWidgetItem(title))
            self.table.setItem(row, 2, QTableWidgetItem(format_type))
            self.table.setItem(row, 3, QTableWidgetItem(duration_str))

            # Сохраняем URL для повторной загрузки
            self.video_urls[row] = url

        log_message(f"INFO Загружено {len(history)} записей в историю")

    def _download_selected(self):
        """Добавляет выбранные видео в очередь загрузки"""
        from queues import add_to_queue, process_queue
        from utils import is_download_active
        import threading

        selected_rows = list(set(item.row() for item in self.table.selectedItems()))
        if not selected_rows:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Ничего не выбрано", "Выберите хотя бы одно видео для загрузки")
            return

        added = 0
        for row in selected_rows:
            url = self.video_urls.get(row, "")
            if url and add_to_queue(url):
                added += 1
                log_message(f"INFO Добавлено в очередь из истории: {url}")

        if added > 0:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Успешно", f"Добавлено в очередь: {added}")

            # Запускаем обработку очереди
            if not is_download_active():
                threading.Thread(target=process_queue, daemon=True).start()

    def closeEvent(self, event):
        """Переопределяем закрытие окна - прячем вместо закрытия"""
        event.accept()
        self.hide()
        log_message("INFO Окно истории загрузок скрыто")


class YouTubeDownloaderApp(QApplication):
    """Главное приложение"""

    def __init__(self, argv):
        global _signals
        super().__init__(argv)

        # Имя приложения для Windows уведомлений
        self.setApplicationName("YouTube Downloader")
        self.setOrganizationName("YTD")

        # Tray-приложение: не завершать при закрытии последнего окна
        self.setQuitOnLastWindowClosed(False)

        # Загружаем настройки
        settings.update(load_settings())

        # Применяем стиль Fusion и тему (тёмную/светлую) из настроек
        self.setStyle("Fusion")
        _styles.set_dark_mode(settings.get("dark_theme", False))
        self.setStyleSheet(_styles.STYLESHEET_MINIMAL)

        # Создаём системный трей
        self.tray = TrayIcon(self)

        # Устанавливаем иконку приложения — наследуется всеми окнами
        self.setWindowIcon(self.tray._create_format_icon())

        # Инициализируем thread-safe сигналы для download.py
        _signals = _TraySignals()
        _signals.notify.connect(self._on_notify)
        _signals.status.connect(self._on_status_update)
        _signals.open_video_list.connect(self._on_open_video_list)

        # Главное окно (пока скрыто)
        self.main_window = None

        # Окно поиска
        self.search_window = None

        # Окно истории
        self.history_window = None

        # Окно отладки
        self.debug_window = None

        # Окна каналов/плейлистов (может быть несколько одновременно)
        self.video_list_windows = []

        # Окно очереди
        self.queue_window = None

        # Окно поиска ВКонтакте
        self.vk_window = None

        # Окно Одноклассников
        self.ok_window = None

        # Окно обновлений утилит
        self.updater_window = None

        log_message("INFO Приложение запущено (PyQt6)")
        QTimer.singleShot(150, self.show_search_window)
        QTimer.singleShot(500, self._check_queue_on_startup)
        QTimer.singleShot(800, self.restore_channel_windows)
        QTimer.singleShot(5000, self._check_updates_background)

        # Периодическое автосохранение окон/вкладок (резервное) — раз в минуту.
        self._autosave_timer = QTimer(self)
        self._autosave_timer.timeout.connect(self._save_all_state)
        self._autosave_timer.start(60 * 1000)

        # Сохранение по событиям с дебаунсом ~1.5 с: любое изменение (открытие/
        # закрытие окна, добавление/заполнение/закрытие вкладки) планирует запись,
        # чтобы после вылета не восстанавливалось устаревшее состояние.
        self._save_debounce = QTimer(self)
        self._save_debounce.setSingleShot(True)
        self._save_debounce.setInterval(1500)
        self._save_debounce.timeout.connect(self._save_all_state)

    def request_state_save(self):
        """Планирует сохранение состояния с дебаунсом. Безопасно вызывать часто
        и из разных мест — записи схлопываются в одну через ~1.5 с."""
        try:
            # start() перезапускает одноразовый таймер — это и есть дебаунс
            QMetaObject.invokeMethod(self._save_debounce, "start",
                                     Qt.ConnectionType.QueuedConnection)
        except Exception:
            try:
                self._save_debounce.start()
            except Exception:
                pass

    def _check_queue_on_startup(self):
        count = get_queue_count()
        if count == 0:
            return
        from queues import process_queue, get_queue_urls
        urls = get_queue_urls()
        preview = "\n".join(f"  • {u}" for u in urls[:5])
        if len(urls) > 5:
            preview += f"\n  … и ещё {len(urls) - 5}"
        reply = QMessageBox.question(
            None,
            "Незавершённая очередь",
            f"В очереди {count} ссылок{'а' if 2 <= count <= 4 else '' if count == 1 else ''}:\n\n{preview}\n\nНачать загрузку?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            import threading
            threading.Thread(target=process_queue, daemon=True).start()
            log_message(f"INFO Запущена очередь при старте ({count} шт.)")
        else:
            log_message("INFO Очередь при старте пропущена пользователем")

    def show_toast(self, title: str, message: str, duration: int = 2000):
        """Показывает кастомное всплывающее уведомление"""
        toast = ToastNotification(title, message, duration)
        toast.show()

    def _on_notify(self, title: str, message: str):
        """Слот: показывает уведомление (вызывается в главном потоке)"""
        # Только кастомный toast - закрывается через 2 секунды
        self.show_toast(title, message)

    def _ensure_main_window(self):
        """Создаёт единое главное окно SmartTubeDL (UI v2) при первом обращении."""
        if self.main_window is None:
            from main_window_qt import MainWindow
            self.main_window = MainWindow(self)
        return self.main_window

    def _sync_window_refs(self):
        """Синхронизирует ссылки на встроенные страницы для _save_all_state и download.py."""
        mw = self.main_window
        if mw is None:
            return
        if mw.youtube_page is not None:
            self.search_window = mw.youtube_page
        if mw.vk_page is not None:
            self.vk_window = mw.vk_page
        if mw.ok_page is not None:
            self.ok_window = mw.ok_page
        if mw.queue_page is not None:
            self.queue_window = mw.queue_page
        if mw.logs_page is not None:
            self.debug_window = mw.logs_page
        if mw.updater_page is not None:
            self.updater_window = mw.updater_page

    def _on_status_update(self, status: str, progress: int):
        """Слот: обновляет статус и прогресс (вызывается в главном потоке)"""
        self.tray.update_status(status, progress if progress >= 0 else None)
        if progress >= 0:
            self.tray.update_progress_icon(progress)

        if self.main_window is not None:
            self.main_window.update_footer_status(status, progress)

        # Обновляем прогресс в окне поиска (если оно открыто)
        self._sync_window_refs()
        if self.search_window is not None and self.search_window.isVisible():
            self.search_window.update_download_progress(status, progress if progress >= 0 else 0)

    def show_search_window(self):
        """Открывает раздел YouTube в главном окне"""
        mw = self._ensure_main_window()
        mw.switch_to(mw.IDX_YOUTUBE)
        self._sync_window_refs()

    def show_history_window(self):
        """Открывает раздел История в главном окне"""
        mw = self._ensure_main_window()
        mw.switch_to(mw.IDX_HISTORY)
        self._sync_window_refs()

    def show_queue_window(self):
        """Открывает раздел Очередь в главном окне"""
        mw = self._ensure_main_window()
        mw.switch_to(mw.IDX_QUEUE)
        self._sync_window_refs()

    def show_debug_window(self):
        """Открывает раздел Логи в главном окне"""
        mw = self._ensure_main_window()
        mw.switch_to(mw.IDX_LOGS)
        self._sync_window_refs()

    def _on_open_video_list(self, url: str, mode: str):
        """Слот: открывает канал (📺) или плейлист (📋) как вкладку в разделе YouTube."""
        mw = self._ensure_main_window()
        mw.open_youtube_video_list(url, mode=mode)
        self._sync_window_refs()
        self.request_state_save()

    def restore_channel_windows(self):
        """Мигрирует старые отдельные окна каналов/плейлистов во вкладки YouTube."""
        if not os.path.exists(_CHANNEL_WINDOWS_FILE):
            return
        try:
            with open(_CHANNEL_WINDOWS_FILE, "r", encoding="utf-8") as f:
                states = json.load(f)
        except Exception:
            return
        if not states:
            return
        mw = self._ensure_main_window()
        for state in states:
            url = state.get("url", "")
            mode = state.get("mode", "channel")
            if not url:
                continue
            try:
                mw.open_youtube_video_list(url, mode=mode, state=state)
            except Exception as e:
                log_message(f"WARNING restore channel tab: {e}")
        self._sync_window_refs()
        # Очищаем legacy-файл, теперь вкладки сохраняются вместе с YouTube в search_tabs.json
        try:
            _atomic_write_json(_CHANNEL_WINDOWS_FILE, [])
        except Exception:
            pass

    def _save_channel_windows(self):
        """Сохраняет состояние всех видимых отдельных окон каналов/плейлистов (если есть)."""
        if not self.video_list_windows:
            return
        states = []
        for w in self.video_list_windows:
            if w.isVisible():
                try:
                    states.append(w.get_state())
                except Exception as e:
                    log_message(f"WARNING save channel window state: {e}")
        try:
            _atomic_write_json(_CHANNEL_WINDOWS_FILE, states)
        except Exception as e:
            log_message(f"WARNING save channel windows file: {e}")

    def _save_all_state(self):
        """Сохраняет открытые вкладки всех разделов."""
        self._sync_window_refs()
        self._save_channel_windows()
        # Вкладки YouTube (поиск + каналы + плейлисты)
        sw = self.search_window
        if sw is not None:
            try:
                sw._save_tabs()
            except Exception as e:
                log_message(f"WARNING autosave search tabs: {e}")
        # Вкладки VK
        vw = self.vk_window
        if vw is not None:
            try:
                vw._save_vk_tabs()
            except Exception as e:
                log_message(f"WARNING autosave vk tabs: {e}")
        # Состояние OK
        ow = self.ok_window
        if ow is not None and hasattr(ow, "_save_state"):
            try:
                ow._save_state()
            except Exception as e:
                log_message(f"WARNING autosave ok state: {e}")
        # Вкладки Mail.ru
        if self.main_window is not None and self.main_window.mailru_page is not None:
            try:
                self.main_window.mailru_page._save_tabs()
            except Exception as e:
                log_message(f"WARNING autosave mailru tabs: {e}")

    def show_vk_search_window(self):
        """Открывает раздел ВКонтакте в главном окне"""
        mw = self._ensure_main_window()
        mw.switch_to(mw.IDX_VK)
        self._sync_window_refs()

    def show_ok_search_window(self):
        """Открывает раздел Одноклассники в главном окне"""
        mw = self._ensure_main_window()
        mw.switch_to(mw.IDX_OK)
        self._sync_window_refs()

    def show_updater_window(self):
        """Открывает раздел Настройки → Обновления утилит в главном окне"""
        mw = self._ensure_main_window()
        mw.switch_to(mw.IDX_SETTINGS)
        self._sync_window_refs()
        if mw.updater_page is not None:
            mw.updater_page.check_versions()

    def _check_updates_background(self):
        """Фоновая проверка обновлений yt-dlp раз в 7 дней."""
        try:
            from updater_qt import check_updates_if_due
            check_updates_if_due(
                lambda cur, latest: _bridge_show_notification(
                    "Доступно обновление yt-dlp",
                    f"Версия {cur} → {latest}. Откройте «Обновления утилит» в меню трея."
                )
            )
        except Exception as e:
            log_message(f"WARNING автопроверка обновлений: {e}")

    def show_direct_download_dialog(self):
        """Диалог для скачивания по произвольной ссылке"""
        url, ok = QInputDialog.getText(
            None,
            "Скачать по ссылке",
            "Вставьте ссылку на видео или аудио:",
        )
        if ok and url and url.strip():
            url = url.strip()
            log_message(f"INFO Прямая загрузка по ссылке: {url}")
            import threading
            from download import download_video
            threading.Thread(target=download_video, args=(url,), daemon=True).start()

    def show_bulk_download_dialog(self):
        """Открывает раздел «По ссылкам» в едином главном окне."""
        mw = self._ensure_main_window()
        mw.switch_to(mw.IDX_LINKS)
        self._sync_window_refs()

    def show_settings_dialog(self):
        """Диалог выбора папки для загрузки"""
        folder = QFileDialog.getExistingDirectory(
            None,
            "Выберите папку для загрузки",
            settings.get("download_folder", os.path.expanduser("~"))
        )
        if folder:
            settings["download_folder"] = folder
            save_settings(settings)
            log_message(f"INFO Папка загрузки: {folder}")
            QMessageBox.information(None, "Настройки", f"Папка сохранения:\n{folder}")


class TrayIcon(QSystemTrayIcon):
    """Иконка в системном трее"""

    def __init__(self, app: YouTubeDownloaderApp):
        # Сначала создаём иконку, потом вызываем super().__init__ с ней
        self._original_icon = None
        icon = self._create_icon()
        super().__init__(icon)

        self.app = app
        self.download_status = "Ожидание..."

        # Сохраняем оригинальную иконку для восстановления после загрузки
        self._original_icon = icon

        # Подсказка при наведении
        self.setToolTip("YouTube Downloader")

        # Создаём меню
        self.setContextMenu(self._create_menu())

        # Обработка клика по иконке
        self.activated.connect(self._on_activated)

        # Показываем иконку
        self.show()

        log_message("INFO Системный трей инициализирован")

    def _create_icon(self) -> QIcon:
        """Создаёт иконку по текущему формату (всегда программная для единого стиля)."""
        return self._create_format_icon()

    def _create_format_icon(self, fmt: str = None) -> QIcon:
        """Создаёт иконку с цветом по формату: красная=видео, синяя=музыка."""
        if fmt is None:
            fmt = settings.get("download_format", "mp4")
        color = "#e74c3c" if fmt == "mp4" else "#2980b9"
        label = "YT" if fmt == "mp4" else "♪"

        pixmap = QPixmap(64, 64)
        pixmap.fill(QColor(color))

        painter = QPainter(pixmap)
        painter.setPen(QColor("white"))
        font = painter.font()
        font.setPixelSize(28 if label == "YT" else 34)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, label)
        painter.end()

        return QIcon(pixmap)

    def _create_default_icon(self) -> QIcon:
        return self._create_format_icon()

    def _create_progress_icon(self, progress: int) -> QIcon:
        """Создаёт иконку с индикатором прогресса загрузки"""
        size = 64
        pixmap = QPixmap(size, size)
        pixmap.fill(QColor(0, 0, 0, 0))  # Прозрачный фон

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Фоновый круг (белый)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("white"))
        painter.drawEllipse(4, 4, size - 8, size - 8)

        # Дуга прогресса (чёрная)
        pen = QPen(QColor("black"), 8)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # Qt рисует углы в 1/16 градуса, начало сверху (90°)
        span = int(360 * progress / 100) * 16
        painter.drawArc(8, 8, size - 16, size - 16, 90 * 16, -span)

        painter.end()
        return QIcon(pixmap)

    def update_progress_icon(self, progress: int):
        """Обновляет иконку трея с прогрессом"""
        if progress >= 100:
            # Загрузка завершена - возвращаем обычную иконку
            if self._original_icon:
                self.setIcon(self._original_icon)
        else:
            self.setIcon(self._create_progress_icon(progress))

    def _create_menu(self) -> QMenu:
        """Создаёт контекстное меню трея"""
        menu = QMenu()

        # === Основные действия ===
        action_folder = menu.addAction("Выбрать папку")
        action_folder.triggered.connect(self.app.show_settings_dialog)

        action_history = menu.addAction("История загрузок")
        action_history.triggered.connect(self.app.show_history_window)

        action_queue = menu.addAction("Очередь загрузок")
        action_queue.triggered.connect(self.app.show_queue_window)

        action_search = menu.addAction("Поиск на YouTube")
        action_search.triggered.connect(self.app.show_search_window)

        action_vk = menu.addAction("Поиск ВКонтакте")
        action_vk.triggered.connect(self.app.show_vk_search_window)

        action_ok = menu.addAction("Одноклассники")
        action_ok.triggered.connect(self.app.show_ok_search_window)

        action_direct = menu.addAction("Скачать по ссылке")
        action_direct.triggered.connect(self.app.show_direct_download_dialog)

        action_bulk = menu.addAction("Скачать по ссылкам (пачкой)")
        action_bulk.triggered.connect(self.app.show_bulk_download_dialog)

        action_debug = menu.addAction("Режим отладки")
        action_debug.triggered.connect(self.app.show_debug_window)

        action_updates = menu.addAction("Обновления утилит")
        action_updates.triggered.connect(self.app.show_updater_window)

        menu.addSeparator()

        # === Формат ===
        menu.addAction("Формат:").setEnabled(False)

        action_mp3 = menu.addAction("  Музыка (MP3)")
        action_mp3.setCheckable(True)
        action_mp3.setChecked(settings.get("download_format") == "mp3")
        action_mp3.triggered.connect(lambda: self._set_format("mp3"))

        action_mp4 = menu.addAction("  Видео (MP4)")
        action_mp4.setCheckable(True)
        action_mp4.setChecked(settings.get("download_format") == "mp4")
        action_mp4.triggered.connect(lambda: self._set_format("mp4"))

        self.format_actions = [action_mp3, action_mp4]

        menu.addSeparator()

        # === Качество ===
        menu.addAction("Качество:").setEnabled(False)

        is_video = settings.get("download_format") == "mp4"

        action_1080 = menu.addAction("  1080p")
        action_1080.setCheckable(True)
        action_1080.setChecked(settings.get("video_quality") == "1080p")
        action_1080.setEnabled(is_video)
        action_1080.triggered.connect(lambda: self._set_quality("1080p"))

        action_720 = menu.addAction("  720p")
        action_720.setCheckable(True)
        action_720.setChecked(settings.get("video_quality") == "720p")
        action_720.setEnabled(is_video)
        action_720.triggered.connect(lambda: self._set_quality("720p"))

        action_480 = menu.addAction("  480p")
        action_480.setCheckable(True)
        action_480.setChecked(settings.get("video_quality") == "480p")
        action_480.setEnabled(is_video)
        action_480.triggered.connect(lambda: self._set_quality("480p"))

        self.quality_actions = [action_1080, action_720, action_480]

        menu.addSeparator()

        # === Настройки ===
        action_auto = menu.addAction("Автоперехват ссылок")
        action_auto.setCheckable(True)
        action_auto.setChecked(settings.get("auto_capture_enabled", True))
        action_auto.triggered.connect(self._toggle_auto_capture)
        self.action_auto = action_auto

        action_convert = menu.addAction("Конвертация")
        action_convert.setCheckable(True)
        action_convert.setChecked(settings.get("conversion_enabled", True))
        action_convert.triggered.connect(self._toggle_conversion)
        self.action_convert = action_convert

        action_desc = menu.addAction("Скачивать с описаниями")
        action_desc.setCheckable(True)
        action_desc.setChecked(settings.get("download_with_description", False))
        action_desc.triggered.connect(self._toggle_description)
        self.action_desc = action_desc

        menu.addSeparator()

        # === Статус ===
        queue_count = get_queue_count()
        status_text = f"Статус: {self.download_status}"
        if queue_count > 0:
            status_text += f" (в очереди: {queue_count})"

        action_status = menu.addAction(status_text)
        action_status.setEnabled(False)
        self.action_status = action_status

        menu.addSeparator()

        # === Перезапуск и Выход ===
        action_restart = menu.addAction("Перезапустить")
        action_restart.triggered.connect(self._restart_app)

        action_exit = menu.addAction("Выход")
        action_exit.triggered.connect(self._exit_app)

        return menu

    def _set_format(self, fmt: str):
        settings["download_format"] = fmt
        save_settings(settings)
        log_message(f"INFO Формат: {fmt.upper()}")
        for action in self.format_actions:
            action.setChecked(fmt in action.text().lower())
        is_video = fmt == "mp4"
        for action in self.quality_actions:
            action.setEnabled(is_video)
        self._original_icon = self._create_format_icon(fmt)
        self.setIcon(self._original_icon)
        self.update_status(self.download_status)
        if self.app.main_window is not None:
            self.app.main_window.sync_footer_settings()

    def _set_quality(self, quality: str):
        settings["video_quality"] = quality
        save_settings(settings)
        log_message(f"INFO Качество: {quality}")
        for action in self.quality_actions:
            action.setChecked(quality in action.text())
        if self.app.main_window is not None:
            self.app.main_window.sync_footer_settings()

    def _toggle_auto_capture(self):
        settings["auto_capture_enabled"] = not settings.get("auto_capture_enabled", True)
        save_settings(settings)
        self.action_auto.setChecked(settings["auto_capture_enabled"])
        log_message(f"INFO Автоперехват: {'вкл' if settings['auto_capture_enabled'] else 'выкл'}")

    def _toggle_conversion(self):
        settings["conversion_enabled"] = not settings.get("conversion_enabled", True)
        save_settings(settings)
        self.action_convert.setChecked(settings["conversion_enabled"])
        log_message(f"INFO Конвертация: {'вкл' if settings['conversion_enabled'] else 'выкл'}")

    def _toggle_description(self):
        settings["download_with_description"] = not settings.get("download_with_description", False)
        save_settings(settings)
        self.action_desc.setChecked(settings["download_with_description"])
        log_message(f"INFO Скачивать с описаниями: {'вкл' if settings['download_with_description'] else 'выкл'}")

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.app.show_search_window()

    def _prepare_shutdown(self):
        """Сохраняет состояние и корректно закрывает фоновые потоки и браузеры."""
        if settings.get("save_settings_on_exit", False):
            save_settings(settings)
            log_message("INFO Настройки сохранены при выходе")

        # Сохраняем состояние открытых окон и вкладок
        self.app._save_all_state()

        for w in self.app.video_list_windows:
            w.close()
        self.app.video_list_windows.clear()

        # Останавливаем QThread-ы окна поиска (они C++ потоки, не daemon)
        sw = self.app.search_window
        if sw is not None:
            for attr in ("search_worker", "thumbnail_loader"):
                t = getattr(sw, attr, None)
                if t is not None and t.isRunning():
                    t.quit()
                    t.wait(500)
                    if t.isRunning():
                        t.terminate()

        # Закрываем браузеры VK и OK (Selenium), если они были открыты
        for win in (self.app.vk_window, self.app.ok_window):
            if win is not None and hasattr(win, "quit_browser"):
                try:
                    win.quit_browser()
                except Exception:
                    pass

    def _restart_app(self):
        """Безопасный перезапуск: сохраняет все вкладки, освобождает мьютекс и стартует новый процесс."""
        import subprocess
        global _APP_MUTEX_HANDLE
        self._prepare_shutdown()
        log_message("INFO Перезапуск приложения...")
        self.hide()

        # Освобождаем single-instance мьютекс ДО запуска нового процесса
        if _APP_MUTEX_HANDLE is not None and sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.kernel32.CloseHandle(_APP_MUTEX_HANDLE)
                _APP_MUTEX_HANDLE = None
            except Exception:
                pass

        py = sys.executable
        if os.path.basename(py).lower() == "python.exe":
            cand = os.path.join(os.path.dirname(py), "pythonw.exe")
            if os.path.exists(cand):
                py = cand
        flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        try:
            subprocess.Popen(
                [py, os.path.abspath(__file__)],
                cwd=os.path.dirname(os.path.abspath(__file__)),
                creationflags=flags,
                close_fds=True,
            )
        except Exception as e:
            log_message(f"ERROR Ошибка перезапуска: {e}")
        QApplication.quit()
        os._exit(0)

    def _exit_app(self):
        self._prepare_shutdown()
        log_message("INFO Приложение завершено")
        self.hide()
        QApplication.quit()
        os._exit(0)

    def update_status(self, status: str, progress: int = None):
        """Обновляет статус в меню и подсказку"""
        self.download_status = status
        # Восстанавливаем иконку когда загрузок нет
        if status in ("Ожидание...", "Готово!") and self._original_icon:
            self.setIcon(self._original_icon)
        queue_count = get_queue_count()

        status_text = f"Статус: {status}"
        if queue_count > 0:
            status_text += f" (в очереди: {queue_count})"

        self.action_status.setText(status_text)

        fmt = settings.get("download_format", "mp4").upper()
        import utils
        speed = utils.download_speed
        if status == "Загрузка..." and speed and speed != "0 KB/s":
            tooltip = f"YouTube Downloader [{fmt}] — {status} ({speed})"
        else:
            tooltip = f"YouTube Downloader [{fmt}] — {status}"
        self.setToolTip(tooltip)


_APP_MUTEX_HANDLE = None


def _acquire_single_instance() -> bool:
    """True — мы единственный экземпляр app_qt.py, False — программа уже запущена."""
    global _APP_MUTEX_HANDLE
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        k = ctypes.windll.kernel32
        _APP_MUTEX_HANDLE = k.CreateMutexW(None, False, "YTD_AppQt_singleton")
        ERROR_ALREADY_EXISTS = 183
        if k.GetLastError() == ERROR_ALREADY_EXISTS:
            k.CloseHandle(_APP_MUTEX_HANDLE)
            _APP_MUTEX_HANDLE = None
            return False
        return True
    except Exception:
        return True


def main():
    if not _acquire_single_instance():
        log_message("INFO Программа уже запущена — повторный экземпляр завершён")
        sys.exit(0)

    # Инициализация
    ensure_queue_file_exists()
    start_monitoring()

    # Настройка для QtWebEngineWidgets (встроенный видеоплеер)
    # Должна быть установлена ДО создания QApplication
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

    # Создаём приложение
    app = YouTubeDownloaderApp(sys.argv)

    # Запускаем главный цикл
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
