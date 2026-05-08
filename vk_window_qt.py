"""
VK Music Search — PyQt6 window, интегрированный в YTD.
Логика поиска/скачивания портирована из vk_search.py (tkinter).
"""

import os
import re
import sys
import json
import time
import threading
import subprocess
import tempfile
from datetime import datetime
from urllib.parse import quote_plus

import utils as _utils

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QProgressBar, QMenu, QMessageBox, QFileDialog, QSizePolicy, QTabWidget
)
from PyQt6.QtCore import Qt, QObject, pyqtSignal, QMetaObject, Q_ARG
from PyQt6.QtGui import QFont

from logger import log_message
from config import settings

# ── Зависимости ──────────────────────────────────────────────────────────────

SELENIUM_OK = True
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from webdriver_manager.chrome import ChromeDriverManager
    from selenium.webdriver.chrome.service import Service
    from bs4 import BeautifulSoup
except Exception as _e:
    SELENIUM_OK = False
    log_message(f"WARNING VK: зависимости недоступны: {_e}")

try:
    import requests as _requests
    REQUESTS_OK = True
except Exception:
    REQUESTS_OK = False

VK_HISTORY_FILE = os.path.join(os.getcwd(), "vk_history.json")


# ── Сигналы (thread-safe) ─────────────────────────────────────────────────────

class _Sig(QObject):
    status              = pyqtSignal(str)
    progress            = pyqtSignal(float)   # 0-100
    speed               = pyqtSignal(str)
    batch               = pyqtSignal(str)
    show_progress       = pyqtSignal(bool)
    results_ready       = pyqtSignal(list)
    video_results_ready     = pyqtSignal(list)          # видео: [(title,dur,views,thumb_url,video_url)]
    video_description_ready = pyqtSignal(str, str)     # (заголовок, текст описания)
    thumb_ready             = pyqtSignal(object, bytes) # (QLabel, raw PNG/JPEG bytes)
    browser_ready       = pyqtSignal(bool)    # True = залогинен
    error               = pyqtSignal(str)
    search_done         = pyqtSignal()        # разблокировать кнопку


# ── История ───────────────────────────────────────────────────────────────────

def _load_vk_history():
    try:
        if os.path.exists(VK_HISTORY_FILE):
            with open(VK_HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return []

def _save_vk_history(records):
    try:
        with open(VK_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log_message(f"ERROR VK history save: {e}")

def _add_vk_history(artist, title, path):
    records = _load_vk_history()
    records.insert(0, {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "artist": artist,
        "title": title,
        "path": path,
    })
    _save_vk_history(records[:500])


class VKHistoryWindow(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("История загрузок ВК")
        self.setGeometry(120, 120, 800, 500)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        header = QLabel("История загрузок ВКонтакте")
        f = QFont(); f.setPointSize(12); f.setBold(True)
        header.setFont(f)
        layout.addWidget(header)

        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Дата", "Исполнитель", "Название", "Файл"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Закрыть")
        close_btn.setProperty("secondary", True)
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        self._load()

    def _load(self):
        records = _load_vk_history()
        self.table.setRowCount(len(records))
        for i, r in enumerate(records):
            self.table.setItem(i, 0, QTableWidgetItem(r.get("date", "")))
            self.table.setItem(i, 1, QTableWidgetItem(r.get("artist", "")))
            self.table.setItem(i, 2, QTableWidgetItem(r.get("title", "")))
            self.table.setItem(i, 3, QTableWidgetItem(r.get("path", "")))

    def closeEvent(self, e):
        e.accept()
        self.hide()


# ── Вспомогательные функции ───────────────────────────────────────────────────

def _fmt_sec(seconds: float) -> str:
    s = max(0, int(seconds))
    h = s // 3600; m = (s % 3600) // 60; s = s % 60
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

def _safe_name(text: str) -> str:
    return "".join(c for c in text if c not in '<>:"/\\|?*').strip() or "track"


# ── Вкладка результатов ───────────────────────────────────────────────────────

class _VKResultTab(QWidget):
    """Одна вкладка с результатами поиска ВК (таблица + фильтр)."""

    def __init__(self, query: str = ""):
        super().__init__()
        self.query = query

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Исполнитель", "Название", "Длит.", "Владелец"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)

        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setHighlightSections(False)

        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(24)

        layout.addWidget(self.table, 1)

        frow = QHBoxLayout()
        frow.setContentsMargins(0, 4, 0, 0)
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("Фильтр по исполнителю, названию...")
        frow.addWidget(self.filter_input)
        layout.addLayout(frow)


# ── Вкладка видео ────────────────────────────────────────────────────────────

class _VKVideoTab(QWidget):
    """Вкладка с результатами видео ВК (таблица с превью)."""

    THUMB_W, THUMB_H = 160, 90

    def __init__(self, query: str = ""):
        super().__init__()
        self.query = query
        self.results: list = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Превью", "Название", "Длит.", "Просмотры"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)

        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setHighlightSections(False)

        self.table.setColumnWidth(0, self.THUMB_W)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(self.THUMB_H + 4)

        layout.addWidget(self.table, 1)

        frow = QHBoxLayout()
        frow.setContentsMargins(0, 4, 0, 0)
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("Фильтр по названию...")
        frow.addWidget(self.filter_input)
        layout.addLayout(frow)


# ── Главное окно ──────────────────────────────────────────────────────────────

class VKSearchWindow(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.driver = None
        self._sig = _Sig()
        self._batch_mode = False
        self._sort_rev: dict[int, bool] = {}
        self._history_window = None

        self._build_ui()
        self._connect_signals()

        if not SELENIUM_OK:
            QMessageBox.critical(
                self, "Ошибка зависимостей",
                "Не найдены модули Selenium / webdriver-manager / bs4.\n\n"
                "Установите:\n  pip install selenium webdriver-manager beautifulsoup4"
            )
        else:
            threading.Thread(target=self._browser_worker, daemon=True).start()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.setWindowTitle("Поиск музыки ВКонтакте")
        self.setGeometry(100, 100, 950, 620)
        self.setMinimumSize(700, 400)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 8)
        root.setSpacing(6)

        # Заголовок + статус браузера
        top = QHBoxLayout()
        h = QLabel("Поиск музыки ВКонтакте")
        f = QFont(); f.setPointSize(12); f.setBold(True); h.setFont(f)
        top.addWidget(h)
        top.addStretch()
        self.browser_lbl = QLabel("● Браузер запускается...")
        self.browser_lbl.setStyleSheet("color: orange; font-weight: bold;")
        top.addWidget(self.browser_lbl)

        self.recheck_btn = QPushButton("Проверить вход")
        self.recheck_btn.setProperty("secondary", True)
        self.recheck_btn.setEnabled(False)
        self.recheck_btn.clicked.connect(self._recheck_login)
        top.addWidget(self.recheck_btn)

        root.addLayout(top)

        # Строка поиска
        row = QHBoxLayout(); row.setSpacing(6)
        self.query_input = QLineEdit()
        self.query_input.setPlaceholderText("Исполнитель / название, ссылка vk.com/... или wall...")
        self.query_input.returnPressed.connect(self._on_search)
        row.addWidget(self.query_input, 1)

        row.addWidget(QLabel("Кол-во:"))
        self.count_input = QLineEdit("0")
        self.count_input.setFixedWidth(48)
        row.addWidget(self.count_input)

        self.search_btn = QPushButton("Искать")
        self.search_btn.setEnabled(False)
        self.search_btn.clicked.connect(self._on_search)
        row.addWidget(self.search_btn)

        self.dl_btn = QPushButton("⬇ Скачать выбранные")
        self.dl_btn.setProperty("secondary", True)
        self.dl_btn.clicked.connect(self._download_selected)
        row.addWidget(self.dl_btn)

        hist_btn = QPushButton("История")
        hist_btn.setProperty("secondary", True)
        hist_btn.clicked.connect(self._show_history)
        row.addWidget(hist_btn)

        root.addLayout(row)

        # Тонкий прогресс-бар
        self.prog_bar = QProgressBar()
        self.prog_bar.setFixedHeight(3)
        self.prog_bar.setTextVisible(False)
        self.prog_bar.setVisible(False)
        root.addWidget(self.prog_bar)

        # Вкладки результатов
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)

        new_tab_btn = QPushButton("+")
        new_tab_btn.setFixedWidth(28)
        new_tab_btn.setToolTip("Новая вкладка")
        new_tab_btn.setProperty("secondary", True)
        new_tab_btn.clicked.connect(lambda: self._new_tab("Новая вкладка"))
        self.tabs.setCornerWidget(new_tab_btn, Qt.Corner.TopRightCorner)

        self._new_tab("Поиск")
        root.addWidget(self.tabs, 1)

        # Статус + скорость + batch
        bot = QHBoxLayout()
        self.status_lbl = QLabel("Готово")
        self.status_lbl.setProperty("secondary", True)
        bot.addWidget(self.status_lbl, 1)
        self.speed_lbl = QLabel("")
        self.speed_lbl.setProperty("secondary", True)
        bot.addWidget(self.speed_lbl)
        self.batch_lbl = QLabel("")
        self.batch_lbl.setProperty("secondary", True)
        bot.addWidget(self.batch_lbl)
        root.addLayout(bot)

    def _connect_signals(self):
        self._sig.status.connect(self.status_lbl.setText)
        self._sig.speed.connect(self.speed_lbl.setText)
        self._sig.batch.connect(self.batch_lbl.setText)
        self._sig.show_progress.connect(self._on_show_progress)
        self._sig.progress.connect(lambda v: self.prog_bar.setValue(int(v)))
        self._sig.results_ready.connect(self._populate_table)
        self._sig.video_results_ready.connect(self._populate_video_tab)
        self._sig.video_description_ready.connect(self._on_video_description)
        self._sig.thumb_ready.connect(self._on_thumb_ready)
        self._sig.browser_ready.connect(self._on_browser_ready)
        self._sig.error.connect(lambda m: QMessageBox.critical(self, "Ошибка", m))
        self._sig.search_done.connect(lambda: self.search_btn.setEnabled(True))

    def _on_show_progress(self, visible: bool):
        self.prog_bar.setVisible(visible)
        if not visible:
            self.prog_bar.setValue(0)
            self.speed_lbl.setText("")
            self.batch_lbl.setText("")

    def _on_browser_ready(self, ok: bool):
        self.recheck_btn.setEnabled(True)
        if ok:
            self.browser_lbl.setText("● Залогинен в ВК")
            self.browser_lbl.setStyleSheet("color: #4caf50; font-weight: bold;")
            self.search_btn.setEnabled(True)
        else:
            self.browser_lbl.setText("● Войдите в ВК в браузере")
            self.browser_lbl.setStyleSheet("color: #e74c3c; font-weight: bold;")
            self.search_btn.setEnabled(True)  # всё равно даём попробовать

    def _recheck_login(self):
        """Ручная проверка состояния входа в ВК"""
        if not self.driver:
            QMessageBox.warning(self, "Браузер не готов", "Браузер ещё не запущен.")
            return
        self.recheck_btn.setEnabled(False)
        self.browser_lbl.setText("● Проверка...")
        self.browser_lbl.setStyleSheet("color: orange; font-weight: bold;")
        threading.Thread(target=self._do_recheck, daemon=True).start()

    def _do_recheck(self):
        ok = self._is_logged_in()
        self._sig.browser_ready.emit(ok)

    # ── Вспомогательные методы для вкладок ───────────────────────────────────

    def _t(self):
        w = self.tabs.currentWidget()
        return w.table if isinstance(w, (_VKResultTab, _VKVideoTab)) else None

    def _f(self):
        w = self.tabs.currentWidget()
        return w.filter_input if isinstance(w, (_VKResultTab, _VKVideoTab)) else None

    def _current_tab(self):
        w = self.tabs.currentWidget()
        return w if isinstance(w, _VKResultTab) else None

    def _current_video_tab(self):
        w = self.tabs.currentWidget()
        return w if isinstance(w, _VKVideoTab) else None

    def _new_tab(self, query: str) -> _VKResultTab:
        tab = _VKResultTab(query)
        tab.filter_input.textChanged.connect(self._filter)
        tab.table.doubleClicked.connect(self._download_selected)
        tab.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        tab.table.customContextMenuRequested.connect(self._show_ctx_menu)
        tab.table.horizontalHeader().sectionClicked.connect(self._sort_col)
        title = (query[:22] + "…") if len(query) > 22 else (query or "Результаты")
        idx = self.tabs.addTab(tab, title)
        self.tabs.setCurrentIndex(idx)
        return tab

    def _new_video_tab(self, query: str) -> _VKVideoTab:
        tab = _VKVideoTab(query)
        tab.filter_input.textChanged.connect(self._filter)
        tab.table.doubleClicked.connect(self._download_selected)
        tab.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        tab.table.customContextMenuRequested.connect(self._show_video_ctx_menu)
        title = (query[:22] + "…") if len(query) > 22 else (query or "Видео")
        idx = self.tabs.addTab(tab, f"🎬 {title}")
        self.tabs.setCurrentIndex(idx)
        return tab

    def _close_tab(self, index: int):
        self.tabs.removeTab(index)

    # ── Таблица ───────────────────────────────────────────────────────────────

    def _populate_table(self, results: list):
        query = getattr(self, '_pending_vk_query', self.query_input.text().strip())
        tab = self._current_tab() or self._new_tab(query)
        tab.query = query
        title = (query[:22] + "…") if len(query) > 22 else (query or "Результаты")
        self.tabs.setTabText(self.tabs.currentIndex(), title)
        tab.filter_input.blockSignals(True)
        tab.filter_input.clear()
        tab.filter_input.blockSignals(False)

        for row_data in results:
            if len(row_data) < 6:
                continue
            artist, title, duration, owner, url, full_id = row_data[:6]
            r = tab.table.rowCount()
            tab.table.insertRow(r)

            artist_item = QTableWidgetItem(artist)
            artist_item.setData(Qt.ItemDataRole.UserRole,     url)
            artist_item.setData(Qt.ItemDataRole.UserRole + 1, full_id)
            tab.table.setItem(r, 0, artist_item)
            tab.table.setItem(r, 1, QTableWidgetItem(title))
            tab.table.setItem(r, 2, QTableWidgetItem(duration))
            tab.table.setItem(r, 3, QTableWidgetItem(owner))

        total = tab.table.rowCount()
        self._sig.status.emit(f"Найдено треков: {total}" if total else "Ничего не найдено")

    def _row_data(self, row: int):
        t = self._t()
        item = t.item(row, 0) if t else None
        if not item:
            return None
        return {
            "artist":   item.text(),
            "title":    t.item(row, 1).text() if t.item(row, 1) else "",
            "duration": t.item(row, 2).text() if t.item(row, 2) else "",
            "owner":    t.item(row, 3).text() if t.item(row, 3) else "",
            "url":      item.data(Qt.ItemDataRole.UserRole) or "",
            "full_id":  item.data(Qt.ItemDataRole.UserRole + 1) or "",
        }

    def _selected_rows_data(self) -> list[dict]:
        t = self._t()
        if not t:
            return []
        seen = set()
        result = []
        for idx in t.selectedItems():
            r = idx.row()
            if r not in seen:
                seen.add(r)
                d = self._row_data(r)
                if d:
                    result.append(d)
        return result

    def _filter(self, text: str):
        t = self._t()
        if not t:
            return
        lo = text.lower()
        is_video = isinstance(self.tabs.currentWidget(), _VKVideoTab)
        visible = 0
        for r in range(t.rowCount()):
            if is_video:
                val = (t.item(r, 1).text() if t.item(r, 1) else "").lower()
            else:
                artist = (t.item(r, 0).text() if t.item(r, 0) else "").lower()
                title  = (t.item(r, 1).text() if t.item(r, 1) else "").lower()
                val = artist + " " + title
            hidden = bool(lo) and lo not in val
            t.setRowHidden(r, hidden)
            if not hidden:
                visible += 1
        total = t.rowCount()
        label = "видео" if is_video else "треков"
        self.status_lbl.setText(f"Фильтр: {visible} из {total}" if lo else f"Найдено {label}: {total}")

    def _sort_col(self, col: int):
        t = self._t()
        if not t:
            return
        rev = self._sort_rev.get(col, False)
        if col == 2:  # длительность — числовая
            def key(r):
                txt = t.item(r, 2).text() if t.item(r, 2) else ""
                try:
                    p = txt.split(":")
                    return int(p[0]) * 60 + int(p[1]) if len(p) == 2 else 0
                except Exception:
                    return 0
        else:
            def key(r):
                item = t.item(r, col)
                return item.text().lower() if item else ""

        rows = list(range(t.rowCount()))
        rows.sort(key=key, reverse=rev)
        self._sort_rev[col] = not rev

        buf = []
        for r in rows:
            row_buf = []
            for c in range(t.columnCount()):
                it = t.item(r, c)
                row_buf.append({
                    "text": it.text() if it else "",
                    "ur":  it.data(Qt.ItemDataRole.UserRole)     if it else None,
                    "ur1": it.data(Qt.ItemDataRole.UserRole + 1) if it else None,
                })
            buf.append(row_buf)

        for r, row_buf in enumerate(buf):
            for c, d in enumerate(row_buf):
                it = QTableWidgetItem(d["text"])
                if c == 0:
                    it.setData(Qt.ItemDataRole.UserRole,     d["ur"])
                    it.setData(Qt.ItemDataRole.UserRole + 1, d["ur1"])
                t.setItem(r, c, it)

    # ── Контекстное меню видео ───────────────────────────────────────────────

    def _show_video_ctx_menu(self, pos):
        t = self._t()
        if not t:
            return
        row = t.rowAt(pos.y())
        if row < 0:
            return
        if not t.item(row, 1):
            return
        t.selectRow(row)
        menu = QMenu(self)
        menu.addAction("Описание", self._show_video_description)
        menu.addSeparator()
        menu.addAction("Скачать видео",        self._download_selected)
        menu.addAction("Копировать ссылку",    self._copy_video_link)
        menu.addSeparator()
        menu.addAction("Выбрать все", t.selectAll)
        menu.exec(t.viewport().mapToGlobal(pos))

    def _selected_video_url(self) -> str:
        t = self._t()
        if not t:
            return ""
        for item in t.selectedItems():
            if item.column() == 1:
                return item.data(Qt.ItemDataRole.UserRole) or ""
        return ""

    def _copy_video_link(self):
        url = self._selected_video_url()
        if url:
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(url)
            self._sig.status.emit("Ссылка скопирована")

    def _show_video_description(self):
        url = self._selected_video_url()
        if not url or not self.driver:
            return
        threading.Thread(target=self._fetch_video_description, args=(url,), daemon=True).start()

    def _fetch_video_description(self, url: str):
        try:
            self._sig.status.emit("Загружаю описание...")
            self.driver.get(url)
            time.sleep(3)
            soup = BeautifulSoup(self.driver.page_source, "html.parser")

            desc = ""

            # Основной контейнер описания — data-testid="showmoretext"
            block = soup.find(attrs={"data-testid": "showmoretext"})
            if block:
                # Текст внутри vkitShowMoreText__text (класс с хэш-суффиксом)
                text_el = block.find(class_=lambda c: c and "vkitShowMoreText__text" in " ".join(c))
                if text_el:
                    desc = text_el.get_text(separator="\n", strip=True)
                else:
                    desc = block.get_text(separator="\n", strip=True)

            # Fallback: meta og:description
            if not desc:
                for meta in soup.find_all("meta"):
                    if meta.get("property") == "og:description" or meta.get("name") == "description":
                        desc = meta.get("content", "")
                        if desc:
                            break

            title_el = soup.find("title")
            title = title_el.get_text(strip=True) if title_el else url

            self._sig.video_description_ready.emit(title, desc or "Описание не найдено")
        except Exception as e:
            self._sig.video_description_ready.emit("Ошибка", str(e))

    def _on_video_description(self, title: str, desc: str):
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QTextEdit, QPushButton
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.setMinimumSize(520, 380)
        lay = QVBoxLayout(dlg)
        te = QTextEdit()
        te.setReadOnly(True)
        te.setPlainText(desc)
        lay.addWidget(te)
        btn = QPushButton("Закрыть")
        btn.clicked.connect(dlg.accept)
        lay.addWidget(btn)
        dlg.exec()

    # ── Контекстное меню ──────────────────────────────────────────────────────

    def _show_ctx_menu(self, pos):
        t = self._t()
        if not t:
            return
        row = t.rowAt(pos.y())
        if row < 0:
            return
        menu = QMenu(self)
        menu.addAction("Копировать «Исполнитель — Название»", self._copy_artist_title)
        menu.addAction("Копировать ссылку на владельца",      self._copy_owner_link)
        menu.addSeparator()
        menu.addAction("Скачать трек",         self._download_one)
        menu.addAction("Скачать выбранные",    self._download_selected)
        menu.addSeparator()
        menu.addAction("Выбрать все", t.selectAll)
        menu.exec(t.viewport().mapToGlobal(pos))

    def _copy_artist_title(self):
        rows = self._selected_rows_data()
        if not rows:
            return
        d = rows[0]
        text = f"{d['artist']} — {d['title']}".strip(" —")
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(text)
        self._sig.status.emit("Скопировано в буфер")

    def _copy_owner_link(self):
        rows = self._selected_rows_data()
        if not rows:
            return
        owner = rows[0]["owner"]
        if owner.startswith("id"):
            url = f"https://vk.com/{owner}"
        elif owner.startswith("club"):
            url = f"https://vk.com/{owner}"
        else:
            try:
                oid = int(owner)
                url = f"https://vk.com/club{abs(oid)}" if oid < 0 else f"https://vk.com/id{oid}"
            except Exception:
                self._sig.status.emit("Нет данных о владельце")
                return
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(url)
        self._sig.status.emit("Ссылка скопирована")

    # ── История ───────────────────────────────────────────────────────────────

    def _show_history(self):
        if self._history_window is None:
            self._history_window = VKHistoryWindow()
            import styles as _styles
            self._history_window.setStyleSheet(_styles.STYLESHEET_MINIMAL)
        else:
            self._history_window._load()
        self._history_window.show()
        self._history_window.raise_()
        self._history_window.activateWindow()

    # ── Браузер / Логин ───────────────────────────────────────────────────────

    def _browser_worker(self):
        try:
            log_message("INFO VK: запуск браузера")
            opts = webdriver.ChromeOptions()

            # Сохраняем профиль Chrome между запусками (куки, сессия ВК)
            profile_dir = os.path.join(os.getcwd(), ".vk_chrome_profile")
            os.makedirs(profile_dir, exist_ok=True)
            opts.add_argument(f"--user-data-dir={profile_dir}")

            opts.add_argument("--start-maximized")
            opts.add_argument("--disable-blink-features=AutomationControlled")
            opts.add_argument(
                "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            )
            opts.set_capability("goog:loggingPrefs", {"performance": "ALL"})
            try:
                svc = Service(ChromeDriverManager().install())
                self.driver = webdriver.Chrome(service=svc, options=opts)
            except Exception:
                self.driver = webdriver.Chrome(options=opts)

            self.driver.get("https://vk.com")
            log_message("INFO VK: браузер открыт, жду логина...")
            # Разблокируем кнопку "Проверить вход" как только браузер открылся
            QMetaObject.invokeMethod(
                self.recheck_btn, "setEnabled",
                Qt.ConnectionType.QueuedConnection,
                Q_ARG(bool, True)
            )
            self._wait_login()
        except Exception as e:
            log_message(f"ERROR VK браузер: {e}")
            self._sig.error.emit(f"Ошибка запуска браузера ВК:\n{e}")
            self._sig.browser_ready.emit(False)

    def _is_logged_in(self) -> bool:
        if not self.driver:
            return False
        try:
            url = self.driver.current_url
            # Если на странице логина/регистрации — точно не залогинены
            if any(x in url for x in ("login", "join", "blank")):
                return False
            # Ещё не на vk.com
            if "vk.com" not in url:
                return False
            # Есть форма входа — не залогинены
            if self.driver.find_elements(
                By.CSS_SELECTOR,
                "form[action*='login'], input[name='email'], input[name='login'], "
                "input[name='pass'], .VkIdForm, .vkc__Root"
            ):
                return False
            # Старые и новые селекторы залогиненного состояния
            for sel in [
                "a#top_profile_link", "a.top_profile_link", "a.TopNavBtn__profileLink",
                "div#side_bar", "nav.left_menu_nav_wrap",
                ".vkuiAvatar", ".UserAvatar", ".Header__userMenu",
                "[data-testid='header_user_link']",
            ]:
                if self.driver.find_elements(By.CSS_SELECTOR, sel):
                    return True
            # Если на vk.com, нет формы логина — считаем залогиненным
            return True
        except Exception:
            pass
        return False

    def _wait_login(self):
        for _ in range(150):   # до 5 минут
            if self._is_logged_in():
                log_message("INFO VK: логин обнаружен")
                self._sig.browser_ready.emit(True)
                return
            time.sleep(2)
        log_message("WARNING VK: логин не обнаружен за 5 мин")
        self._sig.browser_ready.emit(False)

    # ── Поиск ────────────────────────────────────────────────────────────────

    def _on_search(self):
        if not self.driver:
            QMessageBox.warning(self, "Браузер не готов",
                                "Подождите, пока браузер запустится и войдите в ВК.")
            return
        query = self.query_input.text().strip()
        if not query:
            self._sig.status.emit("Введите запрос!")
            return
        try:
            count = max(0, min(500, int(self.count_input.text().strip() or "30")))
        except ValueError:
            count = 30

        self._pending_vk_query = query
        self.search_btn.setEnabled(False)
        self._sig.status.emit("Поиск...")

        # Определяем тип запроса

        # Видео: vk.com/video/@id... или vkvideo.ru/@...
        m_video = re.match(
            r'^(?:https?://)?(?:www\.)?(?:vk\.com/video|vkvideo\.ru)([/?@].*)?$',
            query.strip(), re.I
        )
        if m_video:
            vurl = query.strip()
            if not vurl.startswith('http'):
                vurl = 'https://' + vurl
            threading.Thread(
                target=self._worker_video, args=(vurl, count), daemon=True
            ).start()
            return

        # Прямая ссылка на аудиозаписи: vk.com/audios-129016356
        m_audios = re.match(
            r'^(?:https?://)?(?:www\.)?vk\.com/audios(-?\d+)(?:\?.*)?$',
            query.strip(), re.I
        )
        if m_audios:
            threading.Thread(
                target=self._worker_direct_audios, args=(m_audios.group(1), count), daemon=True
            ).start()
            return

        wall = self._parse_wall_url(query)
        if wall:
            owner_id, post_id = wall
            threading.Thread(
                target=self._worker_wall, args=(owner_id, post_id, count), daemon=True
            ).start()
            return

        profile = self._parse_profile_url(query)
        if profile:
            threading.Thread(
                target=self._worker_profile, args=(profile, count), daemon=True
            ).start()
            return

        threading.Thread(target=self._worker_search, args=(query, count), daemon=True).start()

    @staticmethod
    def _parse_wall_url(text: str):
        m = re.match(r'^(?:https?://)?(?:www\.)?vk\.com/wall(-?\d+)_(\d+)(?:\?.*)?$',
                     text.strip(), re.I)
        return (m.group(1), m.group(2)) if m else None

    @staticmethod
    def _parse_profile_url(text: str):
        m = re.match(r'^(?:https?://)?(?:www\.)?vk\.com/([a-zA-Z0-9._]+)(?:\?.*)?$',
                     text.strip())
        if m:
            pid = m.group(1)
            excluded = {'audio','audios','music','feed','im','friends',
                        'groups','photos','video','docs','settings','login'}
            if pid.lower() not in excluded:
                return pid
        return None

    # ── Воркеры поиска ───────────────────────────────────────────────────────

    def _worker_search(self, query: str, count: int):
        try:
            url = f"https://vk.com/audio?q={quote_plus(query)}&section=search"
            self.driver.get(url)
            try:
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.CLASS_NAME, "audio_row"))
                )
            except Exception:
                pass

            # Кликаем «Показать все» только если она стоит ДО первого audio_row
            # (это кнопка заголовка секции треков). Кнопка плейлистов идёт ПОСЛЕ
            # audio_row-ов, поэтому compareDocumentPosition её отсеет.
            try:
                show_all = self.driver.execute_script("""
                    var firstRow = document.querySelector('.audio_row');
                    if (!firstRow) return null;
                    var links = Array.from(document.querySelectorAll('a'));
                    for (var i = 0; i < links.length; i++) {
                        var a = links[i];
                        if (a.textContent.trim() !== 'Показать все') continue;
                        // DOCUMENT_POSITION_FOLLOWING (4) — firstRow стоит ПОСЛЕ a
                        if (a.compareDocumentPosition(firstRow) & 4) return a;
                    }
                    return null;
                """)
                if show_all:
                    self._sig.status.emit("Открываю все треки...")
                    prev_url = self.driver.current_url
                    self.driver.execute_script("arguments[0].click();", show_all)
                    try:
                        WebDriverWait(self.driver, 10).until(
                            lambda d: d.current_url != prev_url
                        )
                    except Exception:
                        pass
                    WebDriverWait(self.driver, 10).until(
                        EC.presence_of_element_located((By.CLASS_NAME, "audio_row"))
                    )
            except Exception:
                pass

            results = self._scroll_and_parse(count)
            self._sig.results_ready.emit(results)
        except Exception as e:
            log_message(f"ERROR VK search: {e}")
            self._sig.status.emit(f"Ошибка: {e}")
        finally:
            self._sig.search_done.emit()

    def _worker_profile(self, profile_id: str, count: int):
        try:
            self._sig.status.emit("Открываю профиль...")
            self.driver.get(f"https://vk.com/{profile_id}")
            time.sleep(2)
            cur = self.driver.current_url
            numeric_id = None
            m = re.search(r'vk\.com/id(\d+)', cur)
            if m:
                numeric_id = m.group(1)
            m = re.search(r'vk\.com/(?:club|public)(\d+)', cur)
            if m:
                numeric_id = f"-{m.group(1)}"
            if not numeric_id:
                src = self.driver.page_source
                m = re.search(r'"(?:oid|owner_id)"\s*:\s*(-?\d+)', src)
                numeric_id = m.group(1) if m else profile_id

            self._sig.status.emit("Открываю аудиозаписи...")
            self.driver.get(f"https://vk.com/audios{numeric_id}")
            try:
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.CLASS_NAME, "audio_row"))
                )
            except Exception:
                self._sig.status.emit("Аудио недоступны или скрыты")
                return

            results = self._scroll_and_parse(count)
            self._sig.results_ready.emit(results)
        except Exception as e:
            log_message(f"ERROR VK profile: {e}")
            self._sig.status.emit(f"Ошибка: {e}")
        finally:
            self._sig.search_done.emit()

    def _worker_direct_audios(self, owner_id: str, count: int):
        """Прямой переход на vk.com/audiosXXX без промежуточной загрузки профиля."""
        try:
            self._sig.status.emit("Открываю аудиозаписи...")
            self.driver.get(f"https://vk.com/audios{owner_id}")
            try:
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.CLASS_NAME, "audio_row"))
                )
            except Exception:
                self._sig.status.emit("Аудио недоступны или скрыты")
                return
            results = self._scroll_and_parse(count)
            self._sig.results_ready.emit(results)
        except Exception as e:
            log_message(f"ERROR VK direct audios: {e}")
            self._sig.status.emit(f"Ошибка: {e}")
        finally:
            self._sig.search_done.emit()

    def _worker_wall(self, owner_id: str, post_id: str, count: int):
        try:
            self._sig.status.emit("Открываю пост...")
            wall_url = f"https://m.vk.com/wall{owner_id}_{post_id}"
            self.driver.get(wall_url)
            time.sleep(3)
            html = self.driver.page_source
            soup = BeautifulSoup(html, "html.parser")

            pl_link = soup.find("a", href=lambda h: h and ("audio_playlist" in h or "/music/playlist/" in h))
            if pl_link and "act=audio_playlists" not in pl_link.get("href", ""):
                href = pl_link["href"]
                full_url = href if href.startswith("http") else "https://m.vk.com" + href
                self._sig.status.emit("Загружаю плейлист из поста...")
                self.driver.get(full_url)
                time.sleep(3)
                for _ in range(10):
                    last = self.driver.execute_script("return document.body.scrollHeight")
                    self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                    time.sleep(1.5)
                    if self.driver.execute_script("return document.body.scrollHeight") == last:
                        break
                html = self.driver.page_source
                soup = BeautifulSoup(html, "html.parser")

            self._sig.status.emit("Парсю треки...")
            audio_items = soup.find_all("div", class_="audio_item")
            results = []
            seen = set()
            for item in audio_items:
                try:
                    full_id = (item.get("data-full-id") or item.get("data-id") or item.get("id") or "").replace("audio", "")
                    if not full_id or full_id in seen:
                        continue
                    artist = (item.select_one(".ai_artist") or type("", (), {"get_text": lambda *a, **k: "Неизвестен"})()).get_text(strip=True)
                    title  = (item.select_one(".ai_title")  or type("", (), {"get_text": lambda *a, **k: "Без названия"})()).get_text(strip=True)
                    dur_tag = item.select_one(".ai_dur")
                    duration = ""
                    if dur_tag:
                        sec = dur_tag.get("data-dur")
                        if sec:
                            try:
                                s = int(sec); duration = f"{s//60}:{s%60:02d}"
                            except Exception:
                                pass
                        if not duration:
                            duration = dur_tag.get_text(strip=True)
                    if title == "Без названия" and artist == "Неизвестен":
                        continue
                    seen.add(full_id)
                    results.append((artist, title, duration, "mobile", "", full_id))
                    if count and len(results) >= count:
                        break
                except Exception:
                    continue
            self._sig.results_ready.emit(results)
        except Exception as e:
            log_message(f"ERROR VK wall: {e}")
            self._sig.status.emit(f"Ошибка: {e}")
        finally:
            self._sig.search_done.emit()

    # ── Видео ────────────────────────────────────────────────────────────────

    def _worker_video(self, url: str, count: int):
        try:
            self._sig.status.emit("Открываю страницу видео...")
            self.driver.get(url)
            time.sleep(2)

            limit = count if count > 0 else None
            last_h = self.driver.execute_script("return document.body.scrollHeight")
            for i in range(50):
                parsed = self._parse_video_html(self.driver.page_source, limit)
                self._sig.status.emit(f"Загружаю видео... ({len(parsed)})")
                if limit and len(parsed) >= limit:
                    break
                self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(1.5)
                new_h = self.driver.execute_script("return document.body.scrollHeight")
                if new_h == last_h:
                    break
                last_h = new_h

            results = self._parse_video_html(self.driver.page_source, limit)
            log_message(f"INFO VK video: найдено {len(results)} видео")
            self._sig.video_results_ready.emit(results)
        except Exception as e:
            log_message(f"ERROR VK video: {e}")
            self._sig.status.emit(f"Ошибка: {e}")
        finally:
            self._sig.search_done.emit()

    @staticmethod
    def _parse_video_html(html: str, max_count) -> list:
        """Парсит страницу видео ВК, возвращает [(title, dur, views, thumb_url, video_url)]."""
        soup = BeautifulSoup(html, "html.parser")

        # Href может быть /video-NNN_NNN или https://vkvideo.ru/video-NNN_NNN
        video_link_re = re.compile(r'(?:https?://[^/]+)?/video-?\d+_\d+')
        all_links = soup.find_all("a", href=video_link_re)

        # На каждую карточку обычно две ссылки с одним URL:
        # 1) ссылка-превью (содержит <img>, текст пустой)
        # 2) ссылка-название (текст = заголовок видео)
        # Группируем по нормализованному URL и берём лучшее из обеих ссылок.
        url_order: list[str] = []
        url_data: dict[str, dict] = {}

        for link in all_links:
            try:
                href = link.get("href", "")
                video_url = href if href.startswith("http") else "https://vk.com" + href
                base = video_url.split("?")[0]

                if base not in url_data:
                    url_data[base] = {"url": video_url, "title": "", "thumb": "", "dur": ""}
                    url_order.append(base)

                entry = url_data[base]

                # Название: берём первый непустой текст, не похожий на длительность
                if not entry["title"]:
                    txt = link.get_text(strip=True)
                    if txt and len(txt) > 2 and not re.match(r'^\d{1,2}:\d{2}', txt):
                        entry["title"] = txt

                # Превью: берём из <img> внутри ссылки
                if not entry["thumb"]:
                    img = link.find("img")
                    if img:
                        entry["thumb"] = img.get("src") or img.get("data-src") or ""

                # Длительность: ищем M:SS или H:MM:SS в тексте ссылки
                if not entry["dur"]:
                    m = re.search(r'\b(\d{1,2}:\d{2}(?::\d{2})?)\b', link.get_text())
                    if m:
                        entry["dur"] = m.group(1)
            except Exception:
                continue

        results = []
        for base in url_order:
            if max_count and len(results) >= max_count:
                break
            d = url_data[base]
            results.append((
                (d["title"] or "Без названия")[:120],
                d["dur"], "", d["thumb"], d["url"]
            ))
        return results

    def _populate_video_tab(self, results: list):
        query = getattr(self, '_pending_vk_query', '')
        tab = self._current_video_tab() or self._new_video_tab(query)
        tab.query = query
        tab.results = results
        short = (query[:22] + "…") if len(query) > 22 else (query or "Видео")
        self.tabs.setTabText(self.tabs.currentIndex(), f"🎬 {short}")

        tab.table.setRowCount(0)
        for title, dur, views, thumb, vurl in results:
            r = tab.table.rowCount()
            tab.table.insertRow(r)

            # Превью — QLabel, изображение загружается асинхронно
            lbl = QLabel()
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setFixedSize(_VKVideoTab.THUMB_W, _VKVideoTab.THUMB_H)
            tab.table.setCellWidget(r, 0, lbl)
            if thumb:
                threading.Thread(
                    target=self._load_video_thumb, args=(thumb, lbl), daemon=True
                ).start()

            # Название + URL в UserRole
            title_item = QTableWidgetItem(title)
            title_item.setData(Qt.ItemDataRole.UserRole, vurl)
            title_item.setToolTip(vurl)
            tab.table.setItem(r, 1, title_item)
            tab.table.setItem(r, 2, QTableWidgetItem(dur))
            tab.table.setItem(r, 3, QTableWidgetItem(views))

        total = tab.table.rowCount()
        self._sig.status.emit(f"Найдено видео: {total}" if total else "Видео не найдено")
        if not total:
            log_message(f"WARNING VK video: таблица пуста. Проверьте HTML-структуру страницы.")

    def _load_video_thumb(self, url: str, lbl):
        try:
            resp = _requests.get(url, timeout=10, headers={"Referer": "https://vk.com/"})
            if resp.status_code == 200:
                self._sig.thumb_ready.emit(lbl, resp.content)
        except Exception:
            pass

    def _on_thumb_ready(self, lbl, data: bytes):
        from PyQt6.QtGui import QPixmap
        px = QPixmap()
        px.loadFromData(data)
        if not px.isNull():
            px = px.scaled(
                _VKVideoTab.THUMB_W, _VKVideoTab.THUMB_H,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            lbl.setPixmap(px)

    def _download_vk_videos_selected(self):
        tab = self._current_video_tab()
        if not tab:
            return
        t = tab.table
        seen, urls = set(), []
        for item in t.selectedItems():
            if item.column() != 1:
                continue
            url = item.data(Qt.ItemDataRole.UserRole)
            if url and url not in seen:
                seen.add(url)
                urls.append(url)
        if not urls:
            self._sig.status.emit("Не выбрано ни одного видео")
            return
        import config as _cfg
        from download import download_video
        from queues import add_to_queue
        for url in urls:
            if _cfg.is_downloading:
                add_to_queue(url)
            else:
                threading.Thread(target=download_video, args=(url,), daemon=True).start()
        self._sig.status.emit(f"Добавлено в очередь: {len(urls)} видео")

    def _scroll_and_parse(self, count: int) -> list:
        limit = count if count > 0 else None
        results = self._parse_html(self.driver.page_source, limit)
        if limit and len(results) >= limit:
            return results[:limit]

        last_h = self.driver.execute_script("return document.body.scrollHeight")
        for i in range(20):
            self._sig.status.emit(f"Загружаю треки... ({len(results)}/{limit or '∞'})")
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1.5)
            results = self._parse_html(self.driver.page_source, limit)
            if limit and len(results) >= limit:
                break
            new_h = self.driver.execute_script("return document.body.scrollHeight")
            if new_h == last_h:
                break
            last_h = new_h
        return results[:limit] if limit else results

    @staticmethod
    def _parse_html(html: str, max_count) -> list:
        if not html or len(html) < 100:
            return []
        soup = BeautifulSoup(html, "html.parser")
        rows = soup.find_all("div", class_=lambda c: c and "audio_row" in c)
        results = []
        seen = set()
        for row in rows:
            try:
                if any(c in row.get("class", []) for c in ["audio_claimed"]):
                    continue
                data_attr = row.get("data-audio")
                if not data_attr:
                    continue
                data = json.loads(data_attr)
                if len(data) < 6:
                    continue
                audio_id, owner_id = str(data[0]), str(data[1])
                link = str(data[2]) if len(data) > 2 else ""
                if "audio_api_unavailable" in link:
                    continue
                title  = BeautifulSoup(str(data[3] or ""), "html.parser").get_text(strip=True)
                artist = BeautifulSoup(str(data[4] or ""), "html.parser").get_text(strip=True)
                if not title or "аудио доступно на vk.com" in title.lower():
                    continue
                total_sec = int(data[5] or 0)
                if total_sec <= 0:
                    continue
                duration = f"{total_sec//60}:{total_sec%60:02d}"
                full_id = f"{owner_id}_{audio_id}"
                if full_id in seen:
                    continue
                seen.add(full_id)
                try:
                    oi = int(owner_id)
                    owner_disp = f"club{abs(oi)}" if oi < 0 else f"id{oi}"
                except ValueError:
                    owner_disp = owner_id
                results.append((artist[:80], title[:120], duration, owner_disp, link, full_id))
                if max_count and len(results) >= max_count:
                    break
            except Exception:
                continue
        return results

    # ── Утилиты ──────────────────────────────────────────────────────────────

    @staticmethod
    def _tray_status(status: str, progress: int):
        """Обновляет иконку трея через мост app_qt.py."""
        tray = sys.modules.get("tray")
        if tray and hasattr(tray, "update_download_status"):
            tray.update_download_status(status, progress if progress >= 0 else None)

    # ── Скачивание ────────────────────────────────────────────────────────────

    def _download_one(self):
        rows = self._selected_rows_data()
        if not rows:
            return
        d = rows[0]
        if not d["full_id"]:
            self._sig.status.emit("Нет ID трека")
            return
        base = _safe_name(f"{d['artist']} - {d['title']}") or "track"
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить трек", base + ".mp3",
            "Аудио MP3 (*.mp3);;Все файлы (*.*)"
        )
        if not path:
            return
        threading.Thread(
            target=self._dl_single_worker, args=(d, path), daemon=True
        ).start()

    def _download_selected(self):
        if isinstance(self.tabs.currentWidget(), _VKVideoTab):
            self._download_vk_videos_selected()
            return
        rows = self._selected_rows_data()
        if not rows:
            self._sig.status.emit("Не выбрано ни одного трека")
            return
        folder = QFileDialog.getExistingDirectory(
            self, f"Папка для {len(rows)} треков",
            settings.get("download_folder", "")
        )
        if not folder:
            return
        threading.Thread(
            target=self._dl_batch_worker, args=(rows, folder), daemon=True
        ).start()

    def _dl_single_worker(self, d: dict, path: str):
        label = f"{d['artist']} - {d['title']}"
        key = f"vk:{d['full_id']}"
        _utils.queue_titles[key] = f"[ВК] {label}"
        _utils.current_vk_key = key
        self._sig.show_progress.emit(True)
        self._sig.progress.emit(0)
        self._tray_status("Загрузка...", 0)
        ok = False
        if self.driver:
            ok = self._dl_via_browser(d["full_id"], path)
        if not ok and d["url"].startswith("http"):
            ok = self._dl_direct(d["url"], path)
        _utils.current_vk_key = ""
        _utils.queue_titles.pop(key, None)
        self._sig.show_progress.emit(False)
        self._tray_status("Ожидание..." if not ok else "Готово!", -1)
        if ok:
            self._sig.status.emit("✓ Трек скачан!")
            _add_vk_history(d["artist"], d["title"], path)
        else:
            self._sig.status.emit("Не удалось скачать трек")
            self._sig.error.emit(
                "Не удалось скачать трек.\n\n"
                "Возможные причины:\n"
                "• Трек недоступен\n"
                "• Проблемы с авторизацией\n"
                "• Нужен yt-dlp и ffmpeg"
            )

    def _dl_batch_worker(self, rows: list[dict], folder: str):
        self._batch_mode = True
        total = len(rows)
        self._sig.show_progress.emit(True)
        ok_count = fail_count = 0
        start_t = time.time()
        failed = []

        # Заполняем VK-очередь
        _utils.vk_queue = [
            {"key": f"vk:{d['full_id']}", "label": f"{d['artist']} - {d['title']}"}
            for d in rows
        ]
        for item in _utils.vk_queue:
            _utils.queue_titles[item["key"]] = f"[ВК] {item['label']}"

        for i, d in enumerate(rows, 1):
            base = _safe_name(f"{d['artist']} - {d['title']}") or f"track_{i}"
            path = os.path.join(folder, base + ".mp3")
            cnt = 1
            orig = path
            while os.path.exists(path):
                path = f"{orig[:-4]} ({cnt}).mp3"
                cnt += 1

            key = f"vk:{d['full_id']}"
            _utils.current_vk_key = key
            _utils.vk_queue = [
                {"key": f"vk:{r['full_id']}", "label": f"{r['artist']} - {r['title']}"}
                for r in rows[i:]  # оставшиеся (ещё не начатые)
            ]

            elapsed = time.time() - start_t
            eta = _fmt_sec((elapsed / i) * (total - i)) if i > 1 else "..."
            progress = i / total * 100
            self._sig.batch.emit(f"[{i}/{total}] ~{eta}")
            self._sig.progress.emit(progress)
            self._sig.status.emit(f"{base[:50]}...")
            self._tray_status("Загрузка...", int(progress))

            ok = False
            if self.driver:
                ok = self._dl_via_browser(d["full_id"], path)
            if not ok and d["url"].startswith("http"):
                ok = self._dl_direct(d["url"], path)

            _utils.queue_titles.pop(key, None)

            if ok:
                ok_count += 1
                _add_vk_history(d["artist"], d["title"], path)
            else:
                fail_count += 1
                failed.append(d)
            time.sleep(0.3)

        _utils.current_vk_key = ""
        _utils.vk_queue = []

        # Повторные попытки
        for attempt in range(2):
            if not failed:
                break
            retry_left = []
            for d in failed:
                base = _safe_name(f"{d['artist']} - {d['title']}") or "track"
                path = os.path.join(folder, base + ".mp3")
                ok = False
                if self.driver:
                    ok = self._dl_via_browser(d["full_id"], path)
                if not ok and d["url"].startswith("http"):
                    ok = self._dl_direct(d["url"], path)
                if ok:
                    ok_count += 1; fail_count -= 1
                    _add_vk_history(d["artist"], d["title"], path)
                else:
                    retry_left.append(d)
            failed = retry_left
            time.sleep(1)

        if failed:
            try:
                with open(os.path.join(folder, "failed_tracks.json"), "w", encoding="utf-8") as f:
                    json.dump(failed, f, ensure_ascii=False, indent=2)
            except Exception:
                pass

        self._batch_mode = False
        self._sig.show_progress.emit(False)
        self._sig.batch.emit("")
        self._tray_status("Ожидание...", -1)
        if fail_count == 0:
            self._sig.status.emit(f"✓ Скачано {ok_count} треков")
        else:
            self._sig.status.emit(f"Скачано {ok_count}, ошибок: {fail_count} (см. failed_tracks.json)")

    # ── Методы скачивания (из vk_search.py) ──────────────────────────────────

    def _dl_via_browser(self, full_id: str, path: str) -> bool:
        try:
            self._sig.status.emit("Получаю ссылку...")
            url = self._get_audio_url(full_id)
            if not url:
                return False
            return self._dl_m3u8(url, path)
        except Exception as e:
            log_message(f"ERROR VK browser dl: {e}")
            return False

    def _get_audio_url(self, full_id: str):
        if not self.driver:
            return None
        try:
            self.driver.execute_cdp_cmd("Network.enable", {})
            self.driver.execute_cdp_cmd("Network.clearBrowserCache", {})

            sel = f'div.audio_row[data-full-id="{full_id}"]'
            try:
                el = self.driver.find_element(By.CSS_SELECTOR, sel)
            except Exception:
                el = None
                for row in self.driver.find_elements(By.CSS_SELECTOR, "div.audio_row"):
                    try:
                        if full_id in (row.get_attribute("data-audio") or ""):
                            el = row; break
                    except Exception:
                        continue
            if not el:
                return None

            try:
                play = el.find_element(By.CSS_SELECTOR, ".audio_play_wrap, .audio_row__play_btn, .audio_row__cover")
                self.driver.execute_script("arguments[0].click();", play)
            except Exception:
                self.driver.execute_script("arguments[0].click();", el)

            audio_url = None
            for _ in range(7):
                time.sleep(0.3)
                audio_url = self.driver.execute_script("""
                    try { if(window.ap&&window.ap._impl){var i=window.ap._impl;
                        if(i._currentAudio&&i._currentAudio.url)return i._currentAudio.url;
                        if(i.currentAudio&&i.currentAudio.url)return i.currentAudio.url;}} catch(e){}
                    try { var a=document.querySelector('audio');
                        if(a&&a.src&&a.src.length>10)return a.src;} catch(e){}
                    return null;
                """)
                if audio_url:
                    break

            try:
                self.driver.execute_script("""
                    try{if(window.ap&&window.ap.pause)window.ap.pause();}catch(e){}
                    try{var a=document.querySelector('audio');if(a)a.pause();}catch(e){}
                """)
            except Exception:
                pass

            if audio_url:
                return audio_url

            # Fallback: performance log
            logs = self.driver.get_log("performance")
            m3u8 = fallback = None
            for entry in reversed(logs):
                try:
                    msg = json.loads(entry["message"])
                    url = msg.get("message", {}).get("params", {}).get("request", {}).get("url", "")
                    if "index.m3u8" in url:
                        m3u8 = url; break
                    if "vkuseraudio" in url and not fallback:
                        fallback = url
                except Exception:
                    continue
            if m3u8:
                return m3u8
            if fallback and "/seg-" in fallback:
                return fallback.rsplit("/seg-", 1)[0] + "/index.m3u8"
            return fallback
        except Exception as e:
            log_message(f"ERROR VK get_audio_url: {e}")
            return None

    def _dl_m3u8(self, url: str, path: str) -> bool:
        is_m3u8 = "index.m3u8" in url or ".m3u8" in url
        if is_m3u8:
            try:
                out = path[:-4] if path.lower().endswith(".mp3") else path
                cmd = ["yt-dlp", "--no-warnings", "--newline",
                       "-o", out + ".%(ext)s", "-x",
                       "--audio-format", "mp3", "--audio-quality", "0", url]
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                )
                for line in proc.stdout:
                    line = line.strip()
                    if "[download]" in line and "%" in line:
                        try:
                            pct = float(line.split("%")[0].split()[-1])
                            self._sig.progress.emit(pct)
                            self._sig.status.emit(f"Скачиваю: {pct:.1f}%")
                        except Exception:
                            pass
                proc.wait()
                return proc.returncode == 0
            except FileNotFoundError:
                return self._dl_m3u8_manual(url, path)
            except Exception as e:
                log_message(f"ERROR VK m3u8: {e}")
                return False
        else:
            return self._dl_direct(url, path)

    def _dl_direct(self, url: str, path: str) -> bool:
        if not REQUESTS_OK:
            return False
        try:
            cookies = {}
            if self.driver:
                for c in self.driver.get_cookies():
                    cookies[c["name"]] = c["value"]
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://vk.com/",
            }
            with _requests.get(url, headers=headers, cookies=cookies, stream=True, timeout=120) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length", 0))
                done = 0
                t0 = time.time()
                with open(path, "wb") as f:
                    for chunk in r.iter_content(8192):
                        if chunk:
                            f.write(chunk)
                            done += len(chunk)
                            if total and time.time() - t0 > 0.2:
                                self._sig.progress.emit(done / total * 100)
            return True
        except Exception as e:
            log_message(f"ERROR VK direct dl: {e}")
            return False

    def _dl_m3u8_manual(self, url: str, path: str) -> bool:
        """Fallback: ручная сборка из .ts сегментов."""
        try:
            cookies = {}
            if self.driver:
                for c in self.driver.get_cookies():
                    cookies[c["name"]] = c["value"]
            headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://vk.com/"}
            r = _requests.get(url, headers=headers, cookies=cookies, timeout=30)
            r.raise_for_status()
            base = url.rsplit("/", 1)[0] + "/"
            segs = [l.strip() for l in r.text.splitlines()
                    if l.strip() and not l.startswith("#")]
            if not segs:
                return False
            ts_path = path + ".ts"
            with open(ts_path, "wb") as out:
                for i, seg in enumerate(segs):
                    seg_url = seg if seg.startswith("http") else base + seg
                    sr = _requests.get(seg_url, headers=headers, cookies=cookies, timeout=30)
                    if sr.status_code == 200:
                        out.write(sr.content)
                    self._sig.progress.emit((i + 1) / len(segs) * 100)
            # Конвертация через ffmpeg
            result = subprocess.run(
                ["ffmpeg", "-y", "-i", ts_path, "-acodec", "libmp3lame", "-q:a", "2", path],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            )
            try:
                os.remove(ts_path)
            except Exception:
                pass
            return result.returncode == 0
        except Exception as e:
            log_message(f"ERROR VK m3u8 manual: {e}")
            return False

    # ── Закрытие ─────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        event.accept()
        self.hide()

    def quit_browser(self):
        """Вызывается при выходе из приложения."""
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None
