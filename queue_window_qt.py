import os
import threading
import re

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QLabel, QPushButton, QHeaderView, QAbstractItemView, QMessageBox, QFileDialog
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QShortcut, QKeySequence

import utils as _utils
from config import settings
from queues import (
    get_queue_urls, remove_from_queue, add_to_queue,
    clear_queue_file, move_in_queue, process_queue
)
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

        del_sc = QShortcut(QKeySequence(Qt.Key.Key_Delete), self.table)
        del_sc.setContext(Qt.ShortcutContext.WidgetShortcut)
        del_sc.activated.connect(self._delete_selected)

        btn_layout = QHBoxLayout()

        self.pause_btn = QPushButton("Пауза")
        self.pause_btn.clicked.connect(self._toggle_pause)
        btn_layout.addWidget(self.pause_btn)

        self.delete_btn = QPushButton("Удалить выбранное")
        self.delete_btn.setProperty("secondary", True)
        self.delete_btn.clicked.connect(self._delete_selected)
        btn_layout.addWidget(self.delete_btn)

        self.up_btn = QPushButton("▲")
        self.up_btn.setObjectName("historyBtn")
        self.up_btn.setProperty("secondary", True)
        self.up_btn.setToolTip("Поднять выше в очереди")
        self.up_btn.clicked.connect(lambda: self._move_selected(-1))
        btn_layout.addWidget(self.up_btn)

        self.down_btn = QPushButton("▼")
        self.down_btn.setObjectName("historyBtn")
        self.down_btn.setProperty("secondary", True)
        self.down_btn.setToolTip("Опустить ниже в очереди")
        self.down_btn.clicked.connect(lambda: self._move_selected(1))
        btn_layout.addWidget(self.down_btn)

        btn_layout.addSpacing(8)

        import_btn = QPushButton("Импорт (.txt)")
        import_btn.setProperty("secondary", True)
        import_btn.setToolTip("Добавить ссылки из текстового файла в очередь")
        import_btn.clicked.connect(self._import_queue)
        btn_layout.addWidget(import_btn)

        export_btn = QPushButton("Экспорт (.txt)")
        export_btn.setProperty("secondary", True)
        export_btn.setToolTip("Сохранить ссылки очереди в текстовый файл")
        export_btn.clicked.connect(self._export_queue)
        btn_layout.addWidget(export_btn)

        clear_btn = QPushButton("Очистить")
        clear_btn.setProperty("secondary", True)
        clear_btn.setToolTip("Очистить ожидающие загрузки")
        clear_btn.clicked.connect(self._clear_queue)
        btn_layout.addWidget(clear_btn)

        btn_layout.addStretch()

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

        selected_before = set(self._selected_urls())

        self.table.setRowCount(len(rows))
        for i, (status, url) in enumerate(rows):
            self.table.setItem(i, 0, QTableWidgetItem(status))
            title_item = QTableWidgetItem(_display_title(url))
            title_item.setData(Qt.ItemDataRole.UserRole, url)
            title_item.setToolTip(url)
            self.table.setItem(i, 1, title_item)
            if url in selected_before:
                self.table.selectRow(i)

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

        # Если среди выбранных есть текущая активная YouTube-загрузка —
        # отменяем её одной кнопкой (с удалением из очереди и очисткой фрагментов)
        cur_yt = _utils.current_download_url
        if cur_yt and cur_yt in yt_urls:
            _utils.cancel_requested = True
            _utils.stop_requested = True
            _utils.update_status_ui("Отмена загрузки...", -1)
            log_message(f"INFO Запрошена отмена и удаление текущей загрузки: {cur_yt}")
            yt_urls = [u for u in yt_urls if u != cur_yt]

        queue = get_queue_urls()
        paused_url = queue[0] if (_utils.is_paused and queue) else None
        deleted_paused = False

        for url in yt_urls:
            if url == paused_url:
                _utils.is_paused = False
                deleted_paused = True
                removed = _utils.cleanup_download_fragments()
                log_message(f"INFO Удалены фрагменты приостановленной загрузки: {removed}")
            remove_from_queue(url)
            log_message(f"INFO Удалено из очереди: {url}")

        if deleted_paused:
            remaining = get_queue_urls()
            if remaining:
                from queues import process_queue
                threading.Thread(target=process_queue, daemon=True).start()
            else:
                _utils.update_status_ui("Ожидание...", -1)

        self._refresh()

    def _move_selected(self, delta: int):
        urls = self._selected_urls()
        if len(urls) != 1:
            return
        url = urls[0]
        if url.startswith("vk:"):
            q = _utils.vk_queue
            idxs = [i for i, item in enumerate(q) if item["key"] == url]
            if not idxs:
                return
            idx = idxs[0]
            new_idx = idx + (-1 if delta < 0 else 1)
            if 0 <= new_idx < len(q):
                q[idx], q[new_idx] = q[new_idx], q[idx]
                self._refresh()
        else:
            if move_in_queue(url, delta):
                self._refresh()

    def _export_queue(self):
        urls = [u for u in get_queue_urls() if u.startswith(("http://", "https://"))]
        if not urls:
            QMessageBox.information(self, "Экспорт очереди", "В очереди нет HTTP-ссылок.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Экспорт очереди",
            os.path.join(settings.get("download_folder") or os.path.expanduser("~"), "queue_links.txt"),
            "Текстовые файлы (*.txt);;Все файлы (*.*)"
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(urls) + "\n")
            log_message(f"INFO Экспортирована очередь ({len(urls)} ссылок) -> {path}")
            QMessageBox.information(self, "Экспорт очереди", f"Сохранено ссылок: {len(urls)}")
        except Exception as e:
            QMessageBox.warning(self, "Ошибка экспорта", str(e))

    def _import_queue(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Импорт ссылок в очередь",
            settings.get("download_folder") or os.path.expanduser("~"),
            "Текстовые файлы (*.txt);;Все файлы (*.*)"
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = [ln.strip() for ln in f if ln.strip().startswith(("http://", "https://"))]
        except Exception as e:
            QMessageBox.warning(self, "Ошибка импорта", str(e))
            return
        added = 0
        for u in lines:
            if add_to_queue(u):
                added += 1
        if added > 0:
            if not _utils.is_download_active():
                threading.Thread(target=process_queue, daemon=True).start()
            self._refresh()
            QMessageBox.information(self, "Импорт очереди", f"Добавлено в очередь: {added}")
        else:
            QMessageBox.information(self, "Импорт очереди", "Новых ссылок для добавления не найдено.")

    def _clear_queue(self):
        cur = _utils.current_download_url
        waiting_yt = [u for u in get_queue_urls() if u != cur]
        waiting_vk = list(_utils.vk_queue)
        if not cur and not waiting_yt and not waiting_vk:
            return
        reply = QMessageBox.question(
            self, "Очистить очередь",
            "Очистить всю очередь загрузок (включая отмену текущей загрузки, если она идёт)?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if cur:
            _utils.cancel_requested = True
            _utils.stop_requested = True
        else:
            if _utils.is_paused:
                _utils.is_paused = False
                _utils.cleanup_download_fragments()
            _utils.update_status_ui("Ожидание...", -1)
        clear_queue_file()
        for item in waiting_vk:
            _utils.queue_titles.pop(item["key"], None)
        _utils.vk_queue.clear()
        log_message("INFO Очередь полностью очищена пользователем")
        self._refresh()

    def closeEvent(self, event):
        event.accept()
        self.hide()
