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
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import quote_plus

import utils as _utils

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QProgressBar, QMenu, QMessageBox, QFileDialog, QSizePolicy, QTabWidget, QComboBox
)
from PyQt6.QtCore import Qt, QObject, pyqtSignal, QMetaObject, Q_ARG
from PyQt6.QtGui import QFont

from logger import log_message
from config import settings


from utils import request_state_save as _save_state_soon, safe_filename


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

# Маркер строки-разделителя в таблице результатов (artist-поле кортежа)
_SEP_MARK = "__SEP__"

# JS для извлечения описания плейлиста: DOM-заголовок → React-fiber → og:description.
# Классы вкитовой вёрстки хешируются и меняются, поэтому берём описание ещё и из
# fiber-объекта плейлиста (owner_id+id+строковое поле description).
_PLAYLIST_DESC_JS = r"""
return (function(){
    // 1) JSON-LD (SSR, стабильно, без хешей и без кнопки «Показать ещё»)
    var scripts=document.querySelectorAll('script[type="application/ld+json"]');
    for(var i=0;i<scripts.length;i++){
        try{
            var data=JSON.parse(scripts[i].textContent||'null');
            var arr=Array.isArray(data)?data:[data];
            for(var j=0;j<arr.length;j++){
                var o=arr[j];
                if(o&&(o['@type']==='MusicPlaylist'||o['@type']==='MusicAlbum')&&o.description)
                    return (''+o.description).trim();
            }
        }catch(e){}
    }
    // 2) DOM-элемент описания страницы плейлиста (новые testid + старые классы)
    var el=document.querySelector('[data-testid="MusicPlaylistPage_Description"] [data-testid="showmoretext-in"]')
         ||document.querySelector('[data-testid="MusicPlaylistPage_Description"]')
         ||document.querySelector('[class*="AudioListHeader__description"]')
         ||document.querySelector('[class*="audio_pl__description"]');
    if(el){var t=(el.innerText||el.textContent||'').trim(); if(t) return t;}
    // 3) Фолбэк: og:description / meta description
    var m=document.querySelector('meta[property="og:description"]')
         ||document.querySelector('meta[name="description"]');
    return m?(m.content||'').trim():'';
})()
"""

# ── ВРЕМЕННО: Яндекс-браузер ─────────────────────────────────────────────────
# TODO: вернуть Chrome — установить _USE_YANDEX = False
_USE_YANDEX = False
# ─────────────────────────────────────────────────────────────────────────────


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
    errors_ready        = pyqtSignal(list)    # [(имя, причина)] нескачанных треков


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
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
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

from vk_parser import VKParserMixin
from vk_downloader import VKDownloadMixin


class VKSearchWindow(QWidget, VKParserMixin, VKDownloadMixin):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.driver = None
        self._yandex_proc = None  # ВРЕМЕННО: процесс Яндекс Браузера
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
        self.count_combo = QComboBox()
        self.count_combo.addItems(["Все", "50", "100", "200", "500"])
        self.count_combo.setCurrentText("Все")
        self.count_combo.setFixedWidth(74)
        row.addWidget(self.count_combo)

        self.search_btn = QPushButton("Найти")
        self.search_btn.setEnabled(False)
        self.search_btn.clicked.connect(self._on_search)
        row.addWidget(self.search_btn)

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
        new_tab_btn.setToolTip("Новая вкладка (Ctrl+T)")
        new_tab_btn.setProperty("secondary", True)
        new_tab_btn.clicked.connect(lambda: self._new_tab("", "Новая вкладка"))
        self.tabs.setCornerWidget(new_tab_btn, Qt.Corner.TopRightCorner)

        self._new_tab("", "Поиск")
        root.addWidget(self.tabs, 1)

        # Единая нижняя панель: слева статус/скорость, справа «⚠ Ошибки» и «⬇ Скачать выбранные»
        bot = QHBoxLayout()
        bot.setSpacing(8)
        self.status_lbl = QLabel("Готово")
        self.status_lbl.setProperty("secondary", True)
        bot.addWidget(self.status_lbl, 1)
        self.speed_lbl = QLabel("")
        self.speed_lbl.setProperty("secondary", True)
        bot.addWidget(self.speed_lbl)
        self.batch_lbl = QLabel("")
        self.batch_lbl.setProperty("secondary", True)
        bot.addWidget(self.batch_lbl)

        # Кнопка показа ошибок последней загрузки (скрыта, пока нет ошибок)
        self.btn_errors = QPushButton("⚠ Посмотреть ошибки")
        self.btn_errors.clicked.connect(self._show_errors_dialog)
        self.btn_errors.setVisible(False)
        self._last_errors = []       # [(имя, причина)] для окна
        self._retry_ctx = None       # контекст для повторного скачивания
        self._error_tab = None       # вкладка, на которой были ошибки
        self._dl_tab = None          # вкладка, с которой запущена загрузка
        bot.addWidget(self.btn_errors)

        self.dl_btn = QPushButton("⬇ Скачать выбранные")
        self.dl_btn.clicked.connect(self._download_selected)
        bot.addWidget(self.dl_btn)

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
        self._sig.errors_ready.connect(self._on_errors_ready)
        self.tabs.currentChanged.connect(self._on_tab_changed)

    def _on_tab_changed(self, idx: int):
        tab = self.tabs.widget(idx)
        if tab and hasattr(tab, 'query'):
            self.query_input.blockSignals(True)
            self.query_input.setText(tab.query)
            self.query_input.blockSignals(False)
        self._update_errors_btn()   # кнопка ошибок — только на «своей» вкладке

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

    def _new_tab(self, query: str = "", label: str = "") -> _VKResultTab:
        tab = _VKResultTab(query)
        tab.filter_input.textChanged.connect(self._filter)
        tab.table.doubleClicked.connect(self._download_selected)
        tab.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        tab.table.customContextMenuRequested.connect(self._show_ctx_menu)
        tab.table.horizontalHeader().sectionClicked.connect(self._sort_col)
        title = label or ((query[:22] + "…") if len(query) > 22 else query) or "Поиск"
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
        _save_state_soon()

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
        tab_title = (query[:22] + "…") if len(query) > 22 else (query or "Результаты")
        tab_idx = self.tabs.indexOf(tab)
        if tab_idx >= 0:
            self.tabs.setTabText(tab_idx, tab_title)
        tab.table.setRowCount(0)
        tab.filter_input.blockSignals(True)
        tab.filter_input.clear()
        tab.filter_input.blockSignals(False)

        for row_data in results:
            self._add_audio_row(tab.table, row_data)

        total = sum(
            1 for r in range(tab.table.rowCount())
            if tab.table.item(r, 0)
            and tab.table.item(r, 0).data(Qt.ItemDataRole.UserRole + 1)
        )
        self._sig.status.emit(f"Найдено треков: {total}" if total else "Ничего не найдено")
        _save_state_soon()

    def _add_audio_row(self, table, row_data):
        """Вставляет строку трека или строку-разделитель ('__SEP__')."""
        if len(row_data) < 6:
            return
        artist, title, duration, owner, url, full_id = row_data[:6]
        r = table.rowCount()
        table.insertRow(r)

        if artist == _SEP_MARK:
            sep = QTableWidgetItem(title or "Треки из комментариев")
            # Только отображается, не выбирается/не редактируется
            sep.setFlags(Qt.ItemFlag.ItemIsEnabled)
            f = sep.font(); f.setBold(True); sep.setFont(f)
            sep.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItem(r, 0, sep)
            table.setSpan(r, 0, 1, table.columnCount())
            return

        artist_item = QTableWidgetItem(artist)
        artist_item.setData(Qt.ItemDataRole.UserRole,     url)
        artist_item.setData(Qt.ItemDataRole.UserRole + 1, full_id)
        table.setItem(r, 0, artist_item)
        table.setItem(r, 1, QTableWidgetItem(title))
        table.setItem(r, 2, QTableWidgetItem(duration))
        table.setItem(r, 3, QTableWidgetItem(owner))

    def _row_data(self, row: int):
        t = self._t()
        item = t.item(row, 0) if t else None
        if not item:
            return None
        # Строка-разделитель («Треки из комментариев») — не трек
        if not (item.data(Qt.ItemDataRole.UserRole + 1) or ""):
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
                    d["row_num"] = r + 1
                    result.append(d)
        result.sort(key=lambda x: x["row_num"])
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

        # После пересортировки заново применяем активный фильтр:
        # setRowHidden привязан к номеру строки, а содержимое переехало,
        # поэтому без этого скрытыми остаются не те строки.
        fi = self._f()
        if fi is not None:
            if fi.text():
                self._filter(fi.text())
            else:
                for r in range(t.rowCount()):
                    t.setRowHidden(r, False)

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

    @staticmethod
    def _find_yandex_exe() -> str | None:
        """ВРЕМЕННО: находит browser.exe Яндекс Браузера на этой машине."""
        candidates = [
            os.path.expandvars(r"%LOCALAPPDATA%\Yandex\YandexBrowser\Application\browser.exe"),
            os.path.expandvars(r"%PROGRAMFILES%\Yandex\YandexBrowser\Application\browser.exe"),
            os.path.expandvars(r"%PROGRAMFILES(X86)%\Yandex\YandexBrowser\Application\browser.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Yandex\Application\browser.exe"),
        ]
        for p in candidates:
            if os.path.isfile(p):
                log_message(f"INFO Яндекс: нашёл браузер: {p}")
                return p

        # Ищем в реестре
        try:
            import winreg
            for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                for sub in (
                    r"SOFTWARE\Clients\StartMenuInternet\Yandex\shell\open\command",
                    r"SOFTWARE\Clients\StartMenuInternet\YandexBrowser\shell\open\command",
                ):
                    try:
                        with winreg.OpenKey(root, sub) as k:
                            val = winreg.QueryValue(k, "")
                            exe = val.strip().strip('"').split('"')[0]
                            if os.path.isfile(exe):
                                log_message(f"INFO Яндекс: нашёл браузер в реестре: {exe}")
                                return exe
                    except OSError:
                        pass
        except Exception:
            pass

        # Ищем через where/which
        try:
            r = subprocess.run(
                ["powershell", "-Command",
                 "(Get-ItemProperty 'HKCU:\\Software\\Yandex\\YandexBrowser\\BLBeacon').version"],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            # Это даёт версию, не путь — но говорит что браузер есть; ищем глубже
        except Exception:
            pass

        return None

    @staticmethod
    def _find_yandex_service(yandex_app: str, yandex_exe: str):
        """ВРЕМЕННО: ищет/скачивает chromedriver, совместимый с Яндекс Браузером."""
        # 1. chromedriver.exe в версионных подпапках Яндекса
        if os.path.isdir(yandex_app):
            for _d in sorted(os.listdir(yandex_app), reverse=True):
                _cd = os.path.join(yandex_app, _d, "chromedriver.exe")
                if os.path.isfile(_cd):
                    log_message(f"INFO Яндекс: нашёл chromedriver: {_cd}")
                    return Service(_cd)

        # 2. Читаем Chromium-версию из реестра (BLBeacon → version, напр. 146.0.7680.791)
        chromium_ver = None
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Yandex\YandexBrowser\BLBeacon") as k:
                val, _ = winreg.QueryValueEx(k, "version")
                # Обрезаем Яндекс-суффикс: "146.0.7680.791" → "146.0.7680"
                parts = str(val).split(".")
                chromium_ver = ".".join(parts[:3]) if len(parts) >= 3 else str(val)
                log_message(f"INFO Яндекс: Chromium из реестра = {chromium_ver}")
        except Exception:
            pass

        # 3. Скачиваем chromedriver через webdriver_manager
        try:
            if chromium_ver:
                path = ChromeDriverManager(driver_version=chromium_ver).install()
            else:
                try:
                    from webdriver_manager.core.os_manager import ChromeType
                except ImportError:
                    from webdriver_manager.utils import ChromeType  # type: ignore
                path = ChromeDriverManager(chrome_type=ChromeType.CHROMIUM).install()
            log_message(f"INFO Яндекс: chromedriver скачан: {path}")
            return Service(path)
        except Exception as e:
            log_message(f"WARNING Яндекс: webdriver_manager не смог скачать driver: {e}")

        return None

    @staticmethod
    def _import_chrome_vk_cookies(driver) -> int:
        """ВРЕМЕННО: копирует VK cookies из Chrome в текущую сессию Яндекса."""
        import sqlite3, shutil, json, base64
        chrome_cookies = os.path.expandvars(
            r"%LOCALAPPDATA%\Google\Chrome\User Data\Default\Cookies"
        )
        local_state_file = os.path.expandvars(
            r"%LOCALAPPDATA%\Google\Chrome\User Data\Local State"
        )
        if not os.path.isfile(chrome_cookies):
            log_message("INFO Яндекс куки: файл Cookies Chrome не найден")
            return 0

        # Ключ шифрования из Local State (DPAPI)
        key = None
        try:
            with open(local_state_file, 'r', encoding='utf-8') as f:
                ls = json.load(f)
            enc_key = base64.b64decode(ls['os_crypt']['encrypted_key'])[5:]
            from win32crypt import CryptUnprotectData
            key = CryptUnprotectData(enc_key, None, None, None, 0)[1]
        except Exception as e:
            log_message(f"WARNING Яндекс куки: не удалось получить ключ Chrome: {e}")
            return 0

        tmp = os.path.join(tempfile.gettempdir(), "_vk_chrome_cookies_tmp.db")
        try:
            shutil.copy2(chrome_cookies, tmp)
        except Exception as e:
            log_message(f"WARNING Яндекс куки: не удалось скопировать файл: {e}")
            return 0

        injected = 0
        try:
            from Crypto.Cipher import AES
            conn = sqlite3.connect(tmp)
            rows = conn.execute(
                "SELECT host_key, name, path, encrypted_value, expires_utc, is_secure, is_httponly "
                "FROM cookies WHERE host_key LIKE '%vk.com%'"
            ).fetchall()
            conn.close()

            for host_key, name, path, enc_val, expires_utc, secure, httponly in rows:
                try:
                    if enc_val[:3] == b'v10':
                        nonce, ctxt, tag = enc_val[3:15], enc_val[15:-16], enc_val[-16:]
                        value = AES.new(key, AES.MODE_GCM, nonce).decrypt_and_verify(ctxt, tag).decode()
                    else:
                        from win32crypt import CryptUnprotectData
                        value = CryptUnprotectData(enc_val, None, None, None, 0)[1].decode()
                    cookie = {
                        'name': name, 'value': value,
                        'domain': host_key.lstrip('.'), 'path': path,
                        'secure': bool(secure), 'httpOnly': bool(httponly),
                    }
                    if expires_utc > 0:
                        cookie['expiry'] = int((expires_utc - 11644473600000000) / 1000000)
                    driver.add_cookie(cookie)
                    injected += 1
                except Exception:
                    continue
        except ImportError:
            log_message("WARNING Яндекс куки: нужен pycryptodome: pip install pycryptodome")
        finally:
            try:
                os.remove(tmp)
            except Exception:
                pass

        log_message(f"INFO Яндекс куки: скопировано {injected} куки из Chrome")
        return injected

    @staticmethod
    def _kill_profile_chrome(profile_dir):
        from browser_helper import kill_profile_chrome
        kill_profile_chrome(profile_dir, label="VK")

    @staticmethod
    def _clean_chrome_crash_markers(profile_dir):
        from browser_helper import clean_chrome_crash_markers
        clean_chrome_crash_markers(profile_dir, label="VK")

    def _browser_worker(self):
        try:
            log_message("INFO VK: запуск браузера")

            # ВРЕМЕННО: Яндекс-браузер ───────────────────────────────────────
            if _USE_YANDEX:
                yandex_exe = self._find_yandex_exe()
                if not yandex_exe:
                    raise RuntimeError(
                        "Яндекс Браузер не найден на этом компьютере.\n"
                        "Убедитесь, что он установлен."
                    )
                profile_dir = os.path.join(os.getcwd(), ".vk_yandex_profile")
                os.makedirs(profile_dir, exist_ok=True)

                # Убиваем зависшие процессы браузера, держащие наш профиль
                self._kill_profile_chrome(profile_dir)

                # Запускаем браузер сами — только с debug-портом, без chromedriver-флагов
                debug_port = 9222
                self._yandex_proc = subprocess.Popen([
                    yandex_exe,
                    f"--remote-debugging-port={debug_port}",
                    f"--user-data-dir={profile_dir}",
                    "--no-first-run", "--start-maximized",
                ])
                time.sleep(4)  # ждём запуска DevTools

                # Подключаемся к уже запущенному браузеру (версии chromedriver не важны)
                opts = webdriver.ChromeOptions()
                opts.add_experimental_option("debuggerAddress", f"127.0.0.1:{debug_port}")
                opts.set_capability("goog:loggingPrefs", {"performance": "ALL"})
                try:
                    svc = Service(ChromeDriverManager().install())
                    self.driver = webdriver.Chrome(service=svc, options=opts)
                except Exception:
                    self.driver = webdriver.Chrome(options=opts)
            # ─────────────────────────────────────────────────────────────────
            else:
                profile_dir = os.path.join(os.getcwd(), ".vk_chrome_profile")
                os.makedirs(profile_dir, exist_ok=True)

                def _make_opts():
                    o = webdriver.ChromeOptions()
                    o.add_argument(f"--user-data-dir={profile_dir}")
                    o.add_argument("--start-maximized")
                    o.add_argument("--disable-blink-features=AutomationControlled")
                    # Подавляем диалог восстановления после аварийного выхода
                    o.add_argument("--disable-session-crashed-bubble")
                    o.add_argument("--restore-last-session=false")
                    o.add_argument("--no-first-run")
                    o.add_argument("--no-default-browser-check")
                    o.add_argument(
                        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                    )
                    o.set_capability("goog:loggingPrefs", {"performance": "ALL"})
                    return o

                def _launch():
                    try:
                        svc = Service(ChromeDriverManager().install())
                        return webdriver.Chrome(service=svc, options=_make_opts())
                    except Exception:
                        return webdriver.Chrome(options=_make_opts())

                # Сначала убиваем зависшие Chrome нашего профиля (иначе он занят),
                # затем чистим следы прошлого аварийного завершения
                self._kill_profile_chrome(profile_dir)
                self._clean_chrome_crash_markers(profile_dir)
                try:
                    self.driver = _launch()
                except Exception as e:
                    # Типичный краш после зависания компа: чистим профиль и пробуем ещё раз
                    log_message(f"WARNING VK: первый запуск не удался ({e}); чищу профиль и повторяю")
                    self._clean_chrome_crash_markers(profile_dir)
                    time.sleep(1)
                    self.driver = _launch()

            self.driver.get("https://vk.com")
            log_message("INFO VK: браузер открыт, жду логина...")
            # ВРЕМЕННО: подкладываем куки из Chrome, чтобы не логиниться заново
            if _USE_YANDEX:
                n = self._import_chrome_vk_cookies(self.driver)
                if n > 0:
                    self.driver.get("https://vk.com")  # перезагружаем с куками
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
            # Ещё не на vk.com / vk.ru
            if "vk.com" not in url and "vk.ru" not in url:
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
            raw_cnt = self.count_combo.currentText().strip()
            count = 0 if raw_cnt.lower() == "все" else max(0, min(500, int(raw_cnt)))
        except ValueError:
            count = 0

        self._pending_vk_query = query
        self.search_btn.setEnabled(False)
        self._sig.status.emit("Поиск...")

        # Сразу очищаем текущую вкладку
        tab = self._current_tab()
        if tab:
            tab.table.setRowCount(0)
            tab.results = []
            tab.filter_input.blockSignals(True)
            tab.filter_input.clear()
            tab.filter_input.blockSignals(False)

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

        # Плейлист, открытый из поста/стены или прямой ссылкой на аудио:
        #   ...&z=audio_playlist-17232727_71312367_93a9b96bd2c605381f
        #   ...?act=audio_playlist-17232727_71312367
        m_zpl = re.search(
            r'(?:z|act)=audio_playlist(-?\d+)_(\d+)(?:_(\w+))?', query, re.I
        )
        if m_zpl:
            owner_id, pl_id, key = m_zpl.group(1), m_zpl.group(2), m_zpl.group(3) or ''
            pl_url = f"https://vk.com/music/playlist/{owner_id}_{pl_id}"
            if key:
                pl_url += f"_{key}"
            threading.Thread(
                target=self._worker_open_playlist, args=(pl_url, "Плейлист"), daemon=True
            ).start()
            return

        # Прямая ссылка на конкретный плейлист:
        #   vk.com|vk.ru/music/playlist/<owner>_<id>[_<access_key>]
        if re.match(
            r'^(?:https?://)?(?:www\.)?vk\.(?:com|ru)/music/playlist/-?\d+_\d+',
            query, re.I
        ):
            pl_url = query if query.startswith('http') else 'https://' + query
            threading.Thread(
                target=self._worker_open_playlist, args=(pl_url, "Плейлист"), daemon=True
            ).start()
            return

        # Плейлисты ВК: страницы со списком плейлистов (section=recoms, playlists и т.п.)
        _q = query.strip()
        if ('vk.com' in _q.lower() or 'vk.ru' in _q.lower()) and (
            re.search(r'section=(?:recoms|playlists|playlist|owner_playlists)', _q, re.I)
            or re.match(r'^(?:https?://)?(?:www\.)?vk\.(?:com|ru)/music(?:/playlists?|/catalog/[A-Za-z0-9_-]+)?(?:\?|$)', _q, re.I)
        ):
            vurl = _q if _q.startswith('http') else 'https://' + _q
            threading.Thread(
                target=self._worker_playlists, args=(vurl, count), daemon=True
            ).start()
            return

        # Видео: vk.com/video/@id... или vkvideo.ru/@...
        m_video = re.match(
            r'^(?:https?://)?(?:www\.)?(?:vk\.(?:com|ru)/video|vkvideo\.ru)([/?@].*)?$',
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
            r'^(?:https?://)?(?:www\.)?vk\.(?:com|ru)/audios(-?\d+)(?:\?.*)?$',
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
        m = re.match(r'^(?:https?://)?(?:www\.)?vk\.(?:com|ru)/wall(-?\d+)_(\d+)(?:\?.*)?$',
                     text.strip(), re.I)
        return (m.group(1), m.group(2)) if m else None

    @staticmethod
    def _parse_profile_url(text: str):
        m = re.match(r'^(?:https?://)?(?:www\.)?vk\.(?:com|ru)/([a-zA-Z0-9._]+)(?:\?.*)?$',
                     text.strip())
        if m:
            pid = m.group(1)
            excluded = {'audio','audios','music','feed','im','friends',
                        'groups','photos','video','docs','settings','login'}
            if pid.lower() not in excluded:
                return pid
        return None

    # ── Воркеры поиска ───────────────────────────────────────────────────────

    def _rows_present(self):
        """True, если на странице есть треки (старый audio_row, data-testid или ссылки /audio<owner>_<id>)."""
        try:
            return self.driver.execute_script("""
                if (document.querySelectorAll('div.audio_row, [data-testid="MusicTrackRow"]').length > 0) {
                    return true;
                }
                var re = /^\\/audio-?\\d+_\\d+/;
                var links = document.querySelectorAll('a[href*="/audio"]');
                var cnt = 0;
                for (var i = 0; i < links.length; i++) {
                    var href = links[i].getAttribute('href') || '';
                    if (!re.test(href)) continue;
                    // Игнорируем верхнюю панель плеера (там обычно 1 ссылка)
                    var p = links[i].parentElement;
                    var isTopPlayer = false;
                    for (var u = 0; u < 5 && p; u++) {
                        var t = p.textContent || '';
                        if (t.indexOf('Показать текст') !== -1 || t.indexOf('Микс по треку') !== -1) {
                            isTopPlayer = true; break;
                        }
                        p = p.parentElement;
                    }
                    if (!isTopPlayer && ++cnt >= 1) return true;
                }
                return false;
            """)
        except Exception:
            return False

    def _worker_search_navigate(self, query: str):
        """Открывает страницу поиска и переходит в каталог «Показать все»."""
        q = quote_plus(query)
        self.driver.get(f"https://vk.com/audio?q={q}&section=search")
        try:
            WebDriverWait(self.driver, 10).until(lambda d: self._rows_present())
        except Exception:
            pass

        # Ищем href ссылки «Показать все» ТОЛЬКО для секции треков («Все треки» / «Треки»).
        # Не полагаемся на data-testid, т.к. ВК убрал data-testid из продакшн-сборки.
        try:
            show_all_url = self.driver.execute_script("""
                var texts = ['показать все', 'показать всё', 'все треки', 'show all'];
                function isShowAll(a) {
                    var t = (a.textContent || '').trim().toLowerCase();
                    for (var k = 0; k < texts.length; k++) {
                        if (t === texts[k] || t.startsWith(texts[k])) return true;
                    }
                    return false;
                }

                // 1. Приоритет: ищем ссылку «Показать все» (/music/catalog/...), рядом с которой
                // заголовок секции содержит «все треки» / «треки» / «аудиозаписи» (и НЕ плейлисты/альбомы/текст).
                var badWords = ['плейлист', 'альбом', 'музыкант', 'исполнител', 'видео', 'в тексте', 'куратор', 'подкаст'];
                var goodWords = ['все треки', 'треки', 'аудиозапис', 'tracks', 'audio'];
                var allLinks = Array.from(document.querySelectorAll('a[href]')).filter(isShowAll);
                for (var i = 0; i < allLinks.length; i++) {
                    var a = allLinks[i];
                    var p = a.parentElement;
                    for (var up = 0; up < 4 && p; up++) {
                        var ctx = (p.innerText || p.textContent || '').trim().toLowerCase().replace(/\\s+/g, ' ');
                        if (ctx.length > 12 && ctx.length < 160) {
                            var isBad = badWords.some(function(w){ return ctx.indexOf(w) !== -1; });
                            var isGood = goodWords.some(function(w){ return ctx.indexOf(w) !== -1; });
                            if (isGood && !isBad) return a.href;
                            break;
                        }
                        p = p.parentElement;
                    }
                }

                // 2. Фолбэк: обход вверх от первого трека (если заголовок секции нестандартный)
                var audioRe = /^\\/audio-?\\d+_\\d+/;
                var trackRow = document.querySelector('[data-testid="MusicTrackRow"], div.audio_row');
                if (!trackRow) {
                    var al = document.querySelectorAll('a[href*="/audio"]');
                    for (var j = 0; j < al.length; j++) {
                        if (!audioRe.test(al[j].getAttribute('href') || '')) continue;
                        var pr = al[j].parentElement;
                        var topBar = false;
                        for (var u = 0; u < 5 && pr; u++) {
                            var tx = pr.textContent || '';
                            if (tx.indexOf('Показать текст') !== -1 || tx.indexOf('Микс по треку') !== -1) {
                                topBar = true; break;
                            }
                            pr = pr.parentElement;
                        }
                        if (!topBar) { trackRow = al[j]; break; }
                    }
                }
                if (!trackRow) return null;

                var node = trackRow.parentElement;
                for (var up2 = 0; up2 < 10 && node; up2++) {
                    var links = node.querySelectorAll('a[href]');
                    for (var m = 0; m < links.length; m++) {
                        if (isShowAll(links[m])) {
                            var ctx2 = ((links[m].parentElement && links[m].parentElement.innerText) || '').toLowerCase();
                            var bad2 = badWords.some(function(w){ return ctx2.indexOf(w) !== -1; });
                            if (!bad2) return links[m].href;
                        }
                    }
                    node = node.parentElement;
                }
                return null;
            """)
            log_message(f"INFO VK search: show_all_url = {show_all_url!r}")
            if show_all_url:
                self._sig.status.emit("Открываю все треки...")
                self.driver.get(show_all_url)
                try:
                    WebDriverWait(self.driver, 8).until(lambda d: self._rows_present())
                except Exception:
                    pass
                # Защита: если «Показать все» увёл не на треки (напр. в каталог
                # плейлистов), возвращаемся к странице поиска и берём её треки.
                if not self._rows_present():
                    log_message("INFO VK search: на странице нет треков (вероятно плейлисты) — возврат к поиску")
                    self.driver.get(f"https://vk.com/audio?q={q}&section=search")
                    try:
                        WebDriverWait(self.driver, 8).until(lambda d: self._rows_present())
                    except Exception:
                        pass
        except Exception:
            pass

    def _worker_search(self, query: str, count: int):
        try:
            self._worker_search_navigate(query)
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
                WebDriverWait(self.driver, 10).until(lambda d: self._rows_present())
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
                WebDriverWait(self.driver, 10).until(lambda d: self._rows_present())
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
            desk_url = f"https://vk.com/wall{owner_id}_{post_id}"
            self.driver.get(desk_url)
            time.sleep(3)

            html = self.driver.page_source
            cap = count if count > 0 else None

            # Аудио из самого поста (apiPrefetchCache в <script>)
            post_audio = self._parse_wall_scripts(html, None)
            log_message(f"INFO wall scripts: найдено {len(post_audio)} треков в посте")

            # Раскрываем ВСЕ комментарии и ветки ответов, добираем аудио из них.
            self._sig.status.emit("Загружаю комментарии...")
            self._expand_wall_comments()
            # Основной источник — React-fiber отрисованных плееров; фолбэк — старый audio_row.
            dom_audio = self._extract_dom_audio()
            log_message(f"INFO wall dom fiber: {len(dom_audio)} аудио в DOM")
            dom_audio += self._parse_html(self.driver.page_source, None)

            # Комментарии = всё из DOM, чего нет в самом посте (по full_id)
            post_ids = {r[5] for r in post_audio}
            comment_audio, seen = [], set(post_ids)
            for r in dom_audio:
                fid = r[5]
                if fid in seen:
                    continue
                seen.add(fid)
                comment_audio.append(r)
            log_message(f"INFO wall comments: найдено {len(comment_audio)} треков в комментариях")

            # Диагностика: если в комментах пусто — сохраняем DOM для анализа разметки
            if not comment_audio:
                try:
                    with open("debug_wall_comments.html", "w", encoding="utf-8") as _f:
                        _f.write(self.driver.page_source)
                    log_message("INFO wall: DOM сохранён в debug_wall_comments.html")
                except Exception:
                    pass

            # Фолбэк: если совсем пусто — старая логика прокрутки
            if not post_audio and not comment_audio:
                post_audio = self._scroll_and_parse(count)
                log_message(f"INFO wall audio_row: найдено {len(post_audio)} треков")

            # Собираем итог: треки поста, затем разделитель и треки из комментариев.
            # Комментарии по умолчанию показываем ВСЕ (не режем по count).
            audio_results = list(post_audio)
            if comment_audio:
                audio_results.append(
                    (_SEP_MARK, "Треки из комментариев", "", "", "", "")
                )
                audio_results.extend(comment_audio)
            log_message(
                f"INFO wall audio total: {len(post_audio)} пост + "
                f"{len(comment_audio)} комментарии"
            )

            # Видео (только из самого поста)
            video_results = self._parse_wall_video_scripts(html, cap)
            log_message(f"INFO wall video scripts: найдено {len(video_results)} видео")
            if not video_results:
                video_results = self._parse_video_html(html, cap)
                log_message(f"INFO wall video html: найдено {len(video_results)} видео")

            if audio_results:
                self._sig.results_ready.emit(audio_results)
            if video_results:
                self._sig.video_results_ready.emit(video_results)
            if not audio_results and not video_results:
                self._sig.status.emit("В посте не найдено аудио или видео")
        except Exception as e:
            log_message(f"ERROR VK wall: {e}")
            self._sig.status.emit(f"Ошибка: {e}")
        finally:
            self._sig.search_done.emit()

    def _expand_wall_comments(self, max_rounds: int = 40):
        """Раскрывает все комментарии и ветки ответов под постом,
        чтобы в DOM появились аудио из комментариев."""
        js = r"""
return (function(){
    var clicked=0;
    var sels=['.replies_next','.reply_show_next','.wl_replies_next',
              '._replies_next_link','.replies_next_wrap','.show_more_replies',
              '[class*="repliesNext"]','[class*="showNextComments"]',
              '[class*="ShowMoreComments"]'];
    sels.forEach(function(sel){
        document.querySelectorAll(sel).forEach(function(el){
            if(el.offsetParent){ try{el.click(); clicked++;}catch(e){} }
        });
    });
    if(clicked===0){
        var all=document.querySelectorAll('a,button,span,div');
        for(var i=0;i<all.length;i++){
            var el=all[i];
            if(!el.offsetParent||el.children.length>2) continue;
            var t=(el.textContent||'').trim().toLowerCase();
            if(!t||t.length>60) continue;
            if(t.indexOf('показать след')>=0||t.indexOf('показать пред')>=0||
               (t.indexOf('показать')>=0&&t.indexOf('коммент')>=0)||
               (t.indexOf('показать')>=0&&t.indexOf('ответ')>=0)){
                try{el.click(); clicked++;}catch(e){}
            }
        }
    }
    return clicked;
})()
"""
        idle = 0
        for _ in range(max_rounds):
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1.0)
            try:
                clicked = self.driver.execute_script(js) or 0
            except Exception:
                clicked = 0
            try:
                n = self.driver.execute_script(
                    "return document.querySelectorAll('div.audio_row').length")
            except Exception:
                n = 0
            self._sig.status.emit(f"Загружаю комментарии... (аудио: {n})")
            if clicked == 0:
                idle += 1
                if idle >= 3:
                    break
            else:
                idle = 0
            time.sleep(0.8)

    @staticmethod
    def _parse_wall_scripts(html: str, max_count) -> list:
        """Извлекает аудио из JSON apiPrefetchCache в <script> на странице поста ВК."""
        results, seen = [], set()
        for s in re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL):
            if '"type":"audio"' not in s:
                continue
            for m in re.finditer(
                r'"type"\s*:\s*"audio"\s*,\s*"audio"\s*:\s*(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})', s
            ):
                try:
                    a = json.loads(m.group(1))
                    oid, aid = str(a.get('owner_id', '')), str(a.get('id', ''))
                    full_id = f"{oid}_{aid}"
                    if not oid or not aid or full_id in seen:
                        continue
                    seen.add(full_id)
                    artist   = BeautifulSoup(str(a.get('artist', '') or ''), 'html.parser').get_text(strip=True)
                    title    = BeautifulSoup(str(a.get('title',  '') or ''), 'html.parser').get_text(strip=True)
                    duration = a.get('duration', 0) or 0
                    dur_str  = f"{duration//60}:{duration%60:02d}" if duration else ""
                    url      = a.get('url', '') or ''
                    try:
                        oi = int(oid)
                        owner_disp = f"club{abs(oi)}" if oi < 0 else f"id{oi}"
                    except ValueError:
                        owner_disp = oid
                    if not title:
                        continue
                    results.append((artist, title, dur_str, owner_disp, url, full_id))
                    if max_count and len(results) >= max_count:
                        return results
                except Exception:
                    continue
        return results

    @staticmethod
    def _parse_wall_video_scripts(html: str, max_count) -> list:
        """Извлекает видео из JSON apiPrefetchCache в <script> на странице поста ВК."""
        results, seen = [], set()
        for s in re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL):
            if '"type":"video"' not in s and '"type": "video"' not in s:
                continue
            for m in re.finditer(r'"type"\s*:\s*"video"\s*,\s*"video"\s*:\s*\{', s):
                start = m.end() - 1  # позиция открывающей {
                depth, end = 0, start
                for j in range(start, min(start + 100_000, len(s))):
                    if s[j] == '{':
                        depth += 1
                    elif s[j] == '}':
                        depth -= 1
                        if depth == 0:
                            end = j + 1
                            break
                if end <= start:
                    continue
                try:
                    v = json.loads(s[start:end])
                    oid = str(v.get('owner_id', ''))
                    vid = str(v.get('id', ''))
                    full_id = f"{oid}_{vid}"
                    if not oid or not vid or full_id in seen:
                        continue
                    seen.add(full_id)
                    title    = BeautifulSoup(str(v.get('title', '') or ''), 'html.parser').get_text(strip=True) or 'Без названия'
                    duration = v.get('duration', 0) or 0
                    dur_str  = f"{duration//60}:{duration%60:02d}" if duration else ""
                    video_url = f"https://vk.com/video{oid}_{vid}"
                    images   = v.get('image', []) or v.get('photo', [])
                    thumb_url = ''
                    if images and isinstance(images, list):
                        last = images[-1]
                        if isinstance(last, dict):
                            thumb_url = last.get('url', '')
                    results.append((title[:120], dur_str, "", thumb_url, video_url))
                    if max_count and len(results) >= max_count:
                        return results
                except Exception:
                    continue
        return results

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

            # Новый React-каталог музыки ВК: и /catalog/, и обычные страницы
            # плейлистов рендерятся одинаково (блоки music_playlist_item_block,
            # ссылки лежат только в React-fiber). Извлекаем единообразно.
            results = self._parse_react_playlists(limit)

            # Фолбэк на старую desktop-вёрстку (audio_pl_item2), если React-блоков нет
            if not results:
                results = self._parse_playlists_html(self.driver.page_source, limit)

            log_message(f"INFO VK playlists: найдено {len(results)}")
            self._sig.playlist_results_ready.emit(results)
        except Exception as e:
            log_message(f"ERROR VK playlists: {e}")
            self._sig.status.emit(f"Ошибка: {e}")
        finally:
            self._sig.search_done.emit()

    _PL_SEL = '[data-testid="music_playlist_item_block"]'

    def _parse_react_playlists(self, limit) -> list:
        """Извлекает плейлисты из нового React-каталога ВК (с прокруткой)."""
        log_message(f"INFO playlists: начало, url={self.driver.current_url[:80]}")
        count_js = ('return document.querySelectorAll(\''
                    + self._PL_SEL + '\').length')
        try:
            WebDriverWait(self.driver, 15).until(
                lambda d: d.execute_script(count_js + ' > 0')
            )
            log_message("INFO playlists: React-блоки появились")
        except Exception:
            log_message(f"WARNING playlists: блоки не появились за 15 сек, url={self.driver.current_url[:80]}")
            return []

        # Подгружаем все плейлисты прокруткой (ленивая загрузка)
        prev_n, stable = -1, 0
        for _ in range(40):
            n = self.driver.execute_script(count_js)
            self._sig.status.emit(f"Загружаю плейлисты... ({n})")
            if limit and n >= limit:
                break
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1.2)
            if n == prev_n:
                stable += 1
                if stable >= 2:
                    break
            else:
                stable = 0
            prev_n = n

        return self._extract_react_playlists(limit)

    def _extract_react_playlists(self, limit) -> list:
        """Считывает title/author/url из React-fiber каждой карточки."""
        js = r"""
return (function(){
    function fk(el){return Object.keys(el).find(k=>k.startsWith('__reactFiber')||k.startsWith('__reactInternalInstance'));}
    function cnt(o){
        var c=o.count!=null?o.count:(o.total_count!=null?o.total_count:(o.totalCount!=null?o.totalCount:null));
        return (c!=null&&!isNaN(c))?(''+c):'';
    }
    function up(fiber){
        var cur=fiber;
        for(var d=0;d<40&&cur;d++){
            var p=cur.memoizedProps;
            if(p){
                if(typeof p.href==='string'&&p.href.indexOf('/music/playlist/')!==-1) return {url:p.href,count:''};
                var cs=[p.playlist,p.item,p.data,p.audio,p.audioPlaylist,p.playlistData,p.model];
                for(var i=0;i<cs.length;i++){
                    var o=cs[i];
                    if(o&&typeof o==='object'&&o.owner_id!=null&&o.id!=null)
                        return {url:'/music/playlist/'+o.owner_id+'_'+o.id+(o.access_key?'_'+o.access_key:''),count:cnt(o)};
                }
            }
            cur=cur.return;
        }
        return {url:'',count:''};
    }
    // Глобальный список playlists_ids — фолбэк по индексу
    var plIds=null, item0=document.querySelector('[data-testid="music_playlist_item_block"]');
    if(item0){var k0=fk(item0); if(k0){var c=item0[k0];
        for(var d=0;d<20&&c;d++){var p=c.memoizedProps;
            if(p&&p.model&&p.model.raw&&p.model.raw.data&&p.model.raw.data.playlists_ids){plIds=p.model.raw.data.playlists_ids;break;}
            c=c.return;}}}
    var items=document.querySelectorAll('[data-testid="music_playlist_item_block"]');
    var res=[];
    items.forEach(function(item,i){
        var te=item.querySelector('[data-testid="MusicPlaylistItem_Title"]');
        var ae=item.querySelector('[data-testid="MusicPlaylistItem_AuthorLink"]');
        var title=te?te.textContent.trim():'';
        var author=ae?ae.textContent.trim():'';
        var url='',count='',k=fk(item);
        if(k){try{var r=up(item[k]);url=r.url||'';count=r.count||'';}catch(e){}}
        if(!url&&plIds&&plIds[i]) url='/music/playlist/'+plIds[i];
        if(title) res.push([title,author,count,url]);
    });
    return JSON.stringify(res);
})()
"""
        results = []
        for attempt in range(6):
            try:
                raw = self.driver.execute_script(js)
                if not raw:
                    time.sleep(2)
                    continue
                items = json.loads(raw)
                log_message(f"INFO playlists [{attempt}]: {len(items)} карточек")
                results = []
                for t, a, c, u in items:
                    if u and not u.startswith("http"):
                        u = 'https://vk.com' + u
                    results.append((t, a, c, u))
                # ждём гидратации ссылок, но не зацикливаемся, если их нет вовсе
                if results and all(r[3] for r in results):
                    break
            except Exception as e:
                log_message(f"WARNING playlists [{attempt}]: {e}")
            time.sleep(2)
        if limit:
            results = results[:limit]
        return results

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
        _save_state_soon()

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
        if not t.selectedItems() or row not in {i.row() for i in t.selectedItems()}:
            t.selectRow(row)
        menu = QMenu(self)
        menu.addAction("Показать треки",         self._download_playlist_selected)
        menu.addAction("Скачать выбранные",      self._dl_playlist_ytdlp_selected)
        menu.addSeparator()
        menu.addAction("Описание",               self._show_playlist_description)
        menu.addAction("Копировать ссылку",      self._copy_playlist_link)
        menu.addSeparator()
        menu.addAction("Выбрать все",            t.selectAll)
        menu.exec(t.viewport().mapToGlobal(pos))

    def _dl_playlist_ytdlp_selected(self):
        tab = self._current_playlist_tab()
        if not tab:
            return
        folder = settings.get("download_folder", "")
        if not folder:
            self._sig.status.emit("Укажите папку загрузок в настройках")
            return
        rows = sorted({i.row() for i in tab.table.selectedItems()})
        if not rows:
            return
        playlists = []
        for r in rows:
            item = tab.table.item(r, 0)
            if item:
                playlists.append((item.data(Qt.ItemDataRole.UserRole), item.text()))
        if not playlists:
            return
        threading.Thread(
            target=self._worker_dl_playlists_batch, args=(playlists, folder), daemon=True
        ).start()

    def _worker_dl_playlists_batch(self, playlists: list, folder: str):
        for pl_url, pl_title in playlists:
            self._worker_dl_playlist_ytdlp(pl_url, pl_title, folder)

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
                desc = self.driver.execute_script(_PLAYLIST_DESC_JS) or ""
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
                desc = self.driver.execute_script(_PLAYLIST_DESC_JS) or ""
                if desc:
                    break
            log_message(f"INFO playlist desc: {len(desc)} символов")
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
        tab_idx = self.tabs.indexOf(tab)
        if tab_idx >= 0:
            self.tabs.setTabText(tab_idx, f"🎬 {short}")

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
        _save_state_soon()

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
        from queues import add_to_queue, process_queue
        from utils import is_download_active
        for url in urls:
            add_to_queue(url)
        if not is_download_active():
            threading.Thread(target=process_queue, daemon=True).start()
        self._sig.status.emit(f"Добавлено в очередь: {len(urls)} видео")

    def _cdp_capture_url(self, d: dict, id_to_el: dict, mobile_items, i: int):
        """Проигрывает трек на мобильной странице и захватывает СВЕЖИЙ audio URL
        (для перезахвата при ретрае — новая ссылка может лечь на рабочий узел)."""
        audio_url = None
        full_id = d.get("full_id", "")
        try:
            prev_src = self.driver.execute_script(
                "var a=document.querySelector('audio'); return a?a.src:'';") or ''
            self.driver.get_log("performance")
            item_el = id_to_el.get(full_id)
            if item_el is None and i - 1 < len(mobile_items):
                item_el = mobile_items[i - 1]
            target = None
            if item_el is not None:
                try:
                    self.driver.execute_script(
                        "arguments[0].scrollIntoView({block:'center'});", item_el)
                    time.sleep(0.2)
                except Exception:
                    pass
                play_btns = item_el.find_elements(
                    By.CSS_SELECTOR, ".ai_play, [class*='play'], button")
                target = play_btns[0] if play_btns else item_el
                self.driver.execute_script("arguments[0].click();", target)
            # Ждём ссылку дольше (до ~15 с) и, если не появилась за ~7 с, кликаем ещё раз
            for it in range(100):
                time.sleep(0.15)
                if it == 50 and target is not None and not audio_url:
                    try:
                        self.driver.execute_script("arguments[0].click();", target)
                    except Exception:
                        pass
                src = self.driver.execute_script(
                    "var a=document.querySelector('audio');"
                    "return (a&&a.src&&a.src.startsWith('http'))?a.src:null;")
                if src and src != prev_src:
                    audio_url = src
                    break
                for entry in self.driver.get_log("performance"):
                    try:
                        u = json.loads(entry["message"]).get("message", {}) \
                                .get("params", {}).get("request", {}).get("url", "")
                        if "index.m3u8" in u:
                            audio_url = u
                            break
                        if "vkuseraudio" in u and not audio_url:
                            audio_url = u
                    except Exception:
                        continue
                if audio_url:
                    break
            try:
                self.driver.execute_script(
                    "var a=document.querySelector('audio');if(a)a.pause();")
            except Exception:
                pass
        except Exception as e:
            log_message(f"WARNING cdp recapture: {e}")
        return audio_url

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
        ok_count = 0
        failed_jobs = []
        done_count = [0]
        lock = threading.Lock()

        def _download(job):
            _, d, url, path = job
            if not url:
                log_message(f"WARNING cdp_batch: нет ссылки — {d['artist']} - {d['title']}")
                ok = False
            else:
                ok = self._dl_m3u8(url, path)
            with lock:
                done_count[0] += 1
                pct = 50 + done_count[0] / total * 50
                self._sig.progress.emit(pct)
                self._sig.status.emit(f"Скачано {done_count[0]}/{total}")
            return job, ok

        max_workers = min(4, total)
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(_download, job): job for job in jobs}
            for fut in as_completed(futures):
                job, ok = fut.result()
                d = job[1]
                if ok:
                    ok_count += 1
                    _add_vk_history(d["artist"], d["title"], job[3])
                else:
                    failed_jobs.append(job)

        # ── Повтор нескачанных: ПЕРЕЗАХВАТ свежей ссылки (переигрываем трек) ──
        # 504 от CDN часто привязан к конкретному узлу/токену; свежая ссылка
        # после переигрывания может лечь на рабочий узел.
        # Но если не скачалось НИЧЕГО — CDN недоступен, ретрай бессмыслен и долог.
        still_failed = []
        if failed_jobs and ok_count == 0:
            log_message("WARNING CDP: не скачалось ничего — вероятно CDN недоступен, "
                        "повтор пропущен")
            still_failed = list(failed_jobs)
            failed_jobs = []
        elif failed_jobs:
            log_message(f"INFO CDP повтор {len(failed_jobs)} треков (перезахват)...")
        for job in failed_jobs:
            idx, d, url, path = job
            self._sig.status.emit(f"Повтор: {d['artist']} - {d['title']}...")
            ok = False
            for attempt in range(2):
                time.sleep(2)
                fresh = self._cdp_capture_url(d, id_to_el, mobile_items, idx) or url
                if fresh and self._dl_m3u8(fresh, path):
                    ok = True
                    break
            if ok:
                ok_count += 1
                _add_vk_history(d["artist"], d["title"], path)
                log_message(f"INFO CDP повтор удался: {d['artist']} - {d['title']}")
            else:
                still_failed.append(job)

        fail_count = len(still_failed)
        if still_failed:
            log_message("WARNING CDP не скачались (" + str(fail_count) + "): "
                        + "; ".join(f"{j[1]['artist']} - {j[1]['title']}" for j in still_failed))

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

        # Ошибки для кнопки «Посмотреть ошибки» + контекст повтора нескачанных
        errors = []
        for _, d, url, _ in still_failed:
            name = f"{d['artist']} - {d['title']}" if d.get('artist') else d.get('title', '')
            if url:
                reason = "Сервер VK не отдал файл (обычно 504 — перегрузка/атака на VK). Попробуйте позже."
            else:
                reason = ("Поток не получен — VK не запустил трек (вероятно перегрузка). "
                          "В браузере трек может играться; попробуйте позже.")
            errors.append((name, reason))
        if errors:
            self._retry_ctx = {
                "rows": [j[1] for j in still_failed],
                "folder": folder, "pl_url": pl_url, "mobile_url": mobile_url,
            }
            self._sig.errors_ready.emit(errors)

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
        # Сбрасываем ошибки предыдущей загрузки и запоминаем вкладку текущей
        self._last_errors = []
        self._error_tab = None
        self._dl_tab = self.tabs.currentWidget()
        self.btn_errors.setVisible(False)
        if isinstance(self.tabs.currentWidget(), _VKVideoTab):
            self._download_vk_videos_selected()
            return
        if isinstance(self.tabs.currentWidget(), _VKPlaylistTab):
            self._dl_playlist_ytdlp_selected()
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
        vk_query   = getattr(tab, 'query',               '')   if tab else ''
        t = self._t()
        total_rows = t.rowCount() if t else 0
        if (pl_url or mobile_url) and all(not d.get("url") for d in rows):
            threading.Thread(
                target=self._dl_cdp_batch,
                args=(rows, folder, pl_url, mobile_url, pl_title), daemon=True
            ).start()
        else:
            threading.Thread(
                target=self._dl_batch_worker, args=(rows, folder, vk_query, total_rows), daemon=True
            ).start()

    def _on_errors_ready(self, errors: list):
        """Запоминает ошибки и привязывает их к вкладке, с которой шла загрузка."""
        self._last_errors = errors or []
        self._error_tab = self._dl_tab
        self.btn_errors.setText(f"⚠ Посмотреть ошибки ({len(self._last_errors)})")
        self._update_errors_btn()

    def _update_errors_btn(self):
        """Кнопка ошибок видна только на вкладке, где эти ошибки возникли."""
        visible = bool(self._last_errors) and self._error_tab is not None \
            and self.tabs.currentWidget() is self._error_tab
        self.btn_errors.setVisible(visible)

    def _show_errors_dialog(self):
        """Окно с понятным объяснением ошибок и повтором нескачанных треков."""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit, QPushButton
        dlg = QDialog(self)
        dlg.setWindowTitle("Не скачанные треки")
        dlg.setMinimumSize(560, 380)
        lay = QVBoxLayout(dlg)

        head = QLabel(
            f"Не удалось скачать: {len(self._last_errors)} трек(ов).\n"
            "Почти всегда это временная проблема на стороне серверов VK "
            "(ошибка 504 — перегрузка/атака), а не приложения. Обычно помогает "
            "повтор чуть позже."
        )
        head.setWordWrap(True)
        lay.addWidget(head)

        txt = QTextEdit()
        txt.setReadOnly(True)
        txt.setPlainText("\n".join(f"• {name}\n    {reason}" for name, reason in self._last_errors))
        lay.addWidget(txt, 1)

        btns = QHBoxLayout()
        retry_btn = QPushButton("↻ Повторить скачивание нескачанных")
        can_retry = bool(self._retry_ctx and self._retry_ctx.get("rows"))
        retry_btn.setEnabled(can_retry)
        def _do_retry():
            dlg.accept()
            self._retry_failed()
        retry_btn.clicked.connect(_do_retry)
        btns.addWidget(retry_btn)
        close_btn = QPushButton("Закрыть")
        close_btn.setProperty("secondary", True)
        close_btn.clicked.connect(dlg.reject)
        btns.addWidget(close_btn)
        lay.addLayout(btns)
        dlg.exec()

    def _retry_failed(self):
        """Повторно скачивает нескачанные треки из последней загрузки."""
        ctx = self._retry_ctx
        if not ctx or not ctx.get("rows"):
            self._sig.status.emit("Нет треков для повтора")
            return
        self._dl_tab = self._error_tab   # повтор привязываем к той же вкладке
        self.btn_errors.setVisible(False)
        self._sig.status.emit(f"Повтор скачивания {len(ctx['rows'])} треков...")
        # folder уже конечная (с именем плейлиста) → pl_title="" чтобы не вкладывать
        threading.Thread(
            target=self._dl_cdp_batch,
            args=(ctx["rows"], ctx["folder"], ctx["pl_url"], ctx["mobile_url"], ""),
            daemon=True
        ).start()


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
            # Сортировка — только если она реально активна на таблице
            hh = tab.table.horizontalHeader()
            if hh.isSortIndicatorShown():
                entry["sort_col"] = hh.sortIndicatorSection()
                entry["sort_order"] = hh.sortIndicatorOrder().value
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
                    self._add_audio_row(tab.table, row_data)
                self._restore_row(tab.table, entry.get("current_row", -1), entry)

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
                self._restore_row(tab.table, entry.get("current_row", -1), entry)

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
                self._restore_row(tab.table, entry.get("current_row", -1), entry)

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
    def _restore_row(table, row: int, entry=None):
        # Сначала сортировка (двигает строки), потом выделение строки
        if entry is not None:
            sc = entry.get("sort_col", -1)
            if sc is not None and sc >= 0:
                table.sortItems(sc, Qt.SortOrder(entry.get("sort_order", 0)))
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
        # ВРЕМЕННО: завершаем процесс Яндекса если запускали сами
        if self._yandex_proc:
            try:
                self._yandex_proc.terminate()
            except Exception:
                pass
            self._yandex_proc = None
