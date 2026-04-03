import threading
import re

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QLabel, QPushButton, QHeaderView, QAbstractItemView, QMessageBox
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont

import utils as _utils
from queues import get_queue_urls, remove_from_queue
from logger import log_message


def _display_title(url: str) -> str:
    """Возвращает название из кэша или укороченный URL."""
    title = _utils.queue_titles.get(url)
    if title:
        return title
    if url.startswith("vk:"):
        return url[3:]  # убираем префикс
    m = re.search(r'[?&]v=([^&]{6,})', url)
    if m:
        return f"[{m.group(1)}]  {url}"
    return url


class QueueWindow(QWidget):
    """Окно очереди загрузок с поддержкой паузы"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Очередь загрузок")
        self.setGeometry(100, 100, 700, 450)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        header = QLabel("Очередь загрузок")
        header_font = QFont()
        header_font.setPointSize(12)
        header_font.setBold(True)
        header.setFont(header_font)
        layout.addWidget(header)

        self.table = QTableWidget()
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["Статус", "Название"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)

        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

        layout.addWidget(self.table)

        btn_layout = QHBoxLayout()

        self.pause_btn = QPushButton("Пауза")
        self.pause_btn.clicked.connect(self._toggle_pause)
        btn_layout.addWidget(self.pause_btn)

        self.delete_btn = QPushButton("Удалить выбранное")
        self.delete_btn.setProperty("secondary", True)
        self.delete_btn.clicked.connect(self._delete_selected)
        btn_layout.addWidget(self.delete_btn)

        btn_layout.addStretch()

        close_btn = QPushButton("Закрыть")
        close_btn.setProperty("secondary", True)
        close_btn.clicked.connect(self.close)
        btn_layout.addWidget(close_btn)

        layout.addLayout(btn_layout)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(1000)
        self._refresh()

    def _refresh(self):
        is_paused = _utils.is_paused
        current_url = _utils.current_download_url
        is_dl = bool(current_url)
        queue_urls = [u for u in get_queue_urls() if u != current_url]

        # VK
        current_vk = _utils.current_vk_key
        vk_queue = list(_utils.vk_queue)

        rows = []

        # YouTube
        if is_dl:
            rows.append(("↓ Загружается", current_url))
            for url in queue_urls:
                rows.append(("⏳ Ожидание", url))
        elif is_paused and queue_urls:
            rows.append(("⏸ На паузе", queue_urls[0]))
            for url in queue_urls[1:]:
                rows.append(("⏳ Ожидание", url))
        else:
            for url in queue_urls:
                rows.append(("⏳ Ожидание", url))

        # VK (показываем отдельной группой)
        if current_vk:
            rows.append(("↓ Загружается", current_vk))
        for item in vk_queue:
            rows.append(("⏳ Ожидание", item["key"]))

        self.table.setRowCount(len(rows))
        for i, (status, url) in enumerate(rows):
            self.table.setItem(i, 0, QTableWidgetItem(status))
            title_item = QTableWidgetItem(_display_title(url))
            title_item.setData(Qt.ItemDataRole.UserRole, url)
            title_item.setToolTip(url)
            self.table.setItem(i, 1, title_item)

        if is_paused:
            self.pause_btn.setText("Возобновить")
            self.pause_btn.setEnabled(True)
        elif is_dl:
            self.pause_btn.setText("Пауза")
            self.pause_btn.setEnabled(True)
        else:
            self.pause_btn.setText("Пауза")
            self.pause_btn.setEnabled(False)

    def _selected_urls(self) -> list[str]:
        """Возвращает URL всех выделенных строк (без дублей)."""
        seen = set()
        urls = []
        for item in self.table.selectedItems():
            if item.column() != 1:
                continue
            url = item.data(Qt.ItemDataRole.UserRole)
            if url and url not in seen:
                seen.add(url)
                urls.append(url)
        return urls

    def _toggle_pause(self):
        if _utils.is_paused:
            _utils.is_paused = False
            from queues import process_queue
            threading.Thread(target=process_queue, daemon=True).start()
            log_message("INFO Загрузка возобновлена")
        else:
            _utils.stop_requested = True
            log_message("INFO Запрошена пауза")

    def _delete_selected(self):
        urls = self._selected_urls()
        if not urls:
            return

        vk_keys = [u for u in urls if u.startswith("vk:")]
        yt_urls  = [u for u in urls if not u.startswith("vk:")]

        # VK-элементы: просто убираем из списка ожидания
        if vk_keys:
            cur = _utils.current_vk_key
            if cur in vk_keys:
                QMessageBox.information(
                    self, "Нельзя удалить",
                    "ВК-трек сейчас скачивается — дождитесь завершения."
                )
                vk_keys = [k for k in vk_keys if k != cur]
            _utils.vk_queue = [
                item for item in _utils.vk_queue if item["key"] not in vk_keys
            ]
            for k in vk_keys:
                _utils.queue_titles.pop(k, None)
            log_message(f"INFO Удалено из VK-очереди: {vk_keys}")

        if not yt_urls:
            self._refresh()
            return

        # Не даём удалить текущую YouTube-загрузку
        if _utils.current_download_url in yt_urls:
            QMessageBox.information(
                self, "Нельзя удалить",
                "Сначала поставьте загрузку на паузу, затем удалите."
            )
            return

        queue = get_queue_urls()
        paused_url = queue[0] if (_utils.is_paused and queue) else None
        deleted_paused = False

        for url in yt_urls:
            if url == paused_url:
                _utils.is_paused = False
                deleted_paused = True
            remove_from_queue(url)
            log_message(f"INFO Удалено из очереди: {url}")

        if deleted_paused:
            from tray import update_download_status
            remaining = get_queue_urls()
            if remaining:
                from queues import process_queue
                threading.Thread(target=process_queue, daemon=True).start()
            else:
                update_download_status("Ожидание...", -1)

        self._refresh()

    def closeEvent(self, event):
        event.accept()
        self.hide()
