import threading

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QLabel, QPushButton, QHeaderView, QAbstractItemView, QMessageBox
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont

import utils as _utils
from queues import get_queue_urls, remove_from_queue
from logger import log_message


class QueueWindow(QWidget):
    """Окно очереди загрузок с поддержкой паузы"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Очередь загрузок")
        self.setGeometry(100, 100, 700, 450)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        # Заголовок
        header = QLabel("Очередь загрузок")
        header_font = QFont()
        header_font.setPointSize(12)
        header_font.setBold(True)
        header.setFont(header_font)
        layout.addWidget(header)

        # Таблица
        self.table = QTableWidget()
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["Статус", "URL"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)

        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

        layout.addWidget(self.table)

        # Кнопки
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

        # Автообновление раз в секунду
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(1000)
        self._refresh()

    def _refresh(self):
        is_paused = _utils.is_paused
        current_url = _utils.current_download_url
        is_dl = bool(current_url)  # current_download_url надёжнее config.is_downloading
        queue_urls = [u for u in get_queue_urls() if u != current_url]  # не дублировать текущий

        rows = []
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

        self.table.setRowCount(len(rows))
        for i, (status, url) in enumerate(rows):
            self.table.setItem(i, 0, QTableWidgetItem(status))
            self.table.setItem(i, 1, QTableWidgetItem(url))

        # Обновляем кнопку паузы
        if is_paused:
            self.pause_btn.setText("Возобновить")
            self.pause_btn.setEnabled(True)
        elif is_dl:
            self.pause_btn.setText("Пауза")
            self.pause_btn.setEnabled(True)
        else:
            self.pause_btn.setText("Пауза")
            self.pause_btn.setEnabled(False)

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
        row = self.table.currentRow()
        if row < 0:
            return

        status_item = self.table.item(row, 0)
        url_item = self.table.item(row, 1)
        if not url_item:
            return

        url = url_item.text()

        if url == _utils.current_download_url:
            QMessageBox.information(
                self, "Нельзя удалить",
                "Сначала поставьте загрузку на паузу, затем удалите."
            )
            return

        if _utils.is_paused and get_queue_urls() and get_queue_urls()[0] == url:
            _utils.is_paused = False
            from tray import update_download_status
            update_download_status("Ожидание...", -1)

        remove_from_queue(url)
        log_message(f"INFO Удалено из очереди: {url}")
        self._refresh()

    def closeEvent(self, event):
        event.accept()
        self.hide()
