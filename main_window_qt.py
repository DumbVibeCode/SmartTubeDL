"""
Единое главное окно SmartTubeDL (UI v2).
Объединяет все разделы (YouTube, ВК, ОК, Mail.ru, По ссылкам, Очередь, История,
Настройки + Обновления, Логи) с левой боковой панелью и постоянной нижней панелью статуса.
"""

import os
import re
import threading

from PyQt6.QtCore import Qt, QTimer, QObject, QUrl, pyqtSignal
from PyQt6.QtGui import QFont, QShortcut, QKeySequence
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QListWidget, QListWidgetItem, QStackedWidget, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QComboBox, QCheckBox,
    QProgressBar, QTextEdit, QFileDialog, QMessageBox, QFrame, QTabWidget, QMenu,
    QSlider
)

import styles as _styles
from styles import COLORS_MINIMAL as COLORS
from config import settings, save_settings, format_invidious_duration
from download_history import load_download_history
from logger import log_message
from queues import add_to_queue, process_queue, get_queue_count
import utils as _utils


# ── Раздел: Mail.ru (полноценный поиск музыки и плейлистов + вкладки) ────────

_MAILRU_TABS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mailru_search_tabs.json")


class _MailRuSignals(QObject):
    results_ready = pyqtSignal(object, list, str, str)  # (tab, items, kind: 'tracks'|'playlists', title_hint)
    status = pyqtSignal(str)
    progress = pyqtSignal(int)
    busy = pyqtSignal(bool)
    error = pyqtSignal(str)


class _MailRuTab(QWidget):
    """Одна вкладка Mail.ru: таблица + единая нижняя панель (фильтр + счётчик + действие)."""

    def __init__(self, query: str = "", kind: str = "tracks", on_action=None, on_ctx=None):
        super().__init__()
        self.query = query
        self.kind = kind          # 'tracks' | 'playlists'
        self.items = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(6)

        self.table = QTableWidget(0, 5)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        if on_ctx:
            self.table.customContextMenuRequested.connect(on_ctx)
        if on_action:
            self.table.doubleClicked.connect(lambda _idx: on_action())
        self.table.itemSelectionChanged.connect(self._update_sel)
        lay.addWidget(self.table, 1)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("Фильтр по названию или исполнителю (Ctrl+F)...")
        self.filter_input.textChanged.connect(self._apply_filter)
        bar.addWidget(self.filter_input, 1)

        self.sel_lbl = QLabel("Выбрано: 0")
        bar.addWidget(self.sel_lbl)

        self.action_btn = QPushButton("⬇ Скачать выбранные")
        self.action_btn.setEnabled(False)
        if on_action:
            self.action_btn.clicked.connect(on_action)
        bar.addWidget(self.action_btn)
        lay.addLayout(bar)

    def _update_sel(self):
        cnt = len({idx.row() for idx in self.table.selectedIndexes()})
        self.sel_lbl.setText(f"Выбрано: {cnt}")

    def _apply_filter(self, text: str):
        q = text.strip().lower()
        cols = self.table.columnCount()
        for r in range(self.table.rowCount()):
            if not q:
                self.table.setRowHidden(r, False)
                continue
            matched = False
            for c in range(cols):
                it = self.table.item(r, c)
                if it and q in it.text().lower():
                    matched = True
                    break
            self.table.setRowHidden(r, not matched)

    def populate(self, items: list, kind: str):
        self.items = list(items)
        self.kind = kind
        self.table.setUpdatesEnabled(False)
        self.table.setSortingEnabled(False)
        try:
            self.table.setRowCount(0)
            hh = self.table.horizontalHeader()
            if kind == "playlists":
                self.table.setColumnCount(4)
                self.table.setHorizontalHeaderLabels(["Плейлист", "Владелец", "Треков", "Ссылка"])
                hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
                hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
                hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
                hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
                self.table.setColumnWidth(1, 160)
                self.table.setColumnWidth(2, 70)
                self.table.setColumnWidth(3, 220)
                _styles.bind_table_column_widths(self.table, "mailru_playlists")
                self.table.setRowCount(len(items))
                for i, p in enumerate(items):
                    it0 = QTableWidgetItem(p.get("title", "Плейлист"))
                    it0.setData(Qt.ItemDataRole.UserRole, dict(p))
                    self.table.setItem(i, 0, it0)
                    it_own = QTableWidgetItem(p.get("owner", ""))
                    if p.get("owner_url"):
                        it_own.setToolTip(p["owner_url"])
                    self.table.setItem(i, 1, it_own)
                    self.table.setItem(i, 2, QTableWidgetItem(str(p.get("tracks_count", ""))))
                    self.table.setItem(i, 3, QTableWidgetItem(p.get("url", "")))
                self.action_btn.setText("📋 Открыть плейлист")
            else:
                self.table.setColumnCount(5)
                self.table.setHorizontalHeaderLabels(["Исполнитель", "Название", "Длит.", "Битрейт", "Владелец"])
                hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
                hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
                hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
                hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
                hh.setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
                self.table.setColumnWidth(0, 210)
                self.table.setColumnWidth(2, 65)
                self.table.setColumnWidth(3, 80)
                self.table.setColumnWidth(4, 150)
                _styles.bind_table_column_widths(self.table, "mailru_tracks")
                self.table.setRowCount(len(items))
                for i, t in enumerate(items):
                    it0 = QTableWidgetItem(t.get("artist", ""))
                    it0.setData(Qt.ItemDataRole.UserRole, dict(t))
                    self.table.setItem(i, 0, it0)
                    self.table.setItem(i, 1, QTableWidgetItem(t.get("title", "")))
                    self.table.setItem(i, 2, QTableWidgetItem(t.get("duration", "")))
                    self.table.setItem(i, 3, QTableWidgetItem(t.get("bitrate", "")))
                    it_own = QTableWidgetItem(t.get("owner", ""))
                    if t.get("owner_url"):
                        it_own.setToolTip(t["owner_url"])
                    self.table.setItem(i, 4, it_own)
                self.action_btn.setText("⬇ Скачать выбранные")
            self.action_btn.setEnabled(len(items) > 0)
        finally:
            self.table.setSortingEnabled(True)
            self.table.setUpdatesEnabled(True)


class MailRuPage(QWidget):
    """Раздел Mail.ru: полноценный поиск треков/плейлистов, прямые ссылки и вкладки."""

    def __init__(self):
        super().__init__()
        self._busy = False
        self._restoring = True
        self._sig = _MailRuSignals()
        self._sig.results_ready.connect(self._on_results_ready)
        self._sig.status.connect(self.status_label_set)
        self._sig.progress.connect(self._set_progress)
        self._sig.busy.connect(self._set_busy)
        self._sig.error.connect(lambda m: QMessageBox.warning(self, "Mail.ru", m))
        try:
            self._setup_ui()
            self._restore_tabs()
        finally:
            self._restoring = False

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 8)
        lay.setSpacing(6)

        hdr = QLabel("Mail.ru — Музыка")
        f = QFont()
        f.setPointSize(12)
        f.setBold(True)
        hdr.setFont(f)
        lay.addWidget(hdr)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Поиск музыки / плейлистов или вставьте ссылку my.mail.ru/music/...")
        self.url_input.returnPressed.connect(self._on_fetch)
        row.addWidget(self.url_input, 1)

        self._search_hist = _styles.attach_search_history(self.url_input, "mailru_search_history", self._on_fetch)
        row.addWidget(self._search_hist.button)

        self.kind_combo = QComboBox()
        self.kind_combo.addItems(["Треки", "Плейлисты"])
        self.kind_combo.setFixedWidth(105)
        row.addWidget(self.kind_combo)

        self.btn_fetch = QPushButton("Найти")
        self.btn_fetch.clicked.connect(self._on_fetch)
        row.addWidget(self.btn_fetch)

        hist_btn = QPushButton("История")
        hist_btn.setProperty("secondary", True)
        hist_btn.setToolTip("Открыть историю загрузок")
        hist_btn.clicked.connect(self._open_history)
        row.addWidget(hist_btn)
        lay.addLayout(row)

        self.pbar = QProgressBar()
        self.pbar.setFixedHeight(3)
        self.pbar.setTextVisible(False)
        self.pbar.setVisible(False)
        lay.addWidget(self.pbar)

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        _styles.attach_new_tab_button(
            self.tabs,
            lambda: self._new_tab(""),
            tooltip="Новая вкладка (Ctrl+T)",
        )
        lay.addWidget(self.tabs, 1)

        self._new_tab("")

        foot = QHBoxLayout()
        self.status_lbl = QLabel("Введите запрос для поиска или вставьте ссылку на плейлист Mail.ru")
        self.status_lbl.setProperty("secondary", True)
        foot.addWidget(self.status_lbl, 1)
        lay.addLayout(foot)

    def _open_history(self):
        w = self.window()
        if w is not None and hasattr(w, "open_history"):
            w.open_history("Mail.ru")
        elif w is not None and hasattr(w, "switch_to") and hasattr(w, "IDX_HISTORY"):
            w.switch_to(w.IDX_HISTORY)

    def _cur_tab(self) -> _MailRuTab:
        w = self.tabs.currentWidget()
        if isinstance(w, _MailRuTab):
            return w
        return self._new_tab("")

    @property
    def filter_input(self) -> QLineEdit:
        return self._cur_tab().filter_input

    def _new_tab(self, query: str = "", kind: str = "tracks") -> _MailRuTab:
        tab = _MailRuTab(query=query, kind=kind, on_action=self._on_tab_action, on_ctx=self._show_ctx_menu)
        prefix = "📋" if kind == "playlists" else "🎵"
        short = (query[:20] + "…") if len(query) > 20 else (query or "Поиск")
        idx = self.tabs.addTab(tab, f"{prefix} {short}")
        self.tabs.setCurrentIndex(idx)
        return tab

    def _close_tab(self, index: int):
        if self.tabs.count() <= 1:
            return
        w = self.tabs.widget(index)
        self.tabs.removeTab(index)
        if w is not None:
            w.deleteLater()
        self._save_tabs()

    def _on_tab_changed(self, _index: int):
        w = self.tabs.currentWidget()
        if isinstance(w, _MailRuTab) and not self._restoring:
            self.url_input.setText(w.query)
            self._save_tabs()

    def _update_tab_title(self, tab: _MailRuTab, title_hint: str = ""):
        idx = self.tabs.indexOf(tab)
        if idx < 0:
            return
        prefix = "📋" if (tab.kind == "playlists" or tab.query.startswith("http")) else "🎵"
        base = title_hint or tab.query or "Поиск"
        short = (base[:20] + "…") if len(base) > 20 else base
        cnt = f" ({len(tab.items)})" if tab.items else ""
        self.tabs.setTabText(idx, f"{prefix} {short}{cnt}")
        self.tabs.setTabToolTip(idx, base)

    def status_label_set(self, text: str):
        self.status_lbl.setText(text)

    def _set_progress(self, val: int):
        self.pbar.setVisible(0 <= val < 100)
        if val >= 0:
            self.pbar.setValue(val)

    def _set_busy(self, busy: bool):
        self._busy = busy
        self.btn_fetch.setEnabled(not busy)
        cur = self.tabs.currentWidget()
        if isinstance(cur, _MailRuTab):
            cur.action_btn.setEnabled((not busy) and bool(cur.items))

    def open_url(self, url: str, title_hint: str = ""):
        self.url_input.setText(url)
        self._on_fetch(title_hint=title_hint)

    def _on_fetch(self, title_hint: str = ""):
        if self._busy:
            return
        raw = self.url_input.text().strip()
        if not raw:
            return
        self._search_hist.add(raw)
        from mailru_download import is_mailru_url, _fetch_tracks, search_mailru_tracks, search_mailru_playlists

        max_res = 0
        want_playlists = (self.kind_combo.currentText() == "Плейлисты") and not is_mailru_url(raw)
        kind = "playlists" if want_playlists else "tracks"

        tab = self._cur_tab()
        if tab.items and tab.query != raw:
            tab = self._new_tab(raw, kind=kind)
        tab.query = raw
        tab.kind = kind
        self._update_tab_title(tab, title_hint)

        self._sig.busy.emit(True)
        self._sig.progress.emit(0)
        if is_mailru_url(raw):
            self._sig.status.emit("Загружаю плейлист Mail.ru...")
        elif want_playlists:
            self._sig.status.emit(f"Ищу плейлисты «{raw}» на Mail.ru...")
        else:
            self._sig.status.emit(f"Ищу треки «{raw}» на Mail.ru...")

        def _worker():
            try:
                if is_mailru_url(raw):
                    items, owner = _fetch_tracks(raw)
                    hint = title_hint or owner or "Плейлист"
                    self._sig.results_ready.emit(tab, items, "tracks", hint)
                    self._sig.status.emit(f"Найдено треков: {len(items)}" + (f" ({owner})" if owner else ""))
                elif want_playlists:
                    items = search_mailru_playlists(raw, max_results=max_res)
                    self._sig.results_ready.emit(tab, items, "playlists", raw)
                    self._sig.status.emit(f"Найдено плейлистов: {len(items)}")
                else:
                    items = search_mailru_tracks(raw, max_results=max_res)
                    self._sig.results_ready.emit(tab, items, "tracks", raw)
                    self._sig.status.emit(f"Найдено треков: {len(items)}")
            except Exception as e:
                log_message(f"ERROR MailRuPage fetch: {e}")
                self._sig.error.emit(f"Ошибка Mail.ru:\n{e}")
                self._sig.status.emit("Ошибка поиска Mail.ru")
            finally:
                self._sig.progress.emit(100)
                self._sig.busy.emit(False)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_results_ready(self, tab: _MailRuTab, items: list, kind: str, title_hint: str):
        if self.tabs.indexOf(tab) < 0:
            return
        tab.populate(items, kind)
        self._update_tab_title(tab, title_hint)
        self._save_tabs()

    def _on_tab_action(self):
        tab = self._cur_tab()
        if tab.kind == "playlists":
            rows = sorted({idx.row() for idx in tab.table.selectedIndexes()})
            if not rows:
                return
            it = tab.table.item(rows[0], 0)
            data = it.data(Qt.ItemDataRole.UserRole) if it else None
            if data and data.get("url"):
                self.open_url(data["url"], title_hint=data.get("title", ""))
            return
        self._on_download()

    def _show_ctx_menu(self, pos):
        tab = self._cur_tab()
        if tab.table.rowCount() == 0:
            return
        rows = sorted({idx.row() for idx in tab.table.selectedIndexes()})
        first_data = None
        if rows:
            it0 = tab.table.item(rows[0], 0)
            if it0:
                first_data = it0.data(Qt.ItemDataRole.UserRole)
        has_owner_url = isinstance(first_data, dict) and bool(first_data.get("owner_url"))

        menu = QMenu(self)
        act_play = None
        if tab.kind != "playlists":
            act_play = menu.addAction("▶ Прослушать")
            act_play.setEnabled(isinstance(first_data, dict) and bool(first_data.get("url")))
        act_main = menu.addAction("📋 Открыть плейлист" if tab.kind == "playlists" else "⬇ Скачать выбранные")
        act_copy_name = menu.addAction("Копировать название")
        act_copy_url = menu.addAction("Копировать ссылку")
        menu.addSeparator()
        act_open_owner = menu.addAction("🌐 Открыть владельца в браузере")
        act_open_owner.setEnabled(has_owner_url)
        act_copy_owner = menu.addAction("Копировать ссылку на владельца")
        act_copy_owner.setEnabled(has_owner_url)

        act = menu.exec(tab.table.viewport().mapToGlobal(pos))
        if act_play is not None and act == act_play and isinstance(first_data, dict) and first_data.get("url"):
            title = f"{first_data.get('artist', '')} — {first_data.get('title', '')}".strip(" —") or "Mail.ru"
            w = self.window()
            if w is not None and hasattr(w, "play_audio_preview"):
                w.play_audio_preview(title, first_data["url"], headers={"Referer": "https://my.mail.ru/"})
        elif act == act_main:
            self._on_tab_action()
        elif act == act_open_owner and has_owner_url:
            import webbrowser
            webbrowser.open(first_data["owner_url"])
            self.status_label_set(f"Открыт профиль: {first_data['owner_url']}")
        elif act in (act_copy_name, act_copy_url, act_copy_owner):
            from PyQt6.QtWidgets import QApplication
            lines = []
            for r in rows:
                it = tab.table.item(r, 0)
                d = it.data(Qt.ItemDataRole.UserRole) if it else None
                if not isinstance(d, dict):
                    continue
                if act == act_copy_url and d.get("url"):
                    lines.append(d["url"])
                elif act == act_copy_owner and d.get("owner_url"):
                    lines.append(d["owner_url"])
                elif act == act_copy_name:
                    s = f"{d.get('artist', '')} — {d.get('title', '')}".strip(" —")
                    if s:
                        lines.append(s)
            if lines:
                QApplication.clipboard().setText("\n".join(lines))
                self.status_label_set(f"Скопировано: {len(lines)}")

    def _on_download(self):
        if self._busy:
            return
        tab = self._cur_tab()
        rows = sorted({idx.row() for idx in tab.table.selectedIndexes()})
        if not rows:
            QMessageBox.information(self, "Mail.ru", "Выберите треки для скачивания.")
            return
        picked = []
        for r in rows:
            it = tab.table.item(r, 0)
            if it:
                d = it.data(Qt.ItemDataRole.UserRole)
                if isinstance(d, dict) and d.get("url"):
                    picked.append(d)
        if not picked:
            return

        self._sig.busy.emit(True)
        self._sig.progress.emit(0)
        folder = settings.get("download_folder") or os.path.expanduser("~")

        def _worker():
            import requests
            ok_cnt = 0
            total = len(picked)
            for idx, t in enumerate(picked, 1):
                artist = t.get("artist", "")
                title = t.get("title", "track")
                url = t.get("url", "")
                self._sig.status.emit(f"Скачиваю {idx}/{total}: {artist} - {title}")
                self._sig.progress.emit(int((idx - 1) / max(total, 1) * 100))
                base = _utils.safe_filename(f"{artist} - {title}".strip(" -"), default="track")
                path = os.path.join(folder, f"{base}.mp3")
                k = 2
                while os.path.exists(path):
                    path = os.path.join(folder, f"{base} ({k}).mp3")
                    k += 1
                try:
                    r = requests.get(
                        url,
                        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://my.mail.ru/"},
                        timeout=30,
                        stream=True,
                    )
                    r.raise_for_status()
                    with open(path, "wb") as f:
                        for chunk in r.iter_content(65536):
                            if chunk:
                                f.write(chunk)
                    ok_cnt += 1
                    log_message(f"SUCCESS Mail.ru: {os.path.basename(path)}")
                    try:
                        from download_history import add_to_history
                        add_to_history(url=url, title=f"[Mail.ru] {base}", format_type="MP3")
                    except Exception:
                        pass
                except Exception as e:
                    log_message(f"ERROR Mail.ru download '{base}': {e}")
            self._sig.progress.emit(100)
            self._sig.status.emit(f"Готово: скачано {ok_cnt} из {total}")
            self._sig.busy.emit(False)

        threading.Thread(target=_worker, daemon=True).start()

    def _save_tabs(self):
        try:
            from config import atomic_write_json
            cur_idx = self.tabs.currentIndex()
            data = []
            for i in range(self.tabs.count()):
                t = self.tabs.widget(i)
                if not isinstance(t, _MailRuTab) or not t.items:
                    continue
                data.append({
                    "query": t.query,
                    "kind": t.kind,
                    "title": self.tabs.tabText(i),
                    "items": t.items,
                    "active": i == cur_idx,
                })
            atomic_write_json(_MAILRU_TABS_FILE, data, indent=2)
        except Exception as e:
            log_message(f"WARNING Mail.ru save tabs: {e}")

    def _restore_tabs(self):
        if not os.path.exists(_MAILRU_TABS_FILE):
            return
        try:
            import json
            with open(_MAILRU_TABS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not data:
                return
            if self.tabs.count() == 1:
                w0 = self.tabs.widget(0)
                if isinstance(w0, _MailRuTab) and not w0.items:
                    self.tabs.removeTab(0)
                    w0.deleteLater()
            active_idx = 0
            for td in data:
                items = td.get("items") or []
                if not items:
                    continue
                tab = self._new_tab(td.get("query", ""), kind=td.get("kind", "tracks"))
                tab.populate(items, td.get("kind", "tracks"))
                self._update_tab_title(tab)
                if td.get("active"):
                    active_idx = self.tabs.indexOf(tab)
            if self.tabs.count() == 0:
                self._new_tab("")
            self.tabs.setCurrentIndex(active_idx)
            self.url_input.setText(self._cur_tab().query)
        except Exception as e:
            log_message(f"WARNING Mail.ru restore tabs: {e}")


# ── Раздел: По ссылкам (1 ссылка или пачка, умная маршрутизация) ─────────────

class LinksPage(QWidget):
    """Универсальная загрузка по одной или нескольким ссылкам (YouTube, VK, OK, Mail.ru, НГ)."""

    def __init__(self, on_open_yt_list=None, on_open_mailru=None):
        super().__init__()
        self._on_open_yt_list = on_open_yt_list
        self._on_open_mailru = on_open_mailru
        self._setup_ui()

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)

        hdr = QLabel("Загрузка по ссылкам (одиночная или пачкой)")
        f = QFont()
        f.setPointSize(12)
        f.setBold(True)
        hdr.setFont(f)
        lay.addWidget(hdr)

        hint = QLabel(
            "Вставьте одну или несколько ссылок (по одной в строке или через пробел).\n"
            "Поддерживаются: YouTube (видео, каналы, плейлисты), ВКонтакте, Одноклассники, Mail.ru, Неизвестный Гений."
        )
        hint.setStyleSheet(f"color: {COLORS['text_secondary']};")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        self.text_edit = QTextEdit()
        self.text_edit.setPlaceholderText(
            "https://youtu.be/...\n"
            "https://www.youtube.com/@channel/videos\n"
            "https://vkvideo.ru/video-...\n"
            "https://my.mail.ru/music/playlists/..."
        )
        lay.addWidget(self.text_edit, 1)

        btns = QHBoxLayout()
        btn_paste = QPushButton("Вставить из буфера")
        btn_paste.setProperty("secondary", True)
        btn_paste.clicked.connect(self._paste)
        btns.addWidget(btn_paste)

        btn_clear = QPushButton("Очистить")
        btn_clear.setProperty("secondary", True)
        btn_clear.clicked.connect(self.text_edit.clear)
        btns.addWidget(btn_clear)

        btns.addStretch()

        self.btn_go = QPushButton("Запустить загрузку")
        self.btn_go.clicked.connect(self._run)
        btns.addWidget(self.btn_go)
        lay.addLayout(btns)

        self.status_lbl = QLabel("")
        lay.addWidget(self.status_lbl)

    def _paste(self):
        from PyQt6.QtWidgets import QApplication
        txt = QApplication.clipboard().text()
        if txt:
            cur = self.text_edit.toPlainText().strip()
            self.text_edit.setPlainText((cur + "\n" + txt).strip() if cur else txt)

    def _run(self):
        raw = self.text_edit.toPlainText().strip()
        if not raw:
            self.status_lbl.setText("Вставьте хотя бы одну ссылку.")
            return
        urls, seen = [], set()
        for part in re.split(r"\s+", raw):
            u = part.strip()
            if u and u.lower().startswith(("http://", "https://")) and u not in seen:
                seen.add(u)
                urls.append(u)
        if not urls:
            QMessageBox.warning(self, "Ссылки", "Не найдено ни одной корректной ссылки (http:// или https://).")
            return

        queued = 0
        opened_tabs = 0
        for u in urls:
            low = u.lower()
            if "my.mail.ru/music/" in low and self._on_open_mailru:
                self._on_open_mailru(u)
                opened_tabs += 1
            elif ("youtube.com" in low or "youtu.be" in low) and "playlist?list=" in low and self._on_open_yt_list:
                self._on_open_yt_list(u, "playlist")
                opened_tabs += 1
            elif ("youtube.com" in low) and any(p in low for p in ("/channel/", "/c/", "/user/", "/@")) and self._on_open_yt_list:
                self._on_open_yt_list(u, "channel")
                opened_tabs += 1
            else:
                if add_to_queue(u):
                    queued += 1

        if queued > 0 and not _utils.is_download_active():
            threading.Thread(target=process_queue, daemon=True).start()

        parts = []
        if queued:
            parts.append(f"в очередь добавлено: {queued}")
        if opened_tabs:
            parts.append(f"открыто вкладок: {opened_tabs}")
        msg = "Готово — " + ", ".join(parts) if parts else "Ссылки уже были в очереди."
        self.status_lbl.setText(msg)
        log_message(f"INFO LinksPage: {msg}")


# ── Раздел: История загрузок (с живым поиском и фильтром) ────────────────────

class HistoryPage(QWidget):
    """Улучшенная история загрузок с поиском, фильтром по формату и открытием папки."""

    def __init__(self):
        super().__init__()
        self._entries = []
        self._last_mtime = -1.0
        self._setup_ui()
        self.reload(force=True)

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        top = QHBoxLayout()
        hdr = QLabel("История загрузок")
        f = QFont()
        f.setPointSize(12)
        f.setBold(True)
        hdr.setFont(f)
        top.addWidget(hdr)
        top.addStretch()

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Поиск по названию...")
        self.search_input.setMaximumWidth(260)
        self.search_input.textChanged.connect(self._apply_filter)
        top.addWidget(self.search_input)

        self.source_combo = QComboBox()
        self.source_combo.addItems(["Все источники", "YouTube", "ВКонтакте", "Одноклассники", "Mail.ru"])
        self.source_combo.currentTextChanged.connect(self._apply_filter)
        top.addWidget(self.source_combo)

        self.fmt_combo = QComboBox()
        self.fmt_combo.addItems(["Все форматы", "MP4", "MP3"])
        self.fmt_combo.currentTextChanged.connect(self._apply_filter)
        top.addWidget(self.fmt_combo)

        btn_refresh = QPushButton("Обновить")
        btn_refresh.setProperty("secondary", True)
        btn_refresh.clicked.connect(lambda: self.reload(force=True))
        top.addWidget(btn_refresh)
        lay.addLayout(top)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Дата", "Источник", "Название", "Формат", "Длительность"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.doubleClicked.connect(lambda _idx: self._download_selected())
        hh = self.table.horizontalHeader()
        # Фиксированные/интерактивные ширины вместо ResizeToContents —
        # иначе на 1000+ строк Qt пересчитывает ширину O(N^2) и зависает!
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(0, 140)
        self.table.setColumnWidth(1, 105)
        self.table.setColumnWidth(3, 68)
        self.table.setColumnWidth(4, 90)
        _styles.bind_table_column_widths(self.table, "history_table_v2")
        lay.addWidget(self.table, 1)

        btns = QHBoxLayout()
        btn_dl = QPushButton("Скачать выбранные")
        btn_dl.clicked.connect(self._download_selected)
        btns.addWidget(btn_dl)

        btn_folder = QPushButton("Открыть папку загрузок")
        btn_folder.setProperty("secondary", True)
        btn_folder.clicked.connect(self._open_folder)
        btns.addWidget(btn_folder)

        btns.addStretch()
        self.count_lbl = QLabel("")
        btns.addWidget(self.count_lbl)
        lay.addLayout(btns)

    @staticmethod
    def _detect_source(entry: dict) -> str:
        src = entry.get("source")
        if src:
            return src
        title = str(entry.get("title", ""))
        url = str(entry.get("url", "")).lower()
        if title.startswith("[VK]") or "vk.com" in url or "vkvideo.ru" in url:
            return "ВКонтакте"
        if title.startswith("[OK]") or "ok.ru" in url or "odnoklassniki.ru" in url:
            return "Одноклассники"
        if title.startswith("[Mail.ru]") or "mail.ru" in url:
            return "Mail.ru"
        return "YouTube"

    @staticmethod
    def _clean_title(title: str) -> str:
        for pref in ("[VK] ", "[OK] ", "[Mail.ru] "):
            if title.startswith(pref):
                return title[len(pref):]
        return title

    def set_source_filter(self, source_name: str):
        idx = self.source_combo.findText(source_name)
        if idx >= 0:
            self.source_combo.setCurrentIndex(idx)

    def reload(self, force: bool = False):
        from download_history import DOWNLOAD_HISTORY_FILE
        vk_hist_file = os.path.join(os.getcwd(), "vk_history.json")
        try:
            m1 = os.path.getmtime(DOWNLOAD_HISTORY_FILE) if os.path.exists(DOWNLOAD_HISTORY_FILE) else 0.0
            m2 = os.path.getmtime(vk_hist_file) if os.path.exists(vk_hist_file) else 0.0
            mtime = m1 + m2
        except OSError:
            mtime = 0.0
        if not force and mtime == self._last_mtime and self._entries:
            return
        self._last_mtime = mtime
        entries = list(load_download_history())
        seen = {(e.get("date", "")[:16], self._clean_title(str(e.get("title", "")))) for e in entries}
        # Подхватываем старые записи из vk_history.json, чтобы они не терялись
        if os.path.exists(vk_hist_file):
            try:
                import json
                with open(vk_hist_file, "r", encoding="utf-8") as f:
                    vk_recs = json.load(f) or []
                for r in vk_recs:
                    artist = r.get("artist", "")
                    title = r.get("title", "")
                    label = f"{artist} - {title}".strip(" -") or os.path.basename(r.get("path", ""))
                    dt = r.get("date", "")
                    key = (dt[:16], label)
                    if key in seen:
                        continue
                    seen.add(key)
                    entries.append({
                        "date": dt,
                        "title": label,
                        "format": "MP3",
                        "duration": 0,
                        "url": r.get("path", ""),
                        "source": "ВКонтакте",
                    })
            except Exception:
                pass
        entries.sort(key=lambda e: str(e.get("date", "")), reverse=True)
        self._entries = entries
        self._apply_filter()

    def _apply_filter(self):
        q = self.search_input.text().strip().lower()
        src_filter = self.source_combo.currentText()
        fmt = self.fmt_combo.currentText()
        filtered = []
        for entry in self._entries:
            title = self._clean_title(str(entry.get("title", "Без названия")))
            src = self._detect_source(entry)
            rfmt = str(entry.get("format", "N/A")).upper()
            if q and q not in title.lower():
                continue
            if src_filter != "Все источники" and src != src_filter:
                continue
            if fmt in ("MP4", "MP3") and rfmt != fmt:
                continue
            filtered.append((entry, src, title, rfmt))

        self.table.setUpdatesEnabled(False)
        try:
            self.table.setRowCount(len(filtered))
            for row, (entry, src, title, rfmt) in enumerate(filtered):
                date = entry.get("date", "N/A")
                dur_s = entry.get("duration", 0)
                dur = format_invidious_duration(dur_s) if dur_s else "—"
                it0 = QTableWidgetItem(date)
                it0.setData(Qt.ItemDataRole.UserRole, entry.get("url", ""))
                self.table.setItem(row, 0, it0)
                self.table.setItem(row, 1, QTableWidgetItem(src))
                self.table.setItem(row, 2, QTableWidgetItem(title))
                self.table.setItem(row, 3, QTableWidgetItem(rfmt))
                self.table.setItem(row, 4, QTableWidgetItem(dur))
        finally:
            self.table.setUpdatesEnabled(True)
        self.count_lbl.setText(f"Показано: {len(filtered)} из {len(self._entries)}")

    def _download_selected(self):
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()})
        if not rows:
            QMessageBox.information(self, "История", "Выберите хотя бы одно видео.")
            return
        added = 0
        for r in rows:
            it = self.table.item(r, 0)
            url = it.data(Qt.ItemDataRole.UserRole) if it else ""
            if url and add_to_queue(url):
                added += 1
        if added > 0:
            if not _utils.is_download_active():
                threading.Thread(target=process_queue, daemon=True).start()
            QMessageBox.information(self, "Очередь", f"Добавлено в очередь: {added}")

    def _open_folder(self):
        folder = settings.get("download_folder") or os.path.expanduser("~")
        if os.path.isdir(folder):
            os.startfile(folder)


# ── Раздел: Настройки + Обновления утилит ────────────────────────────────────

class SettingsPage(QWidget):
    """Единый экран настроек приложения и обновлений утилит."""

    settings_changed = pyqtSignal()

    def __init__(self, updater_widget: QWidget):
        super().__init__()
        self._updater = updater_widget
        self._setup_ui()

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)

        tabs = QTabWidget()

        # Вкладка 1: Основные настройки
        gen = QWidget()
        gl = QVBoxLayout(gen)
        gl.setContentsMargins(14, 14, 14, 14)
        gl.setSpacing(12)

        # Папка загрузок
        r_dir = QHBoxLayout()
        r_dir.addWidget(QLabel("Папка загрузок:"))
        self.dir_edit = QLineEdit(settings.get("download_folder", ""))
        self.dir_edit.setReadOnly(True)
        r_dir.addWidget(self.dir_edit, 1)
        btn_browse = QPushButton("Выбрать...")
        btn_browse.clicked.connect(self._choose_folder)
        r_dir.addWidget(btn_browse)
        gl.addLayout(r_dir)

        # Формат и качество
        r_fmt = QHBoxLayout()
        r_fmt.addWidget(QLabel("Формат:"))
        self.fmt_combo = QComboBox()
        self.fmt_combo.addItems(["mp4", "mp3"])
        self.fmt_combo.setCurrentText(settings.get("download_format", "mp4"))
        self.fmt_combo.currentTextChanged.connect(self._save)
        r_fmt.addWidget(self.fmt_combo)

        r_fmt.addSpacing(20)
        r_fmt.addWidget(QLabel("Качество видео:"))
        self.qual_combo = QComboBox()
        self.qual_combo.addItems(["1080p", "720p", "480p"])
        self.qual_combo.setCurrentText(settings.get("video_quality", "1080p"))
        self.qual_combo.currentTextChanged.connect(self._save)
        r_fmt.addWidget(self.qual_combo)

        r_fmt.addSpacing(20)
        r_fmt.addWidget(QLabel("Браузер для cookies YouTube:"))
        self.browser_combo = QComboBox()
        self.browser_combo.addItems(["firefox", "chrome", "edge", ""])
        self.browser_combo.setCurrentText(settings.get("cookies_browser", "firefox"))
        self.browser_combo.currentTextChanged.connect(self._save)
        r_fmt.addWidget(self.browser_combo)
        r_fmt.addStretch()
        gl.addLayout(r_fmt)

        # Чекбоксы поведения
        self.chk_auto = QCheckBox("Автоперехват ссылок YouTube из буфера обмена")
        self.chk_auto.setChecked(settings.get("auto_capture_enabled", True))
        self.chk_auto.toggled.connect(self._save)
        gl.addWidget(self.chk_auto)

        self.chk_conv = QCheckBox("Включить конвертацию (MP3 / MP4)")
        self.chk_conv.setChecked(settings.get("conversion_enabled", True))
        self.chk_conv.toggled.connect(self._save)
        gl.addWidget(self.chk_conv)

        self.chk_desc = QCheckBox("Скачивать с описаниями (сохранять .txt рядом)")
        self.chk_desc.setChecked(settings.get("download_with_description", False))
        self.chk_desc.toggled.connect(self._save)
        gl.addWidget(self.chk_desc)

        self.chk_save_exit = QCheckBox("Сохранять настройки при выходе")
        self.chk_save_exit.setChecked(settings.get("save_settings_on_exit", True))
        self.chk_save_exit.toggled.connect(self._save)
        gl.addWidget(self.chk_save_exit)

        # Источники поиска YouTube (перенесены с экрана поиска для чистоты)
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {COLORS['border']};")
        gl.addWidget(sep)

        yt_hdr = QLabel("YouTube API / Invidious (необязательно — поиск работает и через yt-dlp)")
        yt_hdr.setStyleSheet(f"color: {COLORS['text_secondary']}; font-weight: bold;")
        gl.addWidget(yt_hdr)

        r_api = QHBoxLayout()
        r_api.addWidget(QLabel("YouTube API Key:"))
        self.api_key_edit = QLineEdit(settings.get("youtube_api_key", ""))
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText("AIza... (опционально)")
        self.api_key_edit.editingFinished.connect(self._save)
        r_api.addWidget(self.api_key_edit, 1)
        gl.addLayout(r_api)

        r_inv = QHBoxLayout()
        r_inv.addWidget(QLabel("Invidious URL:"))
        self.inv_edit = QLineEdit(settings.get("invidious_url", "http://localhost:3000"))
        self.inv_edit.setPlaceholderText("http://localhost:3000")
        self.inv_edit.editingFinished.connect(self._save)
        r_inv.addWidget(self.inv_edit, 1)
        gl.addLayout(r_inv)

        gl.addStretch()
        tabs.addTab(gen, "Основные настройки")
        tabs.addTab(self._updater, "Обновления утилит")
        lay.addWidget(tabs, 1)

    def sync_from_settings(self):
        self.dir_edit.setText(settings.get("download_folder", ""))
        for w in (self.fmt_combo, self.qual_combo, self.browser_combo,
                  self.chk_auto, self.chk_conv, self.chk_desc, self.chk_save_exit,
                  self.api_key_edit, self.inv_edit):
            w.blockSignals(True)
        self.fmt_combo.setCurrentText(settings.get("download_format", "mp4"))
        self.qual_combo.setCurrentText(settings.get("video_quality", "1080p"))
        self.browser_combo.setCurrentText(settings.get("cookies_browser", "firefox"))
        self.chk_auto.setChecked(settings.get("auto_capture_enabled", True))
        self.chk_conv.setChecked(settings.get("conversion_enabled", True))
        self.chk_desc.setChecked(settings.get("download_with_description", False))
        self.chk_save_exit.setChecked(settings.get("save_settings_on_exit", True))
        self.api_key_edit.setText(settings.get("youtube_api_key", ""))
        self.inv_edit.setText(settings.get("invidious_url", "http://localhost:3000"))
        for w in (self.fmt_combo, self.qual_combo, self.browser_combo,
                  self.chk_auto, self.chk_conv, self.chk_desc, self.chk_save_exit,
                  self.api_key_edit, self.inv_edit):
            w.blockSignals(False)

    def _choose_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Выберите папку для загрузки",
            settings.get("download_folder", os.path.expanduser("~"))
        )
        if folder:
            self.dir_edit.setText(folder)
            settings["download_folder"] = folder
            save_settings(settings)
            log_message(f"INFO Папка загрузки: {folder}")
            self.settings_changed.emit()

    def _save(self):
        settings["download_format"] = self.fmt_combo.currentText()
        settings["video_quality"] = self.qual_combo.currentText()
        settings["cookies_browser"] = self.browser_combo.currentText()
        settings["auto_capture_enabled"] = self.chk_auto.isChecked()
        settings["conversion_enabled"] = self.chk_conv.isChecked()
        settings["download_with_description"] = self.chk_desc.isChecked()
        settings["save_settings_on_exit"] = self.chk_save_exit.isChecked()
        settings["youtube_api_key"] = self.api_key_edit.text().strip()
        settings["invidious_url"] = self.inv_edit.text().strip() or "http://localhost:3000"
        save_settings(settings)
        self.settings_changed.emit()


# ── Встроенный мини-плеер для предпрослушивания аудио ────────────────────────

class AudioPreviewBar(QFrame):
    """Компактная полоса предпрослушивания треков на базе PyQt6.QtMultimedia."""

    _fallback_ready = pyqtSignal(int, str, str)  # (req_id, local_path, error_msg)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(38)
        self.setVisible(False)
        self._seeking = False
        self._req_id = 0
        self._cur_url = ""
        self._cur_headers = None
        self._used_fallback = False
        self._player = None
        self._audio = None
        self._fallback_ready.connect(self._on_fallback_ready)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 3, 12, 3)
        lay.setSpacing(8)

        self.btn_play = QPushButton("▶")
        self.btn_play.setObjectName("historyBtn")
        self.btn_play.setProperty("secondary", True)
        self.btn_play.setFixedSize(26, 26)
        self.btn_play.setToolTip("Воспроизвести / Пауза")
        self.btn_play.clicked.connect(self._toggle_play)
        lay.addWidget(self.btn_play)

        self.btn_stop = QPushButton("⏹")
        self.btn_stop.setObjectName("historyBtn")
        self.btn_stop.setProperty("secondary", True)
        self.btn_stop.setFixedSize(26, 26)
        self.btn_stop.setToolTip("Остановить")
        self.btn_stop.clicked.connect(self.stop)
        lay.addWidget(self.btn_stop)

        self.title_lbl = QLabel("")
        self.title_lbl.setMinimumWidth(120)
        self.title_lbl.setMaximumWidth(320)
        lay.addWidget(self.title_lbl)

        self.seek_slider = QSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setRange(0, 0)
        self.seek_slider.sliderPressed.connect(self._on_seek_press)
        self.seek_slider.sliderReleased.connect(self._on_seek_release)
        self.seek_slider.sliderMoved.connect(self._on_seek_moved)
        lay.addWidget(self.seek_slider, 1)

        self.time_lbl = QLabel("00:00 / 00:00")
        self.time_lbl.setProperty("secondary", True)
        self.time_lbl.setFixedWidth(86)
        self.time_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.time_lbl)

        vol_icon = QLabel("🔊")
        vol_icon.setStyleSheet("border: none;")
        lay.addWidget(vol_icon)

        self.vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setFixedWidth(72)
        self.vol_slider.setValue(int(settings.get("preview_volume", 70)))
        self.vol_slider.setToolTip("Громкость предпрослушивания")
        self.vol_slider.valueChanged.connect(self._on_volume_changed)
        lay.addWidget(self.vol_slider)

        self.btn_close = QPushButton("×")
        self.btn_close.setObjectName("historyBtn")
        self.btn_close.setProperty("secondary", True)
        self.btn_close.setFixedSize(24, 24)
        self.btn_close.setToolTip("Закрыть мини-плеер")
        self.btn_close.clicked.connect(self.close_bar)
        lay.addWidget(self.btn_close)

        self.apply_theme()

    def apply_theme(self):
        self.setStyleSheet(
            f"AudioPreviewBar {{ background-color: {COLORS['bg_secondary']}; "
            f"border-top: 1px solid {COLORS['border_strong']}; }}"
        )
        self.title_lbl.setStyleSheet(
            f"border: none; font-weight: 600; color: {COLORS['text_primary']};"
        )

    def _ensure_player(self) -> bool:
        if self._player is not None:
            return True
        try:
            from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
            self._audio = QAudioOutput(self)
            self._audio.setVolume(self.vol_slider.value() / 100.0)
            self._player = QMediaPlayer(self)
            self._player.setAudioOutput(self._audio)
            self._player.positionChanged.connect(self._on_pos_changed)
            self._player.durationChanged.connect(self._on_dur_changed)
            self._player.playbackStateChanged.connect(self._on_state_changed)
            self._player.errorOccurred.connect(self._on_error)
            return True
        except Exception as e:
            log_message(f"ERROR AudioPreviewBar init: {e}")
            return False

    @staticmethod
    def _fmt_ms(ms: int) -> str:
        s = max(0, int(ms // 1000))
        return f"{s // 60:02d}:{s % 60:02d}"

    def show_loading(self, title: str):
        """Показывает полосу плеера в состоянии подготовки потока."""
        self._req_id += 1
        short = (title[:55] + "…") if len(title) > 55 else title
        self.title_lbl.setText(f"🎵 {short}")
        self.title_lbl.setToolTip(title)
        self.time_lbl.setText("Загрузка...")
        self.seek_slider.setRange(0, 0)
        self.setVisible(True)
        return self._req_id

    def play_url(self, title: str, url: str, headers: dict | None = None, req_id: int | None = None):
        """Запускает воспроизведение HTTP-потока или локального файла."""
        if req_id is not None and req_id != self._req_id:
            return
        if not url or not self._ensure_player():
            return
        if req_id is None:
            self._req_id += 1
        self._cur_url = url
        self._cur_headers = headers
        self._used_fallback = False

        short = (title[:55] + "…") if len(title) > 55 else title
        self.title_lbl.setText(f"🎵 {short}")
        self.title_lbl.setToolTip(title)
        self.time_lbl.setText("Загрузка...")
        self.seek_slider.setRange(0, 0)
        self.setVisible(True)

        self._player.stop()
        if os.path.isfile(url):
            self._player.setSource(QUrl.fromLocalFile(url))
        else:
            self._player.setSource(QUrl(url))
        self._player.play()

    def _on_error(self, _err, err_str: str):
        log_message(f"WARNING AudioPreviewBar stream error: {err_str}")
        # Если прямой поток не открылся (например, нужен Referer), пробуем скачать во временный файл
        if (not self._used_fallback) and self._cur_url.startswith(("http://", "https://")) and ".m3u8" not in self._cur_url:
            self._used_fallback = True
            rid = self._req_id
            url = self._cur_url
            headers = dict(self._cur_headers or {})
            headers.setdefault("User-Agent", "Mozilla/5.0")
            self.time_lbl.setText("Буфер...")

            def _dl():
                import tempfile
                import requests
                tmp_path = os.path.join(tempfile.gettempdir(), f"smarttubedl_preview_{rid}.mp3")
                try:
                    r = requests.get(url, headers=headers, timeout=20, stream=True)
                    r.raise_for_status()
                    with open(tmp_path, "wb") as f:
                        for chunk in r.iter_content(65536):
                            if rid != self._req_id:
                                return
                            if chunk:
                                f.write(chunk)
                    self._fallback_ready.emit(rid, tmp_path, "")
                except Exception as e:
                    self._fallback_ready.emit(rid, "", str(e))

            threading.Thread(target=_dl, daemon=True).start()
            return
        self.time_lbl.setText("Ошибка")

    def _on_fallback_ready(self, rid: int, local_path: str, err_msg: str):
        if rid != self._req_id:
            return
        if err_msg or not local_path:
            self.time_lbl.setText("Ошибка")
            log_message(f"ERROR AudioPreviewBar fallback: {err_msg}")
            return
        if self._player is not None:
            self._player.setSource(QUrl.fromLocalFile(local_path))
            self._player.play()

    def _toggle_play(self):
        if self._player is None:
            return
        from PyQt6.QtMultimedia import QMediaPlayer
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def stop(self):
        self._req_id += 1
        if self._player is not None:
            self._player.stop()
        self.btn_play.setText("▶")

    def close_bar(self):
        self.stop()
        self.setVisible(False)

    def _on_state_changed(self, state):
        from PyQt6.QtMultimedia import QMediaPlayer
        self.btn_play.setText("⏸" if state == QMediaPlayer.PlaybackState.PlayingState else "▶")

    def _on_dur_changed(self, dur_ms: int):
        self.seek_slider.setRange(0, max(0, dur_ms))
        self.time_lbl.setText(f"{self._fmt_ms(self.seek_slider.value())} / {self._fmt_ms(dur_ms)}")

    def _on_pos_changed(self, pos_ms: int):
        if not self._seeking:
            self.seek_slider.setValue(pos_ms)
            dur = self.seek_slider.maximum()
            self.time_lbl.setText(f"{self._fmt_ms(pos_ms)} / {self._fmt_ms(dur)}")

    def _on_seek_press(self):
        self._seeking = True

    def _on_seek_moved(self, val: int):
        self.time_lbl.setText(f"{self._fmt_ms(val)} / {self._fmt_ms(self.seek_slider.maximum())}")

    def _on_seek_release(self):
        self._seeking = False
        if self._player is not None:
            self._player.setPosition(self.seek_slider.value())

    def _on_volume_changed(self, val: int):
        if self._audio is not None:
            self._audio.setVolume(val / 100.0)
        settings["preview_volume"] = val


# ── Единое главное окно SmartTubeDL ──────────────────────────────────────────

class MainWindow(QMainWindow):
    """Единое главное окно SmartTubeDL (UI v2): Sidebar + StackedWidget + Footer Bar."""

    IDX_YOUTUBE  = 0
    IDX_VK       = 1
    IDX_OK       = 2
    IDX_MAILRU   = 3
    IDX_LINKS    = 4
    IDX_QUEUE    = 5
    IDX_HISTORY  = 6
    IDX_SETTINGS = 7
    IDX_LOGS     = 8

    def __init__(self, app_ref):
        super().__init__()
        self.app_ref = app_ref
        self.setWindowTitle("SmartTubeDL")
        self.setMinimumSize(1020, 660)
        self.resize(1220, 760)

        self.youtube_page = None
        self.vk_page = None
        self.ok_page = None
        self.mailru_page = None
        self.links_page = None
        self.queue_page = None
        self.history_page = None
        self.updater_page = None
        self.settings_page = None
        self.logs_page = None

        self._setup_ui()

        # Обновление бейджа очереди и статуса паузы в футере
        self._footer_timer = QTimer(self)
        self._footer_timer.timeout.connect(self._refresh_footer_meta)
        self._footer_timer.start(1500)

    def _setup_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        # ── Левая боковая панель ─────────────────────────────────────────────
        self._sidebar_wrap = QFrame()
        self._sidebar_wrap.setFixedWidth(186)
        sb_lay = QVBoxLayout(self._sidebar_wrap)
        sb_lay.setContentsMargins(8, 12, 8, 10)
        sb_lay.setSpacing(6)

        self._brand_lbl = QLabel("  SmartTubeDL")
        bf = QFont()
        bf.setPointSize(11)
        bf.setBold(True)
        self._brand_lbl.setFont(bf)
        sb_lay.addWidget(self._brand_lbl)

        self.nav = QListWidget()
        self.nav.setCursor(Qt.CursorShape.PointingHandCursor)
        self._nav_items = [
            "▶  YouTube",
            "🎵  ВКонтакте",
            "🟠  Одноклассники",
            "✉  Mail.ru",
            "🔗  По ссылкам",
            "📥  Очередь",
            "🕘  История",
            "⚙  Настройки",
            "📋  Логи",
        ]
        for label in self._nav_items:
            self.nav.addItem(QListWidgetItem(label))
        self.nav.currentRowChanged.connect(self._on_nav_changed)
        sb_lay.addWidget(self.nav, 1)

        body.addWidget(self._sidebar_wrap)

        # ── Стек разделов ────────────────────────────────────────────────────
        self.stack = QStackedWidget()
        for _ in self._nav_items:
            self.stack.addWidget(QWidget())   # ленивые заглушки
        body.addWidget(self.stack, 1)

        outer.addLayout(body, 1)

        # ── Встроенный мини-плеер предпрослушивания (скрыт, пока не играет) ──
        self.preview_bar = AudioPreviewBar(self)
        outer.addWidget(self.preview_bar)

        # ── Постоянная нижняя панель (Footer Bar) ────────────────────────────
        self._footer = QFrame()
        self._footer.setFixedHeight(42)
        fl = QHBoxLayout(self._footer)
        fl.setContentsMargins(12, 4, 12, 4)
        fl.setSpacing(8)

        self.footer_status = QLabel("Ожидание...")
        fl.addWidget(self.footer_status, 1)

        self.footer_progress = QProgressBar()
        self.footer_progress.setFixedWidth(150)
        self.footer_progress.setFixedHeight(16)
        self.footer_progress.setVisible(False)
        fl.addWidget(self.footer_progress)

        self.btn_pause = QPushButton("⏸ Пауза")
        self.btn_pause.setProperty("secondary", True)
        self.btn_pause.setFixedHeight(28)
        self.btn_pause.clicked.connect(self._toggle_pause)
        fl.addWidget(self.btn_pause)

        self.quick_fmt = QComboBox()
        self.quick_fmt.addItems(["MP4 1080p", "MP4 720p", "MP4 480p", "MP3"])
        self.quick_fmt.setFixedHeight(28)
        self.quick_fmt.currentTextChanged.connect(self._on_quick_fmt_changed)
        fl.addWidget(self.quick_fmt)

        self.btn_folder = QPushButton()
        self.btn_folder.setProperty("secondary", True)
        self.btn_folder.setFixedHeight(28)
        self.btn_folder.setToolTip("Открыть папку загрузок в Проводнике")
        self.btn_folder.clicked.connect(self._open_download_folder)
        fl.addWidget(self.btn_folder)

        outer.addWidget(self._footer)

        self.apply_theme()
        self.sync_footer_settings()
        # По умолчанию открываем YouTube
        self.nav.setCurrentRow(self.IDX_YOUTUBE)

    def apply_theme(self):
        """Обновляет inline-стили боковой панели и футера под текущую палитру COLORS."""
        self._sidebar_wrap.setStyleSheet(
            f"QFrame {{ background-color: {COLORS['bg_secondary']}; "
            f"border-right: 1px solid {COLORS['border_strong']}; }}"
        )
        self._brand_lbl.setStyleSheet(
            f"color: {COLORS['accent']}; border: none; padding-bottom: 6px;"
        )
        self.nav.setStyleSheet(f"""
            QListWidget {{
                background: transparent;
                border: none;
                outline: none;
                font-size: 13px;
            }}
            QListWidget::item {{
                padding: 8px 10px;
                border-radius: 6px;
                border-left: 3px solid transparent;
                margin-bottom: 2px;
                color: {COLORS['text_primary']};
            }}
            QListWidget::item:hover:!selected {{
                background-color: {COLORS['bg_primary']};
            }}
            QListWidget::item:selected {{
                background-color: {COLORS['accent_light']};
                color: {COLORS['accent']};
                border-left: 3px solid {COLORS['accent']};
                font-weight: 600;
            }}
        """)
        self._footer.setStyleSheet(
            f"QFrame {{ background-color: {COLORS['bg_secondary']}; "
            f"border-top: 1px solid {COLORS['border_strong']}; }}"
        )
        self.footer_status.setStyleSheet(
            f"border: none; color: {COLORS['text_secondary']};"
        )
        if hasattr(self, "preview_bar"):
            self.preview_bar.apply_theme()

        # Единые горячие клавиши для всех разделов: Ctrl+F (фильтр), Ctrl+T (новая вкладка), Ctrl+W (закрыть вкладку)
        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(self._shortcut_focus_filter)
        QShortcut(QKeySequence("Ctrl+T"), self).activated.connect(self._shortcut_new_tab)
        QShortcut(QKeySequence("Ctrl+W"), self).activated.connect(self._shortcut_close_tab)

    def _shortcut_focus_filter(self):
        idx = self.stack.currentIndex()
        w = self.stack.widget(idx)
        target = None
        if idx == self.IDX_YOUTUBE and self.youtube_page:
            cur = self.youtube_page.tabs.currentWidget()
            target = getattr(cur, "filter_input", None)
        elif idx == self.IDX_VK and self.vk_page:
            cur = self.vk_page.tabs.currentWidget() if hasattr(self.vk_page, "tabs") else None
            target = getattr(cur, "filter_input", None) or getattr(self.vk_page, "filter_edit", None)
        elif idx == self.IDX_OK and self.ok_page:
            cur = self.ok_page.tabs.currentWidget() if hasattr(self.ok_page, "tabs") else self.ok_page
            target = getattr(cur, "filter_input", None) or getattr(self.ok_page, "filter_input", None)
        elif idx == self.IDX_MAILRU and self.mailru_page:
            target = self.mailru_page.filter_input
        elif idx == self.IDX_HISTORY and self.history_page:
            target = self.history_page.search_input
        if target is not None:
            target.setFocus()
            target.selectAll()

    def _shortcut_new_tab(self):
        idx = self.stack.currentIndex()
        if idx == self.IDX_YOUTUBE and self.youtube_page:
            self.youtube_page._new_tab("")
        elif idx == self.IDX_VK and self.vk_page and hasattr(self.vk_page, "_new_tab"):
            self.vk_page._new_tab("")
        elif idx == self.IDX_OK and self.ok_page and hasattr(self.ok_page, "_new_tab"):
            self.ok_page._new_tab("")
        elif idx == self.IDX_MAILRU and self.mailru_page:
            self.mailru_page._new_tab("")

    def _shortcut_close_tab(self):
        idx = self.stack.currentIndex()
        if idx == self.IDX_YOUTUBE and self.youtube_page and self.youtube_page.tabs.count() > 1:
            self.youtube_page._close_tab(self.youtube_page.tabs.currentIndex())
        elif idx == self.IDX_VK and self.vk_page and hasattr(self.vk_page, "tabs") and self.vk_page.tabs.count() > 1:
            self.vk_page._close_tab(self.vk_page.tabs.currentIndex())
        elif idx == self.IDX_OK and self.ok_page and hasattr(self.ok_page, "tabs") and self.ok_page.tabs.count() > 1:
            self.ok_page._close_tab(self.ok_page.tabs.currentIndex())
        elif idx == self.IDX_MAILRU and self.mailru_page and self.mailru_page.tabs.count() > 1:
            self.mailru_page._close_tab(self.mailru_page.tabs.currentIndex())

    def _ensure_page(self, idx: int) -> QWidget:
        """Лениво создаёт страницу раздела при первом обращении."""
        if idx == self.IDX_YOUTUBE and self.youtube_page is None:
            from ui_qt import SearchWindow
            w = SearchWindow()
            w.setWindowFlags(Qt.WindowType.Widget)
            self.youtube_page = w
            self._replace_stack_widget(idx, w)
        elif idx == self.IDX_VK and self.vk_page is None:
            from vk_window_qt import VKSearchWindow
            w = VKSearchWindow()
            w.setWindowFlags(Qt.WindowType.Widget)
            self.vk_page = w
            self._replace_stack_widget(idx, w)
        elif idx == self.IDX_OK and self.ok_page is None:
            from ok_window_qt import OKSearchWindow
            w = OKSearchWindow()
            w.setWindowFlags(Qt.WindowType.Widget)
            self.ok_page = w
            self._replace_stack_widget(idx, w)
        elif idx == self.IDX_MAILRU and self.mailru_page is None:
            w = MailRuPage()
            self.mailru_page = w
            self._replace_stack_widget(idx, w)
        elif idx == self.IDX_LINKS and self.links_page is None:
            w = LinksPage(
                on_open_yt_list=self.open_youtube_video_list,
                on_open_mailru=self.open_mailru_url,
            )
            self.links_page = w
            self._replace_stack_widget(idx, w)
        elif idx == self.IDX_QUEUE and self.queue_page is None:
            from queue_window_qt import QueueWindow
            w = QueueWindow()
            w.setWindowFlags(Qt.WindowType.Widget)
            self.queue_page = w
            self._replace_stack_widget(idx, w)
        elif idx == self.IDX_HISTORY and self.history_page is None:
            w = HistoryPage()
            self.history_page = w
            self._replace_stack_widget(idx, w)
        elif idx == self.IDX_SETTINGS and self.settings_page is None:
            from updater_qt import UpdaterWindow
            self.updater_page = UpdaterWindow()
            self.updater_page.setWindowFlags(Qt.WindowType.Widget)
            w = SettingsPage(self.updater_page)
            w.settings_changed.connect(self._on_settings_page_changed)
            self.settings_page = w
            self._replace_stack_widget(idx, w)
        elif idx == self.IDX_LOGS and self.logs_page is None:
            from debug_qt import DebugWindow
            w = DebugWindow()
            w.setWindowFlags(Qt.WindowType.Widget)
            self.logs_page = w
            self._replace_stack_widget(idx, w)
        return self.stack.widget(idx)

    def _replace_stack_widget(self, idx: int, widget: QWidget):
        old = self.stack.widget(idx)
        self.stack.insertWidget(idx, widget)
        if old is not None:
            self.stack.removeWidget(old)
            old.deleteLater()

    def _on_nav_changed(self, idx: int):
        if idx < 0:
            return
        self._ensure_page(idx)
        self.stack.setCurrentIndex(idx)
        if idx == self.IDX_HISTORY and self.history_page is not None:
            self.history_page.reload()
        elif idx == self.IDX_SETTINGS and self.settings_page is not None:
            self.settings_page.sync_from_settings()
        elif idx == self.IDX_QUEUE and self.queue_page is not None:
            if hasattr(self.queue_page, "_refresh"):
                self.queue_page._refresh()

    def switch_to(self, idx: int):
        """Открывает главное окно и переключает на указанный раздел."""
        self.nav.setCurrentRow(idx)
        self.show()
        self.raise_()
        self.activateWindow()

    def open_history(self, source: str = "Все источники"):
        """Открывает раздел «История» и при необходимости выставляет фильтр по источнику."""
        self._ensure_page(self.IDX_HISTORY)
        self.nav.setCurrentRow(self.IDX_HISTORY)
        if self.history_page is not None:
            self.history_page.reload(force=True)
            self.history_page.set_source_filter(source)
        self.show()
        self.raise_()
        self.activateWindow()

    def show_preview_loading(self, title: str) -> int:
        """Показывает мини-плеер в состоянии загрузки и возвращает ID запроса."""
        return self.preview_bar.show_loading(title)

    def play_audio_preview(self, title: str, url: str, headers: dict | None = None, req_id: int | None = None):
        """Воспроизводит аудиопоток или локальный файл во встроенном мини-плеере."""
        self.preview_bar.play_url(title, url, headers=headers, req_id=req_id)

    def open_youtube_video_list(self, url: str, mode: str = "channel", state: dict = None):
        """Открывает канал/плейлист как вкладку прямо внутри раздела YouTube."""
        self._ensure_page(self.IDX_YOUTUBE)
        self.nav.setCurrentRow(self.IDX_YOUTUBE)
        w = self.youtube_page.open_video_list_tab(url, mode=mode, state=state)
        self.show()
        self.raise_()
        self.activateWindow()
        return w

    def open_mailru_url(self, url: str):
        self._ensure_page(self.IDX_MAILRU)
        self.nav.setCurrentRow(self.IDX_MAILRU)
        self.mailru_page.open_url(url)
        self.show()
        self.raise_()
        self.activateWindow()

    def update_footer_status(self, status: str, progress: int = -1):
        speed = _utils.download_speed
        if status == "Загрузка..." and speed and speed != "0 KB/s":
            self.footer_status.setText(f"⬇ {status} ({speed})")
        else:
            self.footer_status.setText(status)
        if 0 <= progress < 100 and status not in ("Ожидание...", "На паузе", "Готово!"):
            self.footer_progress.setVisible(True)
            self.footer_progress.setValue(progress)
        else:
            self.footer_progress.setVisible(False)

    def sync_footer_settings(self):
        fmt = settings.get("download_format", "mp4").lower()
        qual = settings.get("video_quality", "1080p")
        label = "MP3" if fmt == "mp3" else f"MP4 {qual}"
        self.quick_fmt.blockSignals(True)
        idx = self.quick_fmt.findText(label)
        if idx >= 0:
            self.quick_fmt.setCurrentIndex(idx)
        self.quick_fmt.blockSignals(False)

        folder = settings.get("download_folder", "")
        short_dir = os.path.basename(folder.rstrip("\\/")) or folder
        self.btn_folder.setText(f"📁 {short_dir[:18]}")
        self.btn_folder.setToolTip(f"Открыть папку: {folder}")

    def _on_quick_fmt_changed(self, text: str):
        if text == "MP3":
            settings["download_format"] = "mp3"
        else:
            settings["download_format"] = "mp4"
            parts = text.split()
            if len(parts) == 2:
                settings["video_quality"] = parts[1]
        save_settings(settings)
        if self.app_ref and hasattr(self.app_ref, "tray"):
            self.app_ref.tray._set_format(settings["download_format"])
            if settings["download_format"] == "mp4":
                self.app_ref.tray._set_quality(settings["video_quality"])
        if self.settings_page:
            self.settings_page.sync_from_settings()

    def _on_settings_page_changed(self):
        self.sync_footer_settings()
        if self.app_ref and hasattr(self.app_ref, "tray"):
            self.app_ref.tray._set_format(settings.get("download_format", "mp4"))
            self.app_ref.tray._set_quality(settings.get("video_quality", "1080p"))
            self.app_ref.tray.action_auto.setChecked(settings.get("auto_capture_enabled", True))
            self.app_ref.tray.action_convert.setChecked(settings.get("conversion_enabled", True))
            self.app_ref.tray.action_desc.setChecked(settings.get("download_with_description", False))

    def _toggle_pause(self):
        if _utils.is_paused:
            _utils.is_paused = False
            threading.Thread(target=process_queue, daemon=True).start()
            self.btn_pause.setText("⏸ Пауза")
        elif _utils.is_download_active():
            _utils.stop_requested = True
            self.btn_pause.setText("▶ Продолжить")

    def _refresh_footer_meta(self):
        q_cnt = get_queue_count() + len(_utils.vk_queue)
        item = self.nav.item(self.IDX_QUEUE)
        if item is not None:
            item.setText(f"📥  Очередь ({q_cnt})" if q_cnt > 0 else "📥  Очередь")
        if _utils.is_paused:
            self.btn_pause.setText("▶ Продолжить")
            self.btn_pause.setEnabled(True)
        elif _utils.is_download_active():
            self.btn_pause.setText("⏸ Пауза")
            self.btn_pause.setEnabled(True)
        else:
            self.btn_pause.setText("⏸ Пауза")
            self.btn_pause.setEnabled(False)

    def _open_download_folder(self):
        folder = settings.get("download_folder") or os.path.expanduser("~")
        if os.path.isdir(folder):
            os.startfile(folder)

    def closeEvent(self, event):
        """Крестик сворачивает единое окно в трей, а не завершает приложение."""
        event.ignore()
        self.hide()
        _utils.request_state_save()
        log_message("INFO Главное окно свёрнуто в трей")
