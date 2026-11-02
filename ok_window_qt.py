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
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QProgressBar, QMessageBox, QMenu
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
    results_ready = pyqtSignal(list)    # [{href, title}] видео со страницы
    search_done   = pyqtSignal()        # разблокировать кнопку
    error         = pyqtSignal(str)


# JS: собрать все ссылки на видео на странице и выбрать для каждой осмысленный
# заголовок. На одно видео бывает несколько якорей (кнопка-оверлей «Смотреть»
# и обёртка заголовка) — группируем по id и берём самый длинный текст.
_COLLECT_VIDEOS_JS = r"""
    // Наименьший контейнер карточки: поднимаемся, пока в нём ссылки только
    // на это видео; из его текста достаём длительность (M:SS / H:MM:SS).
    function cardDuration(a, id){
        var card = a, node = a.parentElement, depth = 0;
        while (node && depth < 8){
            var ids = {}, links = node.querySelectorAll('a[href*="/video/"]');
            for (var i=0;i<links.length;i++){
                var mm = links[i].href.match(/\/video\/(\d+)/);
                if (mm) ids[mm[1]] = 1;
            }
            if (Object.keys(ids).length > 1) break;
            card = node; node = node.parentElement; depth++;
        }
        var dm = (card.textContent || '').match(/(?:^|\s)(\d{1,2}:\d{2}(?::\d{2})?)(?:\s|$)/);
        return dm ? dm[1] : '';
    }
    var map = {};
    var as = document.querySelectorAll('a[href*="/video/"]');
    var BAD = {'Смотреть':1,'':1};
    for (var i=0;i<as.length;i++){
        var a = as[i];
        var m = a.href.match(/\/video\/(\d+)/);
        if (!m) continue;
        var id = m[1];
        if (!map[id]) map[id] = {href:'https://ok.ru/video/'+id, cands:[], dur:''};
        var texts = [a.getAttribute('aria-label'), a.getAttribute('title'), a.innerText];
        for (var k=0;k<texts.length;k++){
            var t = (texts[k]||'').trim();
            if (t && !BAD[t]) map[id].cands.push(t.slice(0,200));
        }
        if (!map[id].dur){ var dd = cardDuration(a, id); if (dd) map[id].dur = dd; }
    }
    var out = [];
    for (var id in map){
        var c = map[id].cands, best='';
        for (var j=0;j<c.length;j++) if (c[j].length>best.length) best=c[j];
        out.push({href: map[id].href, title: best, duration: map[id].dur});
    }
    return out;
"""


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

        # Таблица результатов (видео со страницы)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Название", "Длит.", "Ссылка"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_ctx_menu)
        self.table.doubleClicked.connect(self._download_selected)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        root.addWidget(self.table, 1)

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
        self._sig.results_ready.connect(self._populate_table)
        self._sig.search_done.connect(lambda: self.search_btn.setEnabled(True))
        self._sig.error.connect(lambda m: QMessageBox.critical(self, "Ошибка", m))

    def _on_show_progress(self, visible: bool):
        self.prog_bar.setVisible(visible)
        if not visible:
            self.prog_bar.setValue(0)

    def _on_browser_ready(self, ok: bool):
        self.open_browser_btn.setEnabled(True)
        self.recheck_btn.setEnabled(True)
        self.search_btn.setEnabled(True)
        if ok:
            self.browser_lbl.setText("● Залогинен в OK")
            self.browser_lbl.setStyleSheet("color: #4caf50; font-weight: bold;")
        else:
            self.browser_lbl.setText("● Войдите в OK в браузере")
            self.browser_lbl.setStyleSheet("color: #e74c3c; font-weight: bold;")

    # ── Видео со страницы пользователя/группы ────────────────────────────────

    @staticmethod
    def _normalize_video_page(text: str) -> str | None:
        """Приводит ввод к URL страницы видео OK. Возвращает None, если это не
        ссылка OK (поиск по названию пока относится к музыке — другой этап)."""
        import re
        t = (text or "").strip()
        if not t:
            return None
        if not re.search(r'(?:^https?://)?(?:www\.|m\.)?ok\.ru/|odnoklassniki\.ru/', t, re.I):
            return None
        if not t.lower().startswith("http"):
            t = "https://" + t.lstrip("/")
        # Одиночное видео — оставляем как есть
        if re.search(r'/video/\d+', t):
            return t
        # Страница пользователя/группы → её раздел видео
        t = t.split("?")[0].rstrip("/")
        if not t.endswith("/video"):
            t = t + "/video"
        return t

    def _on_search(self):
        if not self.driver:
            QMessageBox.warning(self, "Браузер не готов", "Браузер ещё не запущен.")
            return
        raw = self.query_input.text().strip()
        page = self._normalize_video_page(raw)
        if page is None:
            QMessageBox.information(
                self, "Нужна ссылка OK",
                "Вставьте ссылку на профиль или группу OK (например "
                "ok.ru/group51217248157720) либо на конкретное видео "
                "ok.ru/video/....\n\nПоиск музыки по названию появится отдельным этапом."
            )
            return
        # Одиночное видео — сразу в таблицу без разбора страницы
        import re
        if re.search(r'/video/\d+', page):
            self._populate_table([{"href": page, "title": raw, "duration": ""}])
            self._sig.status.emit("Готово: 1 видео")
            return
        self.search_btn.setEnabled(False)
        self._sig.status.emit("Открываю страницу видео...")
        self._sig.show_progress.emit(True)
        threading.Thread(target=self._worker_videos, args=(page,), daemon=True).start()

    def _worker_videos(self, page_url: str):
        try:
            self.driver.get(page_url)
            time.sleep(4)
            # Скроллим, пока подгружаются карточки (останавливаемся на плато)
            prev = -1
            for _ in range(20):
                try:
                    found = self.driver.execute_script(
                        "return document.querySelectorAll('a[href*=\"/video/\"]').length;"
                    )
                except Exception:
                    found = 0
                self._sig.status.emit(f"Загрузка списка... найдено ссылок: {found}")
                self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(1.5)
                if found == prev:
                    break
                prev = found
            vids = self.driver.execute_script(_COLLECT_VIDEOS_JS) or []
            # Чистим заголовки и сортируем по названию
            for v in vids:
                v["title"] = (v.get("title") or "").strip() or v["href"].rsplit("/", 1)[-1]
            vids.sort(key=lambda v: v["title"].lower())
            log_message(f"INFO OK: собрано видео: {len(vids)} со страницы {page_url}")
            self._sig.results_ready.emit(vids)
            self._sig.status.emit(f"Готово: {len(vids)} видео")
        except Exception as e:
            log_message(f"ERROR OK видео-страница: {e}")
            self._sig.error.emit(f"Не удалось получить список видео:\n{e}")
        finally:
            self._sig.show_progress.emit(False)
            self._sig.search_done.emit()

    def _populate_table(self, results: list):
        self.table.setRowCount(0)
        for v in results:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(v.get("title", "")))
            self.table.setItem(r, 1, QTableWidgetItem(v.get("duration", "")))
            self.table.setItem(r, 2, QTableWidgetItem(v.get("href", "")))
        self.dl_btn.setEnabled(self.table.rowCount() > 0)

    def _selected_urls(self) -> list:
        urls, seen = [], set()
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if not rows:
            rows = range(self.table.rowCount())   # ничего не выбрано → все
        for r in rows:
            it = self.table.item(r, 2)
            u = it.text().strip() if it else ""
            if u and u not in seen:
                seen.add(u)
                urls.append(u)
        return urls

    def _download_selected(self):
        urls = self._selected_urls()
        if not urls:
            QMessageBox.information(self, "Нет видео", "Список пуст — сначала найдите видео.")
            return
        from queues import add_to_queue, process_queue
        import download
        for u in urls:
            add_to_queue(u)
        log_message(f"INFO OK: в очередь добавлено видео: {len(urls)}")
        self._sig.status.emit(f"В очередь добавлено: {len(urls)} — качаю по очереди")
        if not getattr(download, "is_downloading", False):
            threading.Thread(target=process_queue, daemon=True).start()

    def _show_ctx_menu(self, pos):
        if self.table.rowCount() == 0:
            return
        menu = QMenu(self)
        act_dl = menu.addAction("⬇ Скачать выбранные")
        act_copy = menu.addAction("Копировать ссылку")
        act = menu.exec(self.table.viewport().mapToGlobal(pos))
        if act == act_dl:
            self._download_selected()
        elif act == act_copy:
            urls = self._selected_urls()
            if urls:
                from PyQt6.QtWidgets import QApplication
                QApplication.clipboard().setText("\n".join(urls))
                self._sig.status.emit(f"Скопировано ссылок: {len(urls)}")

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
