"""
Окно выбора и загрузки видео с канала или плейлиста (PyQt6).
Используется вместо старого tkinter-варианта в download.py.
"""

import threading
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed

import yt_dlp

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QLineEdit, QProgressBar, QApplication, QMenu,
)
from PyQt6.QtCore import Qt, pyqtSignal

from config import format_invidious_duration
from logger import log_message
from queues import add_to_queue, process_queue


YDL_OPTS_BASE = {
    'quiet': True,
    'color': 'never',
    'ignoreerrors': True,
    'js_runtimes': {'node': {}},
    'remote_components': {'ejs': 'github'},
}


class _DurationItem(QTableWidgetItem):
    """QTableWidgetItem с корректной сортировкой по длительности."""

    def __init__(self, text: str):
        super().__init__(text)
        self._seconds = self._parse(text)

    @staticmethod
    def _parse(s: str) -> int:
        try:
            parts = s.split(':')
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
        except Exception:
            pass
        return 0

    def __lt__(self, other):
        if isinstance(other, _DurationItem):
            return self._seconds < other._seconds
        return super().__lt__(other)


class VideoListWindow(QWidget):
    """Окно выбора видео с YouTube-канала или плейлиста."""

    # Сигналы для thread-safe обновления UI из фоновых потоков
    sig_set_subtitle = pyqtSignal(str, str)           # window_title, subtitle_text
    sig_add_row = pyqtSignal(str, str, str, str)      # video_id, title, duration, date
    sig_update_row = pyqtSignal(str, str, str, str)   # video_id, title, duration, date
    sig_progress = pyqtSignal(int, str)               # percent, label_text
    sig_loading_done = pyqtSignal(str)                # итоговый статус
    sig_error = pyqtSignal(str)                       # сообщение об ошибке
    sig_show_description = pyqtSignal(str, str, str)  # title, description, url

    def __init__(self, url: str, mode: str = "channel"):
        """
        :param url: URL канала или плейлиста
        :param mode: "channel" или "playlist"
        """
        super().__init__()
        self.url = url
        self.mode = mode
        self.cancelled = False

        # Хранилище данных (key = video_id)
        self._video_urls: dict[str, str] = {}          # video_id -> watch URL
        self._original_data: dict[str, tuple] = {}     # video_id -> (title, duration, date)
        self._valid_entries: list = []                  # raw yt-dlp entries
        self._descriptions: dict[str, str] = {}        # video_id -> description (кэш)
        self.description_windows: list = []

        kind_ru = "канала" if mode == "channel" else "плейлиста"
        self.setWindowTitle(f"Загрузка видео с {kind_ru}")
        self.setMinimumSize(900, 600)
        self.resize(1000, 700)

        screen = QApplication.primaryScreen().geometry()
        self.move((screen.width() - self.width()) // 2,
                  (screen.height() - self.height()) // 2)

        self._setup_ui()
        self._connect_signals()

        threading.Thread(target=self._load_info, daemon=True).start()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _setup_ui(self):
        from styles import STYLESHEET_MINIMAL as STYLESHEET
        self.setStyleSheet(STYLESHEET)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # --- Верхняя панель: подзаголовок + прогресс + кнопка стоп ---
        self.load_panel = QWidget()
        self.load_panel.setObjectName("searchBar")
        top = QHBoxLayout(self.load_panel)
        top.setContentsMargins(12, 8, 12, 8)
        top.setSpacing(10)

        self.subtitle_label = QLabel("Загрузка...")
        self.subtitle_label.setStyleSheet("font-size: 13px; font-weight: 600;")
        top.addWidget(self.subtitle_label, 1)

        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("thinProgress")
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setTextVisible(False)
        top.addWidget(self.progress_bar, 2)

        self.stop_btn = QPushButton("Остановить загрузку")
        self.stop_btn.setProperty("secondary", True)
        self.stop_btn.clicked.connect(self._stop_loading)
        top.addWidget(self.stop_btn)

        root.addWidget(self.load_panel)

        # --- Таблица ---
        self.table = QTableWidget(0, 3)
        self.table.setObjectName("resultsTable")
        self.table.setHorizontalHeaderLabels(["Название", "Длительность", "Дата"])

        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)

        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.doubleClicked.connect(self._on_double_click)

        root.addWidget(self.table, 1)

        # --- Нижняя панель: поиск + статус + кнопки ---
        bottom = QWidget()
        bottom.setObjectName("bottomBar")
        bot = QHBoxLayout(bottom)
        bot.setContentsMargins(12, 8, 12, 8)
        bot.setSpacing(8)

        self.search_input = QLineEdit()
        self.search_input.setObjectName("searchInput")
        self.search_input.setPlaceholderText("Поиск по названию...")
        self.search_input.textChanged.connect(self._filter_table)
        bot.addWidget(self.search_input, 2)

        self.status_label = QLabel("Загрузка...")
        self.status_label.setStyleSheet("color: #888; font-size: 12px;")
        bot.addWidget(self.status_label, 1)

        sel_all_btn = QPushButton("Выделить все")
        sel_all_btn.setProperty("secondary", True)
        sel_all_btn.clicked.connect(self.table.selectAll)
        bot.addWidget(sel_all_btn)

        desel_btn = QPushButton("Снять выделение")
        desel_btn.setProperty("secondary", True)
        desel_btn.clicked.connect(self.table.clearSelection)
        bot.addWidget(desel_btn)

        dl_btn = QPushButton("Загрузить выбранные")
        dl_btn.clicked.connect(self._add_selected_to_queue)
        bot.addWidget(dl_btn)

        root.addWidget(bottom)

    def _connect_signals(self):
        self.sig_set_subtitle.connect(self._on_set_subtitle)
        self.sig_add_row.connect(self._on_add_row)
        self.sig_update_row.connect(self._on_update_row)
        self.sig_progress.connect(self._on_progress)
        self.sig_loading_done.connect(self._on_loading_done)
        self.sig_error.connect(self._on_error)
        self.sig_show_description.connect(self._open_description_window)

    # ------------------------------------------------------------------
    # Слоты (вызываются в главном потоке через сигналы)
    # ------------------------------------------------------------------

    def _on_set_subtitle(self, title: str, subtitle: str):
        self.setWindowTitle(title)
        self.subtitle_label.setText(subtitle)

    def _on_add_row(self, video_id: str, title: str, duration: str, date: str):
        self.table.setSortingEnabled(False)
        row = self.table.rowCount()
        self.table.insertRow(row)

        title_item = QTableWidgetItem(title)
        title_item.setData(Qt.ItemDataRole.UserRole, video_id)
        self.table.setItem(row, 0, title_item)
        self.table.setItem(row, 1, _DurationItem(duration))
        self.table.setItem(row, 2, QTableWidgetItem(date))

        self.table.setSortingEnabled(True)
        self._original_data[video_id] = (title, duration, date)
        self.status_label.setText(f"Видео: {self._count_visible()}")

    def _on_update_row(self, video_id: str, title: str, duration: str, date: str):
        row = self._find_row(video_id)
        if row < 0:
            return
        self.table.setSortingEnabled(False)

        title_item = QTableWidgetItem(title)
        title_item.setData(Qt.ItemDataRole.UserRole, video_id)
        self.table.setItem(row, 0, title_item)
        self.table.setItem(row, 1, _DurationItem(duration))
        self.table.setItem(row, 2, QTableWidgetItem(date))

        self.table.setSortingEnabled(True)
        self._original_data[video_id] = (title, duration, date)

    def _on_progress(self, percent: int, text: str):
        self.progress_bar.setValue(percent)
        self.subtitle_label.setText(text)

    def _on_loading_done(self, status: str):
        self.load_panel.setVisible(False)
        self.status_label.setText(status)
        log_message(f"INFO Список загружен: {status}")

    def _on_error(self, msg: str):
        log_message(f"ERROR {msg}")
        self.subtitle_label.setText(f"Ошибка: {msg}")
        self.stop_btn.setEnabled(False)

    # ------------------------------------------------------------------
    # Фоновые потоки
    # ------------------------------------------------------------------

    def _load_info(self):
        """Загружает список видео с канала/плейлиста."""
        opts = {**YDL_OPTS_BASE, 'extract_flat': True, 'skip_download': True}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(self.url, download=False)
                if not info:
                    self.sig_error.emit("Не удалось получить информацию. Проверьте URL.")
                    return

                total = info.get('playlist_count', 0) or len(info.get('entries') or [])
                source_title = info.get('title', self.url)
                kind = "Канал" if self.mode == "channel" else "Плейлист"
                self.sig_set_subtitle.emit(
                    f"{kind}: {source_title}",
                    f"{kind}: {source_title}  •  Всего: {total}"
                )

                loaded = 0
                for entry in (info.get('entries') or []):
                    if self.cancelled or entry is None:
                        break
                    try:
                        if (entry.get('title') == '[Private video]'
                                or entry.get('is_premiere')
                                or entry.get('live_status') == 'is_upcoming'):
                            continue

                        video_id = entry.get('id')
                        if not video_id:
                            continue

                        self._valid_entries.append(entry)
                        self._video_urls[video_id] = (
                            f"https://www.youtube.com/watch?v={video_id}"
                        )

                        dur_raw = entry.get('duration')
                        duration = format_invidious_duration(dur_raw) if dur_raw else "Загрузка..."
                        title = entry.get('title', 'Без названия')
                        raw_date = entry.get('upload_date') or ''
                        date = (f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
                                if len(raw_date) == 8 else '-')

                        self.sig_add_row.emit(video_id, title, duration, date)
                        loaded += 1

                        if total > 0:
                            pct = int(loaded / total * 100)
                            self.sig_progress.emit(pct, f"Загрузка: {pct}%  ({loaded} из {total})")
                        else:
                            self.sig_progress.emit(0, f"Загружено: {loaded}")

                    except Exception as e:
                        log_message(f"ERROR Ошибка при обработке видео: {e}")

                skipped = total - loaded
                note = f"  (пропущено {skipped}: приватные/премьеры)" if skipped > 0 else ""
                self.sig_loading_done.emit(f"Видео: {loaded}{note}")

                # Загрузка метаданных для строк без длительности
                threading.Thread(target=self._load_metadata, daemon=True).start()

        except Exception as e:
            log_message(f"ERROR Ошибка загрузки {self.mode}: {e}")
            self.sig_error.emit(str(e))

    def _load_metadata(self):
        """Дозагружает длительность/дату для видео с 'Загрузка...'."""
        to_fetch = [
            entry.get('id') for entry in self._valid_entries
            if self._original_data.get(entry.get('id'), ('', 'Загрузка...', ''))[1] == "Загрузка..."
        ]
        if not to_fetch:
            return

        opts = {**YDL_OPTS_BASE, 'extract_flat': False, 'skip_download': True}

        def fetch_one(video_id: str):
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(
                        f"https://www.youtube.com/watch?v={video_id}", download=False
                    )
                    if info:
                        raw_date = info.get('upload_date') or ''
                        date = (f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
                                if len(raw_date) == 8 else '-')
                        return (
                            video_id,
                            info.get('title', 'Без названия'),
                            format_invidious_duration(info.get('duration', 0)),
                            date,
                        )
            except Exception as e:
                log_message(f"DEBUG Ошибка метаданных {video_id}: {e}")
            return None

        with ThreadPoolExecutor(max_workers=5) as ex:
            futures = {ex.submit(fetch_one, vid): vid for vid in to_fetch}
            for fut in as_completed(futures):
                if self.cancelled:
                    break
                result = fut.result()
                if result:
                    video_id, title, duration, date = result
                    self.sig_update_row.emit(video_id, title, duration, date)

    # ------------------------------------------------------------------
    # Действия
    # ------------------------------------------------------------------

    def _stop_loading(self):
        self.cancelled = True
        self.stop_btn.setEnabled(False)
        self.subtitle_label.setText("Загрузка остановлена")
        log_message("INFO Загрузка списка остановлена пользователем")

    def _filter_table(self, text: str):
        text = text.lower()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            hidden = text != "" and item is not None and text not in item.text().lower()
            self.table.setRowHidden(row, hidden)
        self.status_label.setText(f"Видео: {self._count_visible()}")

    def _count_visible(self) -> int:
        return sum(
            1 for r in range(self.table.rowCount())
            if not self.table.isRowHidden(r)
        )

    def _find_row(self, video_id: str) -> int:
        """Найти строку таблицы по video_id (хранится в UserRole)."""
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == video_id:
                return row
        return -1

    def _video_id_at_row(self, row: int) -> str | None:
        item = self.table.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _selected_video_ids(self) -> list[str]:
        rows = {idx.row() for idx in self.table.selectedIndexes()}
        ids = []
        for row in rows:
            vid = self._video_id_at_row(row)
            if vid:
                ids.append(vid)
        return ids

    def _add_selected_to_queue(self):
        video_ids = self._selected_video_ids()
        if not video_ids:
            self.status_label.setText("Ничего не выбрано")
            return
        added = 0
        for vid in video_ids:
            url = self._video_urls.get(vid)
            title = self._original_data.get(vid, (None,))[0]
            if url and add_to_queue(url, title or None):
                added += 1
        self.status_label.setText(f"Добавлено в очередь: {added}")
        log_message(f"INFO Добавлено в очередь: {added} видео")
        from config import is_downloading
        if not is_downloading:
            threading.Thread(target=process_queue, daemon=True).start()

    def _show_context_menu(self, pos):
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        video_id = self._video_id_at_row(row)
        if not video_id:
            return

        menu = QMenu(self)
        menu.addAction("Загрузить выбранные").triggered.connect(self._add_selected_to_queue)
        menu.addAction("Копировать URL").triggered.connect(
            lambda: self._copy_url(video_id))
        menu.addAction("Открыть в браузере").triggered.connect(
            lambda: self._open_in_browser(video_id))
        menu.addSeparator()
        menu.addAction("Посмотреть описание").triggered.connect(
            lambda: self._show_description(row))
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _copy_url(self, video_id: str):
        url = self._video_urls.get(video_id, "")
        if url:
            QApplication.clipboard().setText(url)
            try:
                from clipboard_utils import update_last_copy_time
                update_last_copy_time()
            except Exception:
                pass
            self.status_label.setText("URL скопирован")
            log_message(f"INFO Ссылка скопирована: {url}")

    def _open_in_browser(self, video_id: str):
        url = self._video_urls.get(video_id, "")
        if url:
            webbrowser.open(url)

    def _show_description(self, row: int):
        """Показывает описание видео (загружает по требованию)."""
        video_id = self._video_id_at_row(row)
        if not video_id:
            return
        url = self._video_urls.get(video_id, "")
        title_item = self.table.item(row, 0)
        title = title_item.text() if title_item else ""

        # Если описание уже в кэше — сразу показываем
        if video_id in self._descriptions:
            self._open_description_window(title, self._descriptions[video_id], url)
            return

        self.status_label.setText("Загрузка описания...")

        def fetch():
            try:
                from fetch import fetch_description_with_bs
                desc = fetch_description_with_bs(url) if url else ""
            except Exception as e:
                log_message(f"ERROR Ошибка загрузки описания: {e}")
                desc = ""
            self._descriptions[video_id] = desc or "Описание отсутствует"
            self.sig_show_description.emit(title, self._descriptions[video_id], url)

        threading.Thread(target=fetch, daemon=True).start()

    def _open_description_window(self, title: str, description: str, url: str):
        self.status_label.setText(f"Видео: {self._count_visible()}")
        from description import DescriptionWindow
        win = DescriptionWindow(title, description, url)
        win.show()
        win.raise_()
        win.activateWindow()
        self.description_windows.append(win)
        win.destroyed.connect(
            lambda: self.description_windows.remove(win)
            if win in self.description_windows else None
        )

    def _on_double_click(self, index):
        video_id = self._video_id_at_row(index.row())
        if not video_id:
            return
        url = self._video_urls.get(video_id)
        if not url:
            return
        add_to_queue(url)
        self.status_label.setText(f"Добавлено в очередь: {url[:60]}...")
        log_message(f"INFO Видео добавлено в очередь: {url}")
        from config import is_downloading
        if not is_downloading:
            threading.Thread(target=process_queue, daemon=True).start()

    def closeEvent(self, event):
        self.cancelled = True
        kind = "канала" if self.mode == "channel" else "плейлиста"
        log_message(f"INFO Окно {kind} закрыто")
        event.accept()
