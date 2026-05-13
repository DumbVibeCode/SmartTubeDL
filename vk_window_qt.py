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
_VK_TABS_FILE   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vk_search_tabs.json")


# ── Сигналы (thread-safe) ─────────────────────────────────────────────────────

class _Sig(QObject):
    status              = pyqtSignal(str)
    progress            = pyqtSignal(float)   # 0-100
    speed               = pyqtSignal(str)
    batch               = pyqtSignal(str)
    show_progress       = pyqtSignal(bool)
    results_ready       = pyqtSignal(list)
    video_results_ready      = pyqtSignal(list)          # видео: [(title,dur,views,thumb_url,video_url)]
    video_description_ready = pyqtSignal(str, str)      # (заголовок, текст описания)
    thumb_ready              = pyqtSignal(object, bytes) # (QLabel, raw PNG/JPEG bytes)
    playlist_results_ready   = pyqtSignal(list)          # [(title, author, count_str, pl_url)]
    track_unavailable        = pyqtSignal(str)           # full_id недоступного трека
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
    name = "".join(c for c in text if c not in '<>:"/\\|?*').strip().rstrip('. ')
    return name or "track"


# ── Вкладка результатов ───────────────────────────────────────────────────────

class _VKResultTab(QWidget):
    """Одна вкладка с результатами поиска ВК (таблица + фильтр)."""

    def __init__(self, query: str = ""):
        super().__init__()
        self.query   = query
        self.results: list = []

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


# ── Вкладка плейлистов ───────────────────────────────────────────────────────

class _VKPlaylistTab(QWidget):
    """Вкладка со списком плейлистов ВК."""

    def __init__(self, query: str = ""):
        super().__init__()
        self.query = query
        self.results: list = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Название", "Автор", "Треков"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)

        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setHighlightSections(False)

        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(28)

        layout.addWidget(self.table, 1)

        frow = QHBoxLayout()
        frow.setContentsMargins(0, 4, 0, 0)
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("Фильтр по названию, автору...")
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
        self._load_vk_tabs()

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
        h = QLabel("Поиск музыки и видео")
        f = QFont(); f.setPointSize(12); f.setBold(True); h.setFont(f)
        top.addWidget(h)
        top.addStretch()
        self.browser_lbl = QLabel("● Браузер запускается...")
        self.browser_lbl.setStyleSheet("color: orange; font-weight: bold;")
        top.addWidget(self.browser_lbl)

        self.open_browser_btn = QPushButton("Открыть браузер")
        self.open_browser_btn.setProperty("secondary", True)
        self.open_browser_btn.clicked.connect(self._open_browser)
        top.addWidget(self.open_browser_btn)

        self.recheck_btn = QPushButton("Проверить вход")
        self.recheck_btn.setProperty("secondary", True)
        self.recheck_btn.setEnabled(False)
        self.recheck_btn.clicked.connect(self._recheck_login)
        top.addWidget(self.recheck_btn)

        self.yt_cookies_btn = QPushButton("Куки YouTube")
        self.yt_cookies_btn.setProperty("secondary", True)
        self.yt_cookies_btn.setEnabled(False)
        self.yt_cookies_btn.setToolTip("Сохранить куки YouTube из браузера в cookies.txt для загрузки 18+ видео")
        self.yt_cookies_btn.clicked.connect(self._save_youtube_cookies)
        top.addWidget(self.yt_cookies_btn)

        root.addLayout(top)

        # Строка поиска
        row = QHBoxLayout(); row.setSpacing(6)
        self.query_input = QLineEdit()
        self.query_input.setPlaceholderText("Исполнитель / название, ссылка vk.com/..., my.mail.ru/music/...")
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
        self._sig.playlist_results_ready.connect(self._populate_playlist_tab)
        self._sig.track_unavailable.connect(self._mark_track_unavailable)
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
        self.open_browser_btn.setEnabled(True)
        self.recheck_btn.setEnabled(True)
        self.yt_cookies_btn.setEnabled(True)
        if ok:
            self.browser_lbl.setText("● Залогинен в ВК")
            self.browser_lbl.setStyleSheet("color: #4caf50; font-weight: bold;")
            self.search_btn.setEnabled(True)
        else:
            self.browser_lbl.setText("● Войдите в ВК в браузере")
            self.browser_lbl.setStyleSheet("color: #e74c3c; font-weight: bold;")
            self.search_btn.setEnabled(True)  # всё равно даём попробовать

    def _open_browser(self):
        """Открывает браузер, если он закрыт или не отвечает."""
        # Проверяем, жив ли текущий браузер
        alive = False
        if self.driver:
            try:
                _ = self.driver.current_url
                alive = True
            except Exception:
                self.driver = None

        if alive:
            self._sig.status.emit("Браузер уже открыт")
            return

        if not SELENIUM_OK:
            QMessageBox.critical(self, "Ошибка", "Selenium не установлен.")
            return

        self.open_browser_btn.setEnabled(False)
        self.recheck_btn.setEnabled(False)
        self.yt_cookies_btn.setEnabled(False)
        self.search_btn.setEnabled(False)
        self.browser_lbl.setText("● Браузер запускается...")
        self.browser_lbl.setStyleSheet("color: orange; font-weight: bold;")
        threading.Thread(target=self._browser_worker, daemon=True).start()

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

    def _save_youtube_cookies(self):
        if not self.driver:
            QMessageBox.warning(self, "Браузер не готов", "Браузер ещё не запущен.")
            return
        self.yt_cookies_btn.setEnabled(False)
        self._sig.status.emit("Открываю YouTube...")
        threading.Thread(target=self._do_save_youtube_cookies, daemon=True).start()

    def _do_save_youtube_cookies(self):
        try:
            prev_url = self.driver.current_url
            self.driver.get("https://www.youtube.com")
            time.sleep(3)

            cookies = self.driver.get_cookies()
            yt_cookies = [c for c in cookies if 'youtube' in c.get('domain', '') or 'google' in c.get('domain', '')]

            cookies_path = os.path.normpath(
                os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cookies.txt')
            )
            with open(cookies_path, 'w', encoding='utf-8') as f:
                f.write("# Netscape HTTP Cookie File\n")
                for c in yt_cookies:
                    domain   = c.get('domain', '')
                    httponly = '#HttpOnly_' if c.get('httpOnly', False) else ''
                    secure   = 'TRUE' if c.get('secure', False) else 'FALSE'
                    subdomain = 'TRUE' if domain.startswith('.') else 'FALSE'
                    expiry   = str(int(c.get('expiry', 0)))
                    name     = c.get('name', '')
                    value    = c.get('value', '')
                    f.write(f"{httponly}{domain}\t{subdomain}\t{c.get('path','/')}\t{secure}\t{expiry}\t{name}\t{value}\n")

            log_message(f"INFO YouTube cookies сохранены: {len(yt_cookies)} шт. -> {cookies_path}")
            self._sig.status.emit(f"✓ Куки YouTube сохранены ({len(yt_cookies)} шт.)")

            # Возвращаемся на предыдущую страницу
            if prev_url and prev_url != "data:,":
                self.driver.get(prev_url)
        except Exception as e:
            log_message(f"ERROR save_youtube_cookies: {e}")
            self._sig.status.emit(f"Ошибка: {e}")
        finally:
            QMetaObject.invokeMethod(
                self.yt_cookies_btn, "setEnabled",
                Qt.ConnectionType.QueuedConnection,
                Q_ARG(bool, True)
            )

    # ── Вспомогательные методы для вкладок ───────────────────────────────────

    def _t(self):
        w = self.tabs.currentWidget()
        return w.table if isinstance(w, (_VKResultTab, _VKVideoTab, _VKPlaylistTab)) else None

    def _f(self):
        w = self.tabs.currentWidget()
        return w.filter_input if isinstance(w, (_VKResultTab, _VKVideoTab, _VKPlaylistTab)) else None

    def _current_tab(self):
        w = self.tabs.currentWidget()
        return w if isinstance(w, _VKResultTab) else None

    def _current_video_tab(self):
        w = self.tabs.currentWidget()
        return w if isinstance(w, _VKVideoTab) else None

    def _current_playlist_tab(self):
        w = self.tabs.currentWidget()
        return w if isinstance(w, _VKPlaylistTab) else None

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

    def _new_playlist_tab(self, query: str) -> _VKPlaylistTab:
        tab = _VKPlaylistTab(query)
        tab.filter_input.textChanged.connect(self._filter)
        tab.table.doubleClicked.connect(self._download_playlist_selected)
        tab.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        tab.table.customContextMenuRequested.connect(self._show_playlist_ctx_menu)
        title = (query[:22] + "…") if len(query) > 22 else (query or "Плейлисты")
        idx = self.tabs.addTab(tab, f"📋 {title}")
        self.tabs.setCurrentIndex(idx)
        return tab

    def _close_tab(self, index: int):
        self.tabs.removeTab(index)

    # ── Таблица ───────────────────────────────────────────────────────────────

    def _populate_table(self, results: list):
        query = getattr(self, '_pending_vk_query', self.query_input.text().strip())
        tab = self._current_tab() or self._new_tab(query)
        tab.query               = query
        tab.results             = list(results)
        tab.mobile_playlist_url = getattr(self, '_pending_mobile_playlist_url', None)
        tab.playlist_url        = getattr(self, '_pending_playlist_url', None)
        self._pending_mobile_playlist_url = None
        self._pending_playlist_url        = None
        title = (query[:22] + "…") if len(query) > 22 else (query or "Результаты")
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
        w = self.tabs.currentWidget()
        is_video    = isinstance(w, _VKVideoTab)
        is_playlist = isinstance(w, _VKPlaylistTab)
        visible = 0
        for r in range(t.rowCount()):
            if is_video:
                val = (t.item(r, 1).text() if t.item(r, 1) else "").lower()
            elif is_playlist:
                title  = (t.item(r, 0).text() if t.item(r, 0) else "").lower()
                author = (t.item(r, 1).text() if t.item(r, 1) else "").lower()
                val = title + " " + author
            else:
                artist = (t.item(r, 0).text() if t.item(r, 0) else "").lower()
                title  = (t.item(r, 1).text() if t.item(r, 1) else "").lower()
                val = artist + " " + title
            hidden = bool(lo) and lo not in val
            t.setRowHidden(r, hidden)
            if not hidden:
                visible += 1
        total = t.rowCount()
        label = "видео" if is_video else ("плейлистов" if is_playlist else "треков")
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
            self.driver.set_page_load_timeout(4)
            try:
                self.driver.get(url)
            except Exception:
                pass
            finally:
                self.driver.set_page_load_timeout(30)

            result = None
            for _ in range(40):
                time.sleep(0.2)
                result = self.driver.execute_script("""
                    var desc = '';
                    var block = document.querySelector('[data-testid="showmoretext"]');
                    if (block) desc = (block.innerText || block.textContent || '').trim();
                    if (!desc) {
                        var m = document.querySelector('meta[property="og:description"]')
                             || document.querySelector('meta[name="description"]');
                        if (m) desc = (m.content || '').trim();
                    }
                    var title = (document.title || '').trim();
                    return (desc || title) ? {desc: desc, title: title} : null;
                """)
                if result:
                    break
            desc  = (result or {}).get("desc",  "") or "Описание не найдено"
            title = (result or {}).get("title", "") or url

            self._sig.video_description_ready.emit(title, desc)
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

        # ── Mail.ru (не требует браузера) ────────────────────────────────────
        if re.match(r'^https?://my\.mail\.ru/music/', query, re.I):
            threading.Thread(
                target=self._worker_mailru, args=(query,), daemon=True
            ).start()
            return

        # ── Неизвестный Гений (не требует браузера) ───────────────────────
        if re.match(r'^https?://(?:www\.)?neizvestniy-geniy\.ru/users/\d+/works/', query, re.I):
            threading.Thread(
                target=self._worker_neizvestniy, args=(query,), daemon=True
            ).start()
            return

        # ── Всё остальное требует браузера ВК ────────────────────────────────
        if not self.driver:
            QMessageBox.warning(self, "Браузер не готов",
                                "Подождите, пока браузер запустится и войдите в ВК.")
            self.search_btn.setEnabled(True)
            return

        # Определяем тип запроса

        # Плейлисты ВК: страницы со списком плейлистов (section=recoms, playlists и т.п.)
        _q = query.strip()
        if 'vk.com' in _q.lower() and (
            re.search(r'section=(?:recoms|playlists|playlist|owner_playlists)', _q, re.I)
            or re.match(r'^(?:https?://)?(?:www\.)?vk\.com/music(?:/playlists?)?(?:\?|$)', _q, re.I)
        ):
            vurl = _q if _q.startswith('http') else 'https://' + _q
            threading.Thread(
                target=self._worker_playlists, args=(vurl, count), daemon=True
            ).start()
            return

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

    # ── Mail.ru ──────────────────────────────────────────────────────────────

    def _worker_mailru(self, url: str):
        try:
            from mailru_download import _fetch_tracks
            self._sig.status.emit("Загружаю плейлист Mail.ru...")
            tracks, owner = _fetch_tracks(url)
            # Формат _VKResultTab: (artist, title, dur, owner, url, full_id)
            results = [
                (t['artist'], t['title'], t.get('duration', ''),
                 owner, t['url'], f"mailru:{i}")
                for i, t in enumerate(tracks)
            ]
            self._sig.results_ready.emit(results)
        except Exception as e:
            log_message(f"ERROR mailru worker: {e}")
            self._sig.status.emit(f"Ошибка: {e}")
        finally:
            self._sig.search_done.emit()

    # ── Неизвестный Гений ────────────────────────────────────────────────────

    def _worker_neizvestniy(self, url: str):
        try:
            from neizvestniy_download import fetch_works
            tracks, author = fetch_works(url, status_cb=lambda s: self._sig.status.emit(s))
            results = [
                (t['artist'], t['title'], t.get('duration', ''),
                 'НГ', t['url'], f"ng:{i}")
                for i, t in enumerate(tracks)
            ]
            self._sig.results_ready.emit(results)
        except Exception as e:
            log_message(f"ERROR neizvestniy worker: {e}")
            self._sig.status.emit(f"Ошибка: {e}")
        finally:
            self._sig.search_done.emit()

    # ── Плейлисты ────────────────────────────────────────────────────────────

    def _worker_playlists(self, url: str, count: int):
        try:
            self._sig.status.emit("Открываю страницу плейлистов...")
            self.driver.get(url)
            time.sleep(2)

            limit = count if count > 0 else None
            last_h = self.driver.execute_script("return document.body.scrollHeight")
            for _ in range(30):
                parsed = self._parse_playlists_html(self.driver.page_source, limit)
                self._sig.status.emit(f"Загружаю плейлисты... ({len(parsed)})")
                if limit and len(parsed) >= limit:
                    break
                self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(1.5)
                new_h = self.driver.execute_script("return document.body.scrollHeight")
                if new_h == last_h:
                    break
                last_h = new_h

            results = self._parse_playlists_html(self.driver.page_source, limit)
            log_message(f"INFO VK playlists: найдено {len(results)}")
            self._sig.playlist_results_ready.emit(results)
        except Exception as e:
            log_message(f"ERROR VK playlists: {e}")
            self._sig.status.emit(f"Ошибка: {e}")
        finally:
            self._sig.search_done.emit()

    @staticmethod
    def _parse_playlists_html(html: str, max_count) -> list:
        """Возвращает [(title, author, count_str, pl_url)]."""
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.find_all("div", class_=lambda c: c and "audio_pl_item2" in c)
        results = []
        seen = set()
        for card in cards:
            try:
                a = card.find("a", href=lambda h: h and "/music/playlist/" in h)
                if not a:
                    continue
                href = a.get("href", "")
                pl_url = href if href.startswith("http") else "https://vk.com" + href
                base = pl_url.split("?")[0]
                if base in seen:
                    continue
                seen.add(base)

                title_el = card.find(class_=lambda c: c and "audio_item__title" in c)
                title = title_el.get_text(strip=True) if title_el else "Без названия"

                author_el = card.find(class_=lambda c: c and "audio_pl_snippet__artist_link" in c)
                author = author_el.get_text(strip=True) if author_el else ""

                count_el = card.find(class_=lambda c: c and "audio_pl__stats_count" in c)
                count_str = count_el.get_text(strip=True) if count_el else ""

                results.append((title, author, count_str, pl_url))
                if max_count and len(results) >= max_count:
                    break
            except Exception:
                continue
        return results

    def _populate_playlist_tab(self, results: list):
        query = getattr(self, '_pending_vk_query', '')
        tab = self._current_playlist_tab() or self._new_playlist_tab(query)
        tab.query = query
        tab.results = results
        short = (query[:22] + "…") if len(query) > 22 else (query or "Плейлисты")
        self.tabs.setTabText(self.tabs.currentIndex(), f"📋 {short}")

        tab.table.setRowCount(0)
        for title, author, count_str, pl_url in results:
            r = tab.table.rowCount()
            tab.table.insertRow(r)
            title_item = QTableWidgetItem(title)
            title_item.setData(Qt.ItemDataRole.UserRole, pl_url)
            title_item.setToolTip(pl_url)
            tab.table.setItem(r, 0, title_item)
            tab.table.setItem(r, 1, QTableWidgetItem(author))
            tab.table.setItem(r, 2, QTableWidgetItem(count_str))

        total = tab.table.rowCount()
        self._sig.status.emit(f"Найдено плейлистов: {total}" if total else "Плейлисты не найдены")

    def _download_playlist_selected(self):
        tab = self._current_playlist_tab()
        if not tab:
            return
        t = tab.table
        items = t.selectedItems()
        if not items:
            return
        row = items[0].row()
        item = t.item(row, 0)
        if not item:
            return
        pl_url = item.data(Qt.ItemDataRole.UserRole)
        pl_title = item.text()
        if not pl_url or not self.driver:
            return
        threading.Thread(
            target=self._worker_open_playlist, args=(pl_url, pl_title), daemon=True
        ).start()

    def _worker_open_playlist(self, pl_url: str, pl_title: str):
        """Открывает треки плейлиста в новой вкладке результатов."""
        try:
            m_pl = re.search(r'/music/playlist/(-?\d+)_(\d+)(?:_(\w+))?', pl_url)
            if not m_pl:
                self._sig.status.emit("Не удалось разобрать ссылку плейлиста")
                return
            owner_id, playlist_id, access_key = m_pl.group(1), m_pl.group(2), m_pl.group(3) or ''

            # Сразу на мобильную версию — она стабильно показывает audio_item
            mobile_url = f"https://m.vk.com/audio?act=audio_playlist{owner_id}_{playlist_id}"
            if access_key:
                mobile_url += f"&access_hash={access_key}"

            self._sig.status.emit(f"Открываю плейлист: {pl_title}...")
            self.driver.get(mobile_url)
            time.sleep(2)
            for _ in range(20):
                last_h = self.driver.execute_script("return document.body.scrollHeight")
                self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(1.2)
                if self.driver.execute_script("return document.body.scrollHeight") == last_h:
                    break

            html = self.driver.page_source
            soup = BeautifulSoup(html, "html.parser")
            audio_items = soup.find_all("div", class_="audio_item")
            log_message(f"INFO playlist mobile: найдено audio_item: {len(audio_items)}")
            results_tuples = []
            seen = set()
            for item in audio_items:
                try:
                    full_id = (item.get("data-full-id") or item.get("data-id") or "").replace("audio", "")
                    if not full_id or full_id in seen:
                        continue
                    artist  = (item.select_one(".ai_artist") or type("", (), {"get_text": lambda *a, **k: "Неизвестен"})()).get_text(strip=True)
                    ititle  = (item.select_one(".ai_title")  or type("", (), {"get_text": lambda *a, **k: "Без названия"})()).get_text(strip=True)
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
                    seen.add(full_id)
                    results_tuples.append((artist, ititle, duration, "mobile", "", full_id))
                except Exception:
                    continue

            if not results_tuples:
                self._sig.status.emit("Плейлист пуст или недоступен")
                return

            # Показываем в новой вкладке
            self._pending_vk_query = pl_title
            self._pending_mobile_playlist_url = mobile_url  # None если десктоп
            self._pending_playlist_url = pl_url             # оригинальный URL плейлиста
            self._sig.results_ready.emit(results_tuples)
        except Exception as e:
            log_message(f"ERROR VK open playlist: {e}")
            self._sig.status.emit(f"Ошибка: {e}")
        finally:
            self._sig.search_done.emit()

    def _worker_download_playlist(self, pl_url: str, pl_title: str):
        try:
            m_pl = re.search(r'/music/playlist/(-?\d+)_(\d+)(?:_(\w+))?', pl_url)
            if not m_pl:
                self._sig.status.emit("Не удалось разобрать ссылку плейлиста")
                return
            owner_id, playlist_id, access_key = m_pl.group(1), m_pl.group(2), m_pl.group(3) or ''

            # Шаг 1: пробуем старый десктопный URL — он показывает audio_row
            desktop_url = f"https://vk.com/audio?act=audio_playlist{owner_id}_{playlist_id}"
            log_message(f"INFO playlist: пробую desktop URL {desktop_url}")
            self._sig.status.emit(f"Открываю плейлист: {pl_title}...")
            self.driver.get(desktop_url)
            time.sleep(3)

            try:
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.CLASS_NAME, "audio_row"))
                )
                log_message("INFO playlist: desktop audio_row найдены, парсю и качаю")
                results_tuples = self._scroll_and_parse(0)
                rows = [
                    {"artist": r[0], "title": r[1], "duration": r[2],
                     "owner": r[3], "url": r[4], "full_id": r[5]}
                    for r in results_tuples
                ]
            except Exception:
                # Шаг 2: десктоп не дал audio_row — парсим мобильный, качаем через браузер
                log_message("INFO playlist: desktop не дал audio_row, пробую mobile")
                mobile_url = f"https://m.vk.com/audio?act=audio_playlist{owner_id}_{playlist_id}"
                if access_key:
                    mobile_url += f"&access_hash={access_key}"
                self.driver.get(mobile_url)
                time.sleep(3)
                for _ in range(20):
                    last_h = self.driver.execute_script("return document.body.scrollHeight")
                    self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                    time.sleep(1.5)
                    new_h = self.driver.execute_script("return document.body.scrollHeight")
                    if new_h == last_h:
                        break
                html = self.driver.page_source
                soup = BeautifulSoup(html, "html.parser")
                audio_items = soup.find_all("div", class_="audio_item")
                log_message(f"INFO playlist mobile: найдено audio_item: {len(audio_items)}")

                rows = []
                seen = set()
                for item in audio_items:
                    try:
                        full_id = (item.get("data-full-id") or item.get("data-id") or "").replace("audio", "")
                        if not full_id or full_id in seen:
                            continue
                        artist  = (item.select_one(".ai_artist") or type("", (), {"get_text": lambda *a, **k: "Неизвестен"})()).get_text(strip=True)
                        ititle  = (item.select_one(".ai_title")  or type("", (), {"get_text": lambda *a, **k: "Без названия"})()).get_text(strip=True)
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
                        seen.add(full_id)
                        rows.append({"artist": artist, "title": ititle, "duration": duration,
                                     "owner": "mobile", "url": "", "full_id": full_id})
                    except Exception:
                        continue

                if not rows:
                    self._sig.status.emit("Плейлист пуст или недоступен")
                    return

            if not rows:
                self._sig.status.emit("Плейлист пуст или недоступен")
                return

            folder = settings.get("download_folder", "")
            if not folder:
                self._sig.status.emit("Укажите папку загрузок в настройках")
                return

            self._sig.status.emit(f"Скачиваю {len(rows)} треков из «{pl_title}»...")
            self._dl_mobile_batch(rows, folder)
        except Exception as e:
            log_message(f"ERROR VK playlist download: {e}")
            self._sig.status.emit(f"Ошибка: {e}")

    def _show_playlist_ctx_menu(self, pos):
        t = self._t()
        if not t:
            return
        row = t.rowAt(pos.y())
        if row < 0:
            return
        t.selectRow(row)
        menu = QMenu(self)
        menu.addAction("Показать треки",         self._download_playlist_selected)
        menu.addAction("Скачать весь плейлист",  self._dl_playlist_ytdlp_selected)
        menu.addSeparator()
        menu.addAction("Описание",               self._show_playlist_description)
        menu.addAction("Копировать ссылку",      self._copy_playlist_link)
        menu.exec(t.viewport().mapToGlobal(pos))

    def _dl_playlist_ytdlp_selected(self):
        tab = self._current_playlist_tab()
        if not tab:
            return
        items = tab.table.selectedItems()
        if not items:
            return
        item = tab.table.item(items[0].row(), 0)
        if not item:
            return
        pl_url   = item.data(Qt.ItemDataRole.UserRole)
        pl_title = item.text()
        folder   = settings.get("download_folder", "")
        if not folder:
            self._sig.status.emit("Укажите папку загрузок в настройках")
            return
        threading.Thread(
            target=self._worker_dl_playlist_ytdlp, args=(pl_url, pl_title, folder), daemon=True
        ).start()

    def _worker_dl_playlist_ytdlp(self, pl_url: str, pl_title: str, folder: str):
        """Скачивает весь плейлист: парсит мобильную страницу, качает через CDP."""
        try:
            m_pl = re.search(r'/music/playlist/(-?\d+)_(\d+)(?:_(\w+))?', pl_url)
            if not m_pl:
                self._sig.status.emit("Не удалось разобрать ссылку плейлиста")
                return
            owner_id, playlist_id, access_key = m_pl.group(1), m_pl.group(2), m_pl.group(3) or ''
            mobile_url = f"https://m.vk.com/audio?act=audio_playlist{owner_id}_{playlist_id}"
            if access_key:
                mobile_url += f"&access_hash={access_key}"

            self._sig.status.emit(f"Загружаю треки плейлиста «{pl_title}»...")
            self.driver.get(mobile_url)
            time.sleep(3)
            for _ in range(20):
                last_h = self.driver.execute_script("return document.body.scrollHeight")
                self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(1.5)
                new_h = self.driver.execute_script("return document.body.scrollHeight")
                if new_h == last_h:
                    break

            html = self.driver.page_source
            soup = BeautifulSoup(html, "html.parser")
            audio_items = soup.find_all("div", class_="audio_item")
            log_message(f"INFO playlist full dl: найдено audio_item: {len(audio_items)}")

            rows = []
            seen = set()
            for item in audio_items:
                try:
                    full_id = (item.get("data-full-id") or item.get("data-id") or "").replace("audio", "")
                    if not full_id or full_id in seen:
                        continue
                    artist  = (item.select_one(".ai_artist") or type("", (), {"get_text": lambda *a, **k: "Неизвестен"})()).get_text(strip=True)
                    ititle  = (item.select_one(".ai_title")  or type("", (), {"get_text": lambda *a, **k: "Без названия"})()).get_text(strip=True)
                    seen.add(full_id)
                    rows.append({"artist": artist, "title": ititle, "duration": "",
                                 "owner": "mobile", "url": "", "full_id": full_id})
                except Exception:
                    continue

            if not rows:
                self._sig.status.emit("Плейлист пуст или недоступен")
                return

            # Создаём папку заранее
            safe_pl   = _safe_name(pl_title) or "playlist"
            pl_folder = os.path.join(folder, safe_pl)
            os.makedirs(pl_folder, exist_ok=True)

            # Описание: запрашиваем через requests параллельно с загрузкой треков
            desc_result = [""]
            def _fetch_desc_bg():
                try:
                    if not REQUESTS_OK:
                        return
                    cookies = {c['name']: c['value'] for c in self.driver.get_cookies()}
                    r = _requests.get(
                        pl_url, cookies=cookies, timeout=8,
                        headers={
                            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                                          'AppleWebKit/537.36 (KHTML, like Gecko) '
                                          'Chrome/131.0.0.0 Safari/537.36',
                            'Referer': 'https://vk.com/',
                        }
                    )
                    soup = BeautifulSoup(r.text, "html.parser")
                    meta = soup.find("meta", property="og:description")
                    if meta and meta.get("content"):
                        desc_result[0] = meta["content"].strip()
                except Exception as e:
                    log_message(f"WARNING desc_bg: {e}")

            desc_thread = threading.Thread(target=_fetch_desc_bg, daemon=True)
            desc_thread.start()

            # Скачиваем треки (браузер занят мобильной страницей)
            self._dl_cdp_batch(rows, folder, pl_url, mobile_url, pl_title)

            # Ждём описания (обычно уже готово), при неудаче — browser-fallback
            desc_thread.join(timeout=5)
            desc = desc_result[0]
            if not desc:
                desc = self._fetch_playlist_desc_sync(pl_url)

            info_lines = [pl_title]
            if desc:
                info_lines += ["", desc]
            info_lines += ["", f"Треков: {len(rows)}", f"Ссылка: {pl_url}"]
            try:
                with open(os.path.join(pl_folder, "info.txt"), "w", encoding="utf-8") as f:
                    f.write("\n".join(info_lines))
            except Exception as ie:
                log_message(f"WARNING info.txt: {ie}")
        except Exception as e:
            log_message(f"ERROR playlist full dl: {e}")
            self._sig.status.emit(f"Ошибка: {e}")

    def _copy_playlist_link(self):
        tab = self._current_playlist_tab()
        if not tab:
            return
        items = tab.table.selectedItems()
        if not items:
            return
        item = tab.table.item(items[0].row(), 0)
        if item:
            url = item.data(Qt.ItemDataRole.UserRole) or ""
            if url:
                from PyQt6.QtWidgets import QApplication
                QApplication.clipboard().setText(url)
                self._sig.status.emit("Ссылка скопирована")

    def _show_playlist_description(self):
        tab = self._current_playlist_tab()
        if not tab or not self.driver:
            return
        items = tab.table.selectedItems()
        if not items:
            return
        item = tab.table.item(items[0].row(), 0)
        if not item:
            return
        pl_url = item.data(Qt.ItemDataRole.UserRole) or ""
        pl_title = item.text()
        if not pl_url:
            return
        threading.Thread(
            target=self._fetch_playlist_description, args=(pl_url, pl_title), daemon=True
        ).start()

    def _fetch_playlist_desc_sync(self, pl_url: str) -> str:
        """Синхронно получает описание плейлиста (для воркер-потоков)."""
        try:
            self.driver.set_page_load_timeout(3)
            try:
                self.driver.get(pl_url)
            except Exception:
                pass
            finally:
                self.driver.set_page_load_timeout(30)
            for _ in range(30):
                time.sleep(0.2)
                desc = self.driver.execute_script("""
                    var el = document.querySelector('[class*="vkitAudioListHeader__description"]')
                          || document.querySelector('[class*="audio_pl__description"]');
                    if (el) { var t = (el.innerText||el.textContent||'').trim(); if(t) return t; }
                    var m = document.querySelector('meta[property="og:description"]')
                         || document.querySelector('meta[name="description"]');
                    return m ? (m.content||'').trim() : '';
                """) or ""
                if desc:
                    return desc
        except Exception as e:
            log_message(f"WARNING fetch_playlist_desc_sync: {e}")
        return ""

    def _fetch_playlist_description(self, url: str, title: str):
        try:
            self._sig.status.emit("Загружаю описание...")
            # Ставим короткий таймаут — страница начнёт рендериться, описание появится,
            # а мы не ждём загрузки всех ресурсов (как при ручной остановке браузера)
            self.driver.set_page_load_timeout(3)
            try:
                self.driver.get(url)
            except Exception:
                pass  # TimeoutException — нормально, страница уже частично загружена
            finally:
                self.driver.set_page_load_timeout(30)

            desc = ""
            for _ in range(30):   # ещё до 6 сек если описание не сразу
                time.sleep(0.2)
                desc = self.driver.execute_script("""
                    var el = document.querySelector('[class*="vkitAudioListHeader__description"]')
                          || document.querySelector('[class*="audio_pl__description"]');
                    if (el) {
                        var t = (el.innerText || el.textContent || '').trim();
                        if (t) return t;
                    }
                    var m = document.querySelector('meta[property="og:description"]')
                         || document.querySelector('meta[name="description"]');
                    return m ? (m.content || '').trim() : '';
                """) or ""
                if desc:
                    break
            self._sig.video_description_ready.emit(title, desc.strip() or "Описание не найдено")
        except Exception as e:
            self._sig.video_description_ready.emit("Ошибка", str(e))

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

    def _dl_cdp_batch(self, rows: list[dict], folder: str,
                      pl_url: str | None, mobile_url: str | None, pl_title: str = ""):
        """Скачивание треков плейлиста.
        Фаза 1 (последовательно): кликаем треки на мобильной странице, собираем audio URL.
        Фаза 2 (параллельно):     скачиваем все URL одновременно.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        self._batch_mode = True
        total = len(rows)
        self._sig.show_progress.emit(True)

        # Создаём папку с названием плейлиста
        if pl_title:
            safe_pl = _safe_name(pl_title) or "playlist"
            folder = os.path.join(folder, safe_pl)
            os.makedirs(folder, exist_ok=True)

        # ── Фаза 1: сбор audio URL через CDP ─────────────────────────────────
        self._sig.status.emit("Получаю ссылки на треки...")
        nav_url = mobile_url or pl_url
        self.driver.get(nav_url)
        time.sleep(3)

        if mobile_url:
            for _ in range(20):
                last_h = self.driver.execute_script("return document.body.scrollHeight")
                self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(1.5)
                if self.driver.execute_script("return document.body.scrollHeight") == last_h:
                    break

        try:
            self.driver.execute_cdp_cmd("Network.enable", {})
        except Exception:
            pass

        mobile_items = self.driver.find_elements(By.CLASS_NAME, "audio_item") if mobile_url else []
        log_message(f"INFO cdp_batch: mobile items={len(mobile_items)}, tracks={total}")

        # Строим карту full_id → элемент (для правильного клика по выбранным трекам)
        id_to_el = {}
        for el in mobile_items:
            fid = el.get_attribute("data-full-id") or ""
            if not fid:
                did = el.get_attribute("data-id") or ""
                fid = did.replace("audio", "")
            if fid:
                id_to_el[fid] = el

        unavailable = []
        jobs = []   # (index, d, audio_url, path)
        for i, d in enumerate(rows, 1):
            num   = f"{i:02d}"
            base  = _safe_name(f"{d['artist']} - {d['title']}") or f"track_{i}"
            path  = os.path.join(folder, f"{num}. {base}.mp3")

            self._sig.status.emit(f"[{i}/{total}] {base[:50]}...")
            self._sig.progress.emit(i / total * 50)

            audio_url = None
            full_id   = d.get("full_id", "")
            try:
                prev_src = self.driver.execute_script(
                    "var a=document.querySelector('audio'); return a?a.src:'';"
                ) or ''
                self.driver.get_log("performance")

                # Ищем элемент по full_id, иначе по индексу (для полного плейлиста)
                item_el = id_to_el.get(full_id)
                if item_el is None and i - 1 < len(mobile_items):
                    item_el = mobile_items[i - 1]

                if item_el is not None:
                    # Проверяем, доступен ли трек
                    restricted = self.driver.execute_script(
                        "return arguments[0].classList.contains('audio_item__restricted') "
                        "|| !!arguments[0].querySelector('.audio_item__restricted, "
                        "[class*=\"restricted\"], [class*=\"unavailable\"]');",
                        item_el
                    )
                    if restricted:
                        log_message(f"INFO CDP [{i}]: трек недоступен — {base}")
                        unavailable.append(base)
                        if full_id:
                            self._sig.track_unavailable.emit(full_id)
                        jobs.append((i, d, None, path))
                        continue

                    play_btns = item_el.find_elements(
                        By.CSS_SELECTOR, ".ai_play, [class*='play'], button"
                    )
                    target = play_btns[0] if play_btns else item_el
                    self.driver.execute_script("arguments[0].click();", target)
                else:
                    self.driver.execute_script("""
                        var t = arguments[0];
                        var el = Array.from(document.querySelectorAll('*')).find(
                            function(e){ return e.childElementCount===0 && e.textContent.trim()===t; }
                        );
                        if(el) el.click();
                    """, d.get("title", ""))

                for _ in range(40):
                    time.sleep(0.15)
                    src = self.driver.execute_script(
                        "var a=document.querySelector('audio');"
                        "return (a&&a.src&&a.src.startsWith('http'))?a.src:null;"
                    )
                    if src and src != prev_src:
                        audio_url = src; break
                    for entry in self.driver.get_log("performance"):
                        try:
                            u = json.loads(entry["message"]).get("message", {}) \
                                    .get("params", {}).get("request", {}).get("url", "")
                            if "index.m3u8" in u:
                                audio_url = u; break
                            if "vkuseraudio" in u and not audio_url:
                                audio_url = u
                        except Exception:
                            continue
                    if audio_url:
                        break

                try:
                    self.driver.execute_script(
                        "var a=document.querySelector('audio');if(a)a.pause();"
                    )
                except Exception:
                    pass

            except Exception as e:
                log_message(f"WARNING cdp_batch [{i}]: {e}")

            if audio_url:
                log_message(f"INFO CDP [{i}]: {audio_url[:70]}")
            else:
                if base not in unavailable:
                    log_message(f"WARNING CDP [{i}]: URL не получен — '{base}'")
                    unavailable.append(base)
                    if full_id:
                        self._sig.track_unavailable.emit(full_id)

            jobs.append((i, d, audio_url, path))

        # ── Фаза 2: параллельное скачивание ──────────────────────────────────
        self._sig.status.emit("Скачиваю треки...")
        ok_count = fail_count = 0
        done_count = [0]
        lock = threading.Lock()

        def _download(job):
            _, d, url, path = job
            ok = bool(url) and self._dl_m3u8(url, path)
            with lock:
                done_count[0] += 1
                pct = 50 + done_count[0] / total * 50
                self._sig.progress.emit(pct)
                self._sig.status.emit(f"Скачано {done_count[0]}/{total}")
            return d, ok, path

        max_workers = min(4, total)
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(_download, job): job for job in jobs}
            for fut in as_completed(futures):
                d, ok, path = fut.result()
                if ok:
                    ok_count += 1
                    _add_vk_history(d["artist"], d["title"], path)
                else:
                    fail_count += 1

        self._batch_mode = False
        self._sig.show_progress.emit(False)
        self._sig.batch.emit("")
        self._tray_status("Ожидание...", -1)

        n_unavail = len(unavailable)
        if fail_count == 0 and n_unavail == 0:
            self._sig.status.emit(f"✓ Скачано {ok_count} треков → {folder}")
        else:
            parts = [f"Скачано {ok_count}"]
            if fail_count:
                parts.append(f"ошибок: {fail_count}")
            if n_unavail:
                parts.append(f"недоступно: {n_unavail}")
            self._sig.status.emit(", ".join(parts))
            if n_unavail:
                log_message("INFO CDP недоступные треки: " + "; ".join(unavailable))

    def _dl_mobile_batch_from_url(self, rows: list[dict], folder: str, mobile_url: str):
        """Переходит на мобильную страницу плейлиста и скачивает треки."""
        try:
            self._sig.status.emit("Открываю мобильную страницу...")
            self.driver.get(mobile_url)
            time.sleep(3)
            for _ in range(20):
                last_h = self.driver.execute_script("return document.body.scrollHeight")
                self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(1.5)
                new_h = self.driver.execute_script("return document.body.scrollHeight")
                if new_h == last_h:
                    break
        except Exception as e:
            log_message(f"WARNING mobile navigate: {e}")
        self._dl_mobile_batch(rows, folder)

    def _dl_mobile_batch(self, rows: list[dict], folder: str):
        """Скачивание треков с мобильной страницы VK: кликает трек → перехватывает <audio>.src."""
        self._batch_mode = True
        total = len(rows)
        self._sig.show_progress.emit(True)
        ok_count = fail_count = 0
        start_t = time.time()

        for i, d in enumerate(rows, 1):
            base = _safe_name(f"{d['artist']} - {d['title']}") or f"track_{i}"
            path = os.path.join(folder, base + ".mp3")
            cnt, orig = 1, path
            while os.path.exists(path):
                path = f"{orig[:-4]} ({cnt}).mp3"; cnt += 1

            elapsed = time.time() - start_t
            eta = _fmt_sec((elapsed / i) * (total - i)) if i > 1 else "..."
            self._sig.batch.emit(f"[{i}/{total}] ~{eta}")
            self._sig.progress.emit(i / total * 100)
            self._sig.status.emit(f"{base[:50]}...")

            audio_url = None
            full_id = d.get("full_id", "")
            try:
                # Ищем элемент трека на мобильной странице и кликаем
                sel = f'div.audio_item[data-full-id="{full_id}"]'
                els = self.driver.find_elements(By.CSS_SELECTOR, sel)
                if not els:
                    # Fallback: ищем по data-id (mobile format: audioOWNER_ID)
                    sel2 = f'div.audio_item[data-id="audio{full_id}"]'
                    els = self.driver.find_elements(By.CSS_SELECTOR, sel2)
                if els:
                    play_btn = None
                    for cls in ("ai_play", "audio_row__play_btn"):
                        btns = els[0].find_elements(By.CLASS_NAME, cls)
                        if btns:
                            play_btn = btns[0]; break
                    target = play_btn or els[0]
                    self.driver.execute_script("arguments[0].click();", target)
                    # Ждём src у <audio>
                    for _ in range(15):
                        time.sleep(0.4)
                        audio_url = self.driver.execute_script(
                            "var a=document.querySelector('audio');"
                            "return (a&&a.src&&a.src.startsWith('http'))?a.src:null;"
                        )
                        if audio_url:
                            break
                    # Паузим плеер
                    try:
                        self.driver.execute_script(
                            "var a=document.querySelector('audio');if(a)a.pause();"
                        )
                    except Exception:
                        pass
            except Exception as _pe:
                log_message(f"WARNING mobile play {full_id}: {_pe}")

            ok = False
            if audio_url:
                ok = self._dl_m3u8(audio_url, path)
            if not ok and d.get("url", "").startswith("http"):
                ok = self._dl_direct(d["url"], path)

            if ok:
                ok_count += 1
                _add_vk_history(d["artist"], d["title"], path)
            else:
                fail_count += 1
            time.sleep(0.3)

        self._batch_mode = False
        self._sig.show_progress.emit(False)
        self._sig.batch.emit("")
        self._tray_status("Ожидание...", -1)
        if fail_count == 0:
            self._sig.status.emit(f"✓ Скачано {ok_count} треков")
        else:
            self._sig.status.emit(f"Скачано {ok_count}, ошибок: {fail_count}")

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
        if not rows:
            rows = soup.find_all("div", attrs={"data-audio": True})
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
        folder = settings.get("download_folder", "")
        if not folder:
            self._sig.status.emit("Укажите папку загрузок в настройках")
            return
        base = _safe_name(f"{d['artist']} - {d['title']}") or "track"
        path = os.path.join(folder, base + ".mp3")
        cnt, orig = 1, path
        while os.path.exists(path):
            path = f"{orig[:-4]} ({cnt}).mp3"
            cnt += 1
        threading.Thread(
            target=self._dl_single_worker, args=(d, path), daemon=True
        ).start()

    def _download_selected(self):
        if isinstance(self.tabs.currentWidget(), _VKVideoTab):
            self._download_vk_videos_selected()
            return
        if isinstance(self.tabs.currentWidget(), _VKPlaylistTab):
            self._download_playlist_selected()
            return
        rows = self._selected_rows_data()
        if not rows:
            self._sig.status.emit("Не выбрано ни одного трека")
            return
        folder = settings.get("download_folder", "")
        if not folder:
            self._sig.status.emit("Укажите папку загрузок в настройках")
            return
        tab = self._current_tab()
        pl_url     = getattr(tab, 'playlist_url',        None) if tab else None
        mobile_url = getattr(tab, 'mobile_playlist_url', None) if tab else None
        pl_title   = getattr(tab, 'query',               '')   if tab else ''
        if (pl_url or mobile_url) and all(not d.get("url") for d in rows):
            threading.Thread(
                target=self._dl_cdp_batch,
                args=(rows, folder, pl_url, mobile_url, pl_title), daemon=True
            ).start()
        else:
            threading.Thread(
                target=self._dl_batch_worker, args=(rows, folder), daemon=True
            ).start()

    def _dl_single_worker(self, d: dict, path: str):
        label = f"{d['artist']} - {d['title']}"
        is_mailru = d["full_id"].startswith("mailru:")
        is_ng     = d["full_id"].startswith("ng:")
        is_direct = is_mailru or is_ng
        source_lbl = "Mail.ru" if is_mailru else ("НГ" if is_ng else "ВК")
        key = f"vk:{d['full_id']}"
        _utils.queue_titles[key] = f"[{source_lbl}] {label}"
        _utils.current_vk_key = key
        self._sig.show_progress.emit(True)
        self._sig.progress.emit(0)
        self._tray_status("Загрузка...", 0)
        ok = False
        if not is_direct and self.driver:
            ok = self._dl_via_browser(d["full_id"], path)
        if not ok and d["url"].startswith("http"):
            if is_mailru:
                referer = "https://my.mail.ru/"
            elif is_ng:
                referer = "https://www.neizvestniy-geniy.ru/"
            else:
                referer = "https://vk.com/"
            ok = self._dl_direct(d["url"], path, referer=referer)
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

            is_mailru = d["full_id"].startswith("mailru:")
            is_ng     = d["full_id"].startswith("ng:")
            is_direct = is_mailru or is_ng
            ok = False
            if not is_direct and self.driver:
                ok = self._dl_via_browser(d["full_id"], path)
            if not ok and d["url"].startswith("http"):
                if is_mailru:
                    referer = "https://my.mail.ru/"
                elif is_ng:
                    referer = "https://www.neizvestniy-geniy.ru/"
                else:
                    referer = "https://vk.com/"
                ok = self._dl_direct(d["url"], path, referer=referer)

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

    def _dl_via_search(self, d: dict, path: str) -> bool:
        """Ищет трек на VK по исполнителю+названию и скачивает через browser."""
        try:
            q = quote_plus(f"{d['artist']} {d['title']}")
            url = f"https://vk.com/audio?q={q}&section=search"
            self.driver.get(url)
            try:
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.CLASS_NAME, "audio_row"))
                )
            except Exception:
                return False
            full_id = d.get("full_id", "")
            # Сначала ищем точное совпадение по full_id
            if full_id:
                els = self.driver.find_elements(
                    By.CSS_SELECTOR, f'div.audio_row[data-full-id="{full_id}"]'
                )
                if els:
                    return self._dl_via_browser(full_id, path)
            # Берём первый результат
            rows = self._parse_html(self.driver.page_source, 1)
            if rows:
                return self._dl_via_browser(rows[0][5], path)
            return False
        except Exception as e:
            log_message(f"WARNING _dl_via_search: {e}")
            return False

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

    def _dl_direct(self, url: str, path: str, referer: str = "https://vk.com/") -> bool:
        if not REQUESTS_OK:
            return False
        try:
            cookies = {}
            if self.driver and "vk.com" in referer:
                for c in self.driver.get_cookies():
                    cookies[c["name"]] = c["value"]
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": referer,
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

    # ── Сохранение / загрузка вкладок ────────────────────────────────────────

    def _save_vk_tabs(self):
        data = []
        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            entry = {"active": i == self.tabs.currentIndex()}
            if isinstance(tab, _VKResultTab) and tab.results:
                entry.update({"type": "audio",    "query": tab.query,
                               "results": tab.results,
                               "mobile_playlist_url": getattr(tab, "mobile_playlist_url", None),
                               "playlist_url":        getattr(tab, "playlist_url",        None),
                               "current_row": tab.table.currentRow()})
            elif isinstance(tab, _VKVideoTab) and tab.results:
                entry.update({"type": "video",    "query": tab.query, "results": tab.results,
                               "current_row": tab.table.currentRow()})
            elif isinstance(tab, _VKPlaylistTab) and tab.results:
                entry.update({"type": "playlist", "query": tab.query, "results": tab.results,
                               "current_row": tab.table.currentRow()})
            else:
                continue
            data.append(entry)
        try:
            with open(_VK_TABS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log_message(f"WARNING save vk tabs: {e}")

    def _load_vk_tabs(self):
        if not os.path.exists(_VK_TABS_FILE):
            return
        try:
            with open(_VK_TABS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return
        if not data:
            return

        # Удаляем начальную пустую вкладку
        if self.tabs.count() > 0:
            self.tabs.removeTab(0)

        active_idx = 0
        for entry in data:
            t       = entry.get("type")
            query   = entry.get("query", "")
            results = entry.get("results", [])
            if not results:
                continue

            if t == "audio":
                tab = self._new_tab(query)
                tab.results             = results
                tab.mobile_playlist_url = entry.get("mobile_playlist_url")
                tab.playlist_url        = entry.get("playlist_url")
                for row_data in results:
                    if len(row_data) < 6:
                        continue
                    artist, title, duration, owner, url, full_id = row_data[:6]
                    r = tab.table.rowCount(); tab.table.insertRow(r)
                    ai = QTableWidgetItem(artist)
                    ai.setData(Qt.ItemDataRole.UserRole,     url)
                    ai.setData(Qt.ItemDataRole.UserRole + 1, full_id)
                    tab.table.setItem(r, 0, ai)
                    tab.table.setItem(r, 1, QTableWidgetItem(title))
                    tab.table.setItem(r, 2, QTableWidgetItem(duration))
                    tab.table.setItem(r, 3, QTableWidgetItem(owner))
                self._restore_row(tab.table, entry.get("current_row", -1))

            elif t == "video":
                tab = self._new_video_tab(query)
                tab.results = results
                for row_data in results:
                    title, dur, views, thumb, vurl = (list(row_data) + [""] * 5)[:5]
                    r = tab.table.rowCount(); tab.table.insertRow(r)
                    lbl = QLabel()
                    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    lbl.setFixedSize(_VKVideoTab.THUMB_W, _VKVideoTab.THUMB_H)
                    tab.table.setCellWidget(r, 0, lbl)
                    if thumb:
                        threading.Thread(
                            target=self._load_video_thumb, args=(thumb, lbl), daemon=True
                        ).start()
                    ti = QTableWidgetItem(title)
                    ti.setData(Qt.ItemDataRole.UserRole, vurl); ti.setToolTip(vurl)
                    tab.table.setItem(r, 1, ti)
                    tab.table.setItem(r, 2, QTableWidgetItem(dur))
                    tab.table.setItem(r, 3, QTableWidgetItem(views))
                self._restore_row(tab.table, entry.get("current_row", -1))

            elif t == "playlist":
                tab = self._new_playlist_tab(query)
                tab.results = results
                for row_data in results:
                    pl_title, author, count_str, pl_url = (list(row_data) + [""] * 4)[:4]
                    r = tab.table.rowCount(); tab.table.insertRow(r)
                    ti = QTableWidgetItem(pl_title)
                    ti.setData(Qt.ItemDataRole.UserRole, pl_url); ti.setToolTip(pl_url)
                    tab.table.setItem(r, 0, ti)
                    tab.table.setItem(r, 1, QTableWidgetItem(author))
                    tab.table.setItem(r, 2, QTableWidgetItem(count_str))
                self._restore_row(tab.table, entry.get("current_row", -1))

            if entry.get("active"):
                active_idx = self.tabs.count() - 1

        if self.tabs.count() == 0:
            self._new_tab("Поиск")
        else:
            self.tabs.setCurrentIndex(active_idx)

    def _mark_track_unavailable(self, full_id: str):
        """Помечает трек серым во всех вкладках результатов."""
        from PyQt6.QtGui import QColor, QBrush
        gray = QBrush(QColor(150, 150, 150))
        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            if not isinstance(tab, _VKResultTab):
                continue
            t = tab.table
            for r in range(t.rowCount()):
                item = t.item(r, 0)
                if item and item.data(Qt.ItemDataRole.UserRole + 1) == full_id:
                    for c in range(t.columnCount()):
                        cell = t.item(r, c)
                        if cell:
                            cell.setForeground(gray)
                    # Добавляем пометку к исполнителю
                    if item.text() and "⛔" not in item.text():
                        item.setText("⛔ " + item.text())
                    break

    @staticmethod
    def _restore_row(table, row: int):
        if row >= 0 and row < table.rowCount():
            table.setCurrentCell(row, 0)
            table.scrollTo(
                table.model().index(row, 0),
                QAbstractItemView.ScrollHint.PositionAtCenter
            )

    # ── Закрытие ─────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        self._save_vk_tabs()
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
