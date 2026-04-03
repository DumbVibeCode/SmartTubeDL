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
    QProgressBar, QMenu, QMessageBox, QFileDialog, QSizePolicy
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
    status         = pyqtSignal(str)
    progress       = pyqtSignal(float)   # 0-100
    speed          = pyqtSignal(str)
    batch          = pyqtSignal(str)
    show_progress  = pyqtSignal(bool)
    results_ready  = pyqtSignal(list)
    browser_ready  = pyqtSignal(bool)    # True = залогинен
    error          = pyqtSignal(str)
    search_done    = pyqtSignal()        # разблокировать кнопку


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

        # Таблица
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Исполнитель", "Название", "Длит.", "Владелец"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.doubleClicked.connect(self._download_selected)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_ctx_menu)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.sectionClicked.connect(self._sort_col)
        hdr.setHighlightSections(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(24)
        root.addWidget(self.table, 1)

        # Фильтр
        frow = QHBoxLayout(); frow.setContentsMargins(0, 0, 0, 0)
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("Фильтр по исполнителю, названию...")
        self.filter_input.textChanged.connect(self._filter)
        frow.addWidget(self.filter_input)
        root.addLayout(frow)

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

    # ── Таблица ───────────────────────────────────────────────────────────────

    def _populate_table(self, results: list):
        self.table.setRowCount(0)
        self.filter_input.blockSignals(True)
        self.filter_input.clear()
        self.filter_input.blockSignals(False)

        for row_data in results:
            if len(row_data) < 6:
                continue
            artist, title, duration, owner, url, full_id = row_data[:6]
            r = self.table.rowCount()
            self.table.insertRow(r)

            artist_item = QTableWidgetItem(artist)
            # Сохраняем скрытые поля в UserRole
            artist_item.setData(Qt.ItemDataRole.UserRole,     url)
            artist_item.setData(Qt.ItemDataRole.UserRole + 1, full_id)
            self.table.setItem(r, 0, artist_item)
            self.table.setItem(r, 1, QTableWidgetItem(title))
            self.table.setItem(r, 2, QTableWidgetItem(duration))
            self.table.setItem(r, 3, QTableWidgetItem(owner))

        total = self.table.rowCount()
        self._sig.status.emit(f"Найдено треков: {total}" if total else "Ничего не найдено")

    def _row_data(self, row: int):
        item = self.table.item(row, 0)
        if not item:
            return None
        return {
            "artist":   item.text(),
            "title":    self.table.item(row, 1).text() if self.table.item(row, 1) else "",
            "duration": self.table.item(row, 2).text() if self.table.item(row, 2) else "",
            "owner":    self.table.item(row, 3).text() if self.table.item(row, 3) else "",
            "url":      item.data(Qt.ItemDataRole.UserRole) or "",
            "full_id":  item.data(Qt.ItemDataRole.UserRole + 1) or "",
        }

    def _selected_rows_data(self) -> list[dict]:
        seen = set()
        result = []
        for idx in self.table.selectedItems():
            r = idx.row()
            if r not in seen:
                seen.add(r)
                d = self._row_data(r)
                if d:
                    result.append(d)
        return result

    def _filter(self, text: str):
        t = text.lower()
        visible = 0
        for r in range(self.table.rowCount()):
            artist = (self.table.item(r, 0).text() if self.table.item(r, 0) else "").lower()
            title  = (self.table.item(r, 1).text() if self.table.item(r, 1) else "").lower()
            hidden = bool(t) and t not in artist and t not in title
            self.table.setRowHidden(r, hidden)
            if not hidden:
                visible += 1
        total = self.table.rowCount()
        self.status_lbl.setText(f"Фильтр: {visible} из {total}" if t else f"Найдено треков: {total}")

    def _sort_col(self, col: int):
        rev = self._sort_rev.get(col, False)
        if col == 2:  # длительность — числовая
            def key(r):
                txt = self.table.item(r, 2).text() if self.table.item(r, 2) else ""
                try:
                    p = txt.split(":")
                    return int(p[0]) * 60 + int(p[1]) if len(p) == 2 else 0
                except Exception:
                    return 0
        else:
            def key(r):
                item = self.table.item(r, col)
                return item.text().lower() if item else ""

        rows = list(range(self.table.rowCount()))
        rows.sort(key=key, reverse=rev)
        self._sort_rev[col] = not rev

        # Переупорядочиваем строки через временный буфер данных
        buf = []
        for r in rows:
            row_buf = []
            for c in range(self.table.columnCount()):
                it = self.table.item(r, c)
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
                self.table.setItem(r, c, it)

    # ── Контекстное меню ──────────────────────────────────────────────────────

    def _show_ctx_menu(self, pos):
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        menu = QMenu(self)
        menu.addAction("Копировать «Исполнитель — Название»", self._copy_artist_title)
        menu.addAction("Копировать ссылку на владельца",      self._copy_owner_link)
        menu.addSeparator()
        menu.addAction("Скачать трек",         self._download_one)
        menu.addAction("Скачать выбранные",    self._download_selected)
        menu.addSeparator()
        menu.addAction("Выбрать все", self.table.selectAll)
        menu.exec(self.table.viewport().mapToGlobal(pos))

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

        self.table.setRowCount(0)
        self.search_btn.setEnabled(False)
        self._sig.status.emit("Поиск...")

        # Определяем тип запроса

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

            # Кликаем «Показать все»
            try:
                links = self.driver.find_elements(By.CSS_SELECTOR, "a[href*='section=recoms_block']")
                target = next((l for l in links if "Показать все" in (l.text or "")), None) or (links[0] if links else None)
                if target:
                    self._sig.status.emit("Открываю «Показать всё»...")
                    self.driver.execute_script("arguments[0].click();", target)
                    WebDriverWait(self.driver, 10).until(
                        lambda d: "section=recoms_block" in d.current_url
                    )
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
