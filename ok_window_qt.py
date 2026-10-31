"""
Одноклассники (OK.ru) — PyQt6 окно, интегрированное в YTD.

Этап 1: каркас окна + браузерная авторизация (отдельный Chrome-профиль
.ok_chrome_profile), как сделано с ВКонтакте. У OK нет открытого music-API,
поэтому авторизация и доступ к музыке — только через браузер (Selenium + CDP).

Поиск музыки, загрузка музыки и видео со страницы пользователя добавляются
отдельными этапами — их внутренние эндпоинты OK смотрятся вживую под логином.
"""

import os
import sys
import time
import json
import threading
import subprocess

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTableWidget, QHeaderView, QAbstractItemView, QProgressBar,
    QMessageBox, QTabWidget
)
from PyQt6.QtCore import Qt, QObject, pyqtSignal, QMetaObject, Q_ARG
from PyQt6.QtGui import QFont

from logger import log_message


# ── Зависимости ──────────────────────────────────────────────────────────────

SELENIUM_OK = True
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from webdriver_manager.chrome import ChromeDriverManager
    from selenium.webdriver.chrome.service import Service
except Exception as _e:
    SELENIUM_OK = False
    log_message(f"WARNING OK: зависимости недоступны: {_e}")

OK_PROFILE_DIR = os.path.join(os.getcwd(), ".ok_chrome_profile")


# ── Сигналы (thread-safe) ─────────────────────────────────────────────────────

class _Sig(QObject):
    status        = pyqtSignal(str)
    progress      = pyqtSignal(float)   # 0-100
    show_progress = pyqtSignal(bool)
    browser_ready = pyqtSignal(bool)    # True = залогинен
    error         = pyqtSignal(str)


class OKSearchWindow(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.driver = None
        self._sig = _Sig()

        self._build_ui()
        self._connect_signals()

        if not SELENIUM_OK:
            QMessageBox.critical(
                self, "Ошибка зависимостей",
                "Не найдены модули Selenium / webdriver-manager.\n\n"
                "Установите:\n  pip install selenium webdriver-manager"
            )
        else:
            threading.Thread(target=self._browser_worker, daemon=True).start()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.setWindowTitle("Одноклассники — музыка и видео")
        self.setGeometry(120, 120, 950, 620)
        self.setMinimumSize(700, 400)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 8)
        root.setSpacing(6)

        # Заголовок + статус браузера
        top = QHBoxLayout()
        h = QLabel("Одноклассники")
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

        root.addLayout(top)

        # Строка поиска (пока заглушка — наполним на следующем этапе)
        row = QHBoxLayout(); row.setSpacing(6)
        self.query_input = QLineEdit()
        self.query_input.setPlaceholderText(
            "Исполнитель / название, либо ссылка ok.ru/profile/... или ok.ru/group/..."
        )
        self.query_input.returnPressed.connect(self._on_search)
        row.addWidget(self.query_input, 1)

        self.search_btn = QPushButton("Искать")
        self.search_btn.setEnabled(False)
        self.search_btn.clicked.connect(self._on_search)
        row.addWidget(self.search_btn)

        self.dl_btn = QPushButton("⬇ Скачать выбранные")
        self.dl_btn.setProperty("secondary", True)
        self.dl_btn.setEnabled(False)
        self.dl_btn.clicked.connect(self._download_selected)
        row.addWidget(self.dl_btn)

        root.addLayout(row)

        # Тонкий прогресс-бар
        self.prog_bar = QProgressBar()
        self.prog_bar.setFixedHeight(3)
        self.prog_bar.setTextVisible(False)
        self.prog_bar.setVisible(False)
        root.addWidget(self.prog_bar)

        # Область результатов (заглушка этапа 1)
        self.tabs = QTabWidget()
        placeholder = QWidget()
        pl = QVBoxLayout(placeholder)
        pl.addStretch()
        info = QLabel(
            "Этап 1: авторизация в Одноклассниках.\n\n"
            "Войдите в свой аккаунт в открывшемся окне браузера.\n"
            "Когда статус станет «Залогинен», можно добавлять поиск музыки\n"
            "и загрузку видео со страницы пользователя."
        )
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        info.setStyleSheet("color: #888;")
        pl.addWidget(info)
        pl.addStretch()
        self.tabs.addTab(placeholder, "Начало")
        root.addWidget(self.tabs, 1)

        # Статус
        bot = QHBoxLayout()
        self.status_lbl = QLabel("Готово")
        self.status_lbl.setProperty("secondary", True)
        bot.addWidget(self.status_lbl, 1)
        root.addLayout(bot)

    def _connect_signals(self):
        self._sig.status.connect(self.status_lbl.setText)
        self._sig.show_progress.connect(self._on_show_progress)
        self._sig.progress.connect(lambda v: self.prog_bar.setValue(int(v)))
        self._sig.browser_ready.connect(self._on_browser_ready)
        self._sig.error.connect(lambda m: QMessageBox.critical(self, "Ошибка", m))

    def _on_show_progress(self, visible: bool):
        self.prog_bar.setVisible(visible)
        if not visible:
            self.prog_bar.setValue(0)

    def _on_browser_ready(self, ok: bool):
        self.open_browser_btn.setEnabled(True)
        self.recheck_btn.setEnabled(True)
        if ok:
            self.browser_lbl.setText("● Залогинен в OK")
            self.browser_lbl.setStyleSheet("color: #4caf50; font-weight: bold;")
        else:
            self.browser_lbl.setText("● Войдите в OK в браузере")
            self.browser_lbl.setStyleSheet("color: #e74c3c; font-weight: bold;")

    # ── Поиск / загрузка (заглушки этапа 1) ──────────────────────────────────

    def _on_search(self):
        QMessageBox.information(
            self, "В разработке",
            "Поиск по музыке и разбор страницы пользователя добавляются "
            "следующим этапом.\n\nСейчас нужно войти в аккаунт OK в браузере — "
            "по этому логину дальше будем доставать музыку и видео."
        )

    def _download_selected(self):
        self._on_search()

    # ── Браузер (Selenium) ────────────────────────────────────────────────────

    @staticmethod
    def _kill_profile_chrome(profile_dir):
        """Убивает зависшие процессы Chrome, держащие наш профиль (осиротевшие
        после краша). Бьём только по нашему профилю — личный браузер не трогаем."""
        if sys.platform != "win32":
            return
        marker = os.path.basename(profile_dir.rstrip("\\/"))
        if not marker:
            return
        ps = (
            "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
            "Where-Object { $_.CommandLine -like '*" + marker + "*' } | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force "
            "-ErrorAction SilentlyContinue }"
        )
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            log_message(f"INFO OK: закрыты зависшие процессы браузера профиля {marker}")
        except Exception as e:
            log_message(f"WARNING OK: не удалось закрыть зависшие процессы браузера: {e}")

    @staticmethod
    def _clean_chrome_crash_markers(profile_dir):
        """Убирает следы аварийного завершения Chrome, из-за которых новый запуск
        падает с 'DevToolsActivePort file doesn't exist'."""
        try:
            for name in ("SingletonLock", "SingletonCookie", "SingletonSocket",
                         "DevToolsActivePort", "lockfile"):
                p = os.path.join(profile_dir, name)
                try:
                    if os.path.lexists(p):
                        os.remove(p)
                except Exception:
                    pass
            prefs_path = os.path.join(profile_dir, "Default", "Preferences")
            if os.path.exists(prefs_path):
                try:
                    with open(prefs_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    prof = data.get("profile", {})
                    prof["exited_cleanly"] = True
                    prof["exit_type"] = "Normal"
                    data["profile"] = prof
                    with open(prefs_path, "w", encoding="utf-8") as f:
                        json.dump(data, f)
                except Exception:
                    pass
        except Exception as e:
            log_message(f"WARNING OK: очистка профиля: {e}")

    def _browser_worker(self):
        try:
            log_message("INFO OK: запуск браузера")
            profile_dir = OK_PROFILE_DIR
            os.makedirs(profile_dir, exist_ok=True)

            def _make_opts():
                o = webdriver.ChromeOptions()
                o.add_argument(f"--user-data-dir={profile_dir}")
                o.add_argument("--start-maximized")
                o.add_argument("--disable-blink-features=AutomationControlled")
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

            self._kill_profile_chrome(profile_dir)
            self._clean_chrome_crash_markers(profile_dir)
            try:
                self.driver = _launch()
            except Exception as e:
                log_message(f"WARNING OK: первый запуск не удался ({e}); чищу профиль и повторяю")
                self._clean_chrome_crash_markers(profile_dir)
                time.sleep(1)
                self.driver = _launch()

            self.driver.get("https://ok.ru")
            log_message("INFO OK: браузер открыт, жду логина...")
            QMetaObject.invokeMethod(
                self.recheck_btn, "setEnabled",
                Qt.ConnectionType.QueuedConnection,
                Q_ARG(bool, True)
            )
            self._wait_login()
        except Exception as e:
            log_message(f"ERROR OK браузер: {e}")
            self._sig.error.emit(f"Ошибка запуска браузера OK:\n{e}")
            self._sig.browser_ready.emit(False)

    def _is_logged_in(self) -> bool:
        if not self.driver:
            return False
        try:
            url = self.driver.current_url or ""
            # Явная страница входа
            if any(x in url for x in ("/dk?st.cmd=anonym", "st.cmd=anonymLogin",
                                      "/login", "about:blank")):
                return False
            if "ok.ru" not in url and "odnoklassniki.ru" not in url:
                return False
            # Есть форма входа — не залогинены
            if self.driver.find_elements(
                By.CSS_SELECTOR,
                "#field_email, #field_password, input[name='st.email'], "
                "input[name='st.password'], form[action*='Login']"
            ):
                return False
            # Признаки залогиненного состояния (верхняя панель OK)
            for sel in [
                "#hook_Block_MyProfileToolbarUser",
                "#hook_Block_Navigation",
                "a[href^='/profile']",
                "#toolbar", ".toolbar",
                "[data-l*='userPage']",
            ]:
                if self.driver.find_elements(By.CSS_SELECTOR, sel):
                    return True
            return False
        except Exception:
            pass
        return False

    def _wait_login(self):
        for _ in range(150):   # до 5 минут
            if self._is_logged_in():
                log_message("INFO OK: логин обнаружен")
                self._sig.browser_ready.emit(True)
                return
            time.sleep(2)
        log_message("WARNING OK: логин не обнаружен за 5 мин")
        self._sig.browser_ready.emit(False)

    def _open_browser(self):
        """Открывает браузер, если он закрыт или не отвечает."""
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
        self.browser_lbl.setText("● Браузер запускается...")
        self.browser_lbl.setStyleSheet("color: orange; font-weight: bold;")
        threading.Thread(target=self._browser_worker, daemon=True).start()

    def _recheck_login(self):
        """Ручная проверка состояния входа в OK"""
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

    def quit_browser(self):
        """Закрывает Selenium-браузер (вызывается при выходе из приложения)."""
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None
