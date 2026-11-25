"""
Раздел «Поиск музыки» — объединённый параллельный поиск по Mail.ru, YouTube,
ВКонтакте и Одноклассникам в единой таблице с предпрослушиванием и скачиванием.
"""

import json
import os
import re
import threading
import time
from urllib.parse import quote

from PyQt6.QtCore import Qt, QObject, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QCheckBox, QComboBox, QTabWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QProgressBar, QMenu, QMessageBox
)

import styles as _styles
import utils as _utils
from config import settings, save_settings, atomic_write_json, format_invidious_duration
from download_history import add_to_history
from logger import log_message
from queues import add_to_queue, process_queue


_TABS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "unified_music_tabs.json")

_SOURCE_LABELS = {
    "mailru": "Mail.ru",
    "youtube": "YouTube",
    "vk": "ВКонтакте",
    "ok": "Одноклассники",
}


class _UnifiedSignals(QObject):
    chunk_ready   = pyqtSignal(object, int, str, list)   # (tab, search_id, source_key, items)
    source_done   = pyqtSignal(object, int, str, str)    # (tab, search_id, source_key, note)
    status        = pyqtSignal(str)
    progress      = pyqtSignal(int)
    busy          = pyqtSignal(bool)
    preview_ready = pyqtSignal(str, str, dict, int)      # (title, url, headers, req_id)


def _split_artist_title(raw_title: str, fallback_author: str = "") -> tuple[str, str]:
    """Разбирает строку вида 'Исполнитель - Название' на (artist, title)."""
    txt = (raw_title or "").strip()
    for sep in (" — ", " – ", " - "):
        if sep in txt:
            left, right = txt.split(sep, 1)
            if left.strip() and right.strip():
                return left.strip(), right.strip()
    return (fallback_author or "").strip(), txt or "Без названия"


class _UnifiedMusicTab(QWidget):
    """Одна вкладка объединённого поиска музыки."""

    def __init__(self, query: str = "", on_download=None, on_ctx=None, on_play=None):
        super().__init__()
        self.query = query
        self.items: list[dict] = []
        self._search_id = 0

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(6)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Источник", "Исполнитель", "Название", "Длит.", "Битрейт / Канал"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(False)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.setSortingEnabled(True)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        if on_ctx:
            self.table.customContextMenuRequested.connect(on_ctx)
        if on_play:
            self.table.doubleClicked.connect(lambda idx: on_play(self, idx.row()))
        self.table.itemSelectionChanged.connect(self._update_sel)

        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(0, 105)
        self.table.setColumnWidth(1, 195)
        self.table.setColumnWidth(3, 65)
        self.table.setColumnWidth(4, 155)
        _styles.bind_table_column_widths(self.table, "unified_music_table")
        lay.addWidget(self.table, 1)

        # Нижняя панель вкладки: фильтр + фильтр источника + счётчик + Скачать
        bot = QHBoxLayout()
        bot.setContentsMargins(0, 0, 0, 0)
        bot.setSpacing(6)

        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("Фильтр по исполнителю, названию, каналу...")
        self.filter_input.textChanged.connect(self._apply_filter)
        bot.addWidget(self.filter_input, 1)

        self.src_filter = QComboBox()
        self.src_filter.addItems(["Все источники", "Mail.ru", "YouTube", "ВКонтакте", "Одноклассники"])
        self.src_filter.currentTextChanged.connect(self._apply_filter)
        bot.addWidget(self.src_filter)

        self.sel_lbl = QLabel("Выбрано: 0")
        self.sel_lbl.setProperty("secondary", True)
        bot.addWidget(self.sel_lbl)

        self.dl_btn = QPushButton("⬇ Скачать выбранные")
        self.dl_btn.setEnabled(False)
        if on_download:
            self.dl_btn.clicked.connect(on_download)
        bot.addWidget(self.dl_btn)

        lay.addLayout(bot)

    def clear_items(self):
        self.items = []
        self.table.setRowCount(0)
        self.dl_btn.setEnabled(False)
        self._update_sel()

    def append_items(self, new_items: list[dict]):
        if not new_items:
            return
        self.items.extend(new_items)
        was_sorting = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)
        self.table.setUpdatesEnabled(False)
        try:
            start = self.table.rowCount()
            self.table.setRowCount(start + len(new_items))
            for idx, item in enumerate(new_items):
                r = start + idx
                src_lbl = _SOURCE_LABELS.get(item.get("source", ""), item.get("source", ""))
                it0 = QTableWidgetItem(src_lbl)
                it0.setData(Qt.ItemDataRole.UserRole, dict(item))
                self.table.setItem(r, 0, it0)
                self.table.setItem(r, 1, QTableWidgetItem(str(item.get("artist", ""))))
                self.table.setItem(r, 2, QTableWidgetItem(str(item.get("title", ""))))
                self.table.setItem(r, 3, QTableWidgetItem(str(item.get("duration", ""))))
                self.table.setItem(r, 4, QTableWidgetItem(str(item.get("extra", ""))))
        finally:
            self.table.setUpdatesEnabled(True)
            self.table.setSortingEnabled(was_sorting)
        self.dl_btn.setEnabled(self.table.rowCount() > 0)
        self._apply_filter()

    def _update_sel(self):
        cnt = len({idx.row() for idx in self.table.selectedIndexes()})
        self.sel_lbl.setText(f"Выбрано: {cnt}")

    def _apply_filter(self):
        q = self.filter_input.text().strip().lower()
        src_f = self.src_filter.currentText()
        for r in range(self.table.rowCount()):
            it0 = self.table.item(r, 0)
            src_txt = it0.text() if it0 else ""
            if src_f != "Все источники" and src_txt != src_f:
                self.table.setRowHidden(r, True)
                continue
            if not q:
                self.table.setRowHidden(r, False)
                continue
            matched = False
            for c in range(5):
                it = self.table.item(r, c)
                if it and q in it.text().lower():
                    matched = True
                    break
            self.table.setRowHidden(r, not matched)


class UnifiedMusicSearchPage(QWidget):
    """Раздел «Поиск музыки»: параллельный поиск по всем площадкам."""

    def __init__(self, main_window_ref):
        super().__init__()
        self._mw = main_window_ref
        self._sig = _UnifiedSignals()
        self._restoring = True
        self._active_searches: dict[int, set[str]] = {}
        self._search_notes: dict[int, list[str]] = {}
        self._busy_dl = False

        self._sig.chunk_ready.connect(self._on_chunk_ready)
        self._sig.source_done.connect(self._on_source_done)
        self._sig.status.connect(self._set_status)
        self._sig.progress.connect(self._set_progress)
        self._sig.busy.connect(self._set_busy)
        self._sig.preview_ready.connect(self._on_preview_ready)

        try:
            self._setup_ui()
            self._restore_tabs()
        finally:
            self._restoring = False

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 8)
        lay.setSpacing(6)

        hdr = QLabel("Поиск музыки по всем площадкам")
        f = QFont()
        f.setPointSize(12)
        f.setBold(True)
        hdr.setFont(f)
        lay.addWidget(hdr)

        # Строка поиска + история запросов + Найти
        row = QHBoxLayout()
        row.setSpacing(6)
        self.query_input = QLineEdit()
        self.query_input.setPlaceholderText("Исполнитель или название песни...")
        self.query_input.returnPressed.connect(self._on_search)
        row.addWidget(self.query_input, 1)

        self._search_hist = _styles.attach_search_history(
            self.query_input, "unified_music_history", self._on_search
        )
        row.addWidget(self._search_hist.button)

        self.btn_search = QPushButton("Найти")
        self.btn_search.clicked.connect(self._on_search)
        row.addWidget(self.btn_search)
        lay.addLayout(row)

        # Чекбоксы источников
        saved_src = settings.get("unified_sources") or {
            "mailru": True, "youtube": True, "vk": False, "ok": False
        }
        src_row = QHBoxLayout()
        src_row.setSpacing(14)
        src_row.addWidget(QLabel("Источники:"))

        self.chk_mailru = QCheckBox("Mail.ru")
        self.chk_mailru.setChecked(bool(saved_src.get("mailru", True)))
        self.chk_mailru.toggled.connect(self._save_sources)
        src_row.addWidget(self.chk_mailru)

        self.chk_youtube = QCheckBox("YouTube")
        self.chk_youtube.setChecked(bool(saved_src.get("youtube", True)))
        self.chk_youtube.toggled.connect(self._save_sources)
        src_row.addWidget(self.chk_youtube)

        self.chk_vk = QCheckBox("ВКонтакте")
        self.chk_vk.setChecked(bool(saved_src.get("vk", False)))
        self.chk_vk.toggled.connect(self._on_vk_toggled)
        src_row.addWidget(self.chk_vk)

        self.chk_ok = QCheckBox("Одноклассники")
        self.chk_ok.setChecked(bool(saved_src.get("ok", False)))
        self.chk_ok.toggled.connect(self._on_ok_toggled)
        src_row.addWidget(self.chk_ok)

        src_row.addStretch()
        lay.addLayout(src_row)

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
        self.status_lbl = QLabel("Выберите источники и введите запрос (двойной клик по треку — прослушать)")
        self.status_lbl.setProperty("secondary", True)
        foot.addWidget(self.status_lbl, 1)
        lay.addLayout(foot)

    def _on_vk_toggled(self, checked: bool):
        self._save_sources()
        if checked and not self._restoring and self._mw is not None:
            if getattr(self._mw, "vk_page", None) is None:
                self._mw._ensure_page(self._mw.IDX_VK)

    def _on_ok_toggled(self, checked: bool):
        self._save_sources()
        if checked and not self._restoring and self._mw is not None:
            if getattr(self._mw, "ok_page", None) is None:
                self._mw._ensure_page(self._mw.IDX_OK)

    def _save_sources(self):
        settings["unified_sources"] = {
            "mailru": self.chk_mailru.isChecked(),
            "youtube": self.chk_youtube.isChecked(),
            "vk": self.chk_vk.isChecked(),
            "ok": self.chk_ok.isChecked(),
        }
        try:
            save_settings(settings)
        except Exception:
            pass

    def _cur_tab(self) -> _UnifiedMusicTab:
        w = self.tabs.currentWidget()
        if isinstance(w, _UnifiedMusicTab):
            return w
        return self._new_tab("")

    @property
    def filter_input(self) -> QLineEdit:
        return self._cur_tab().filter_input

    def _new_tab(self, query: str = "") -> _UnifiedMusicTab:
        tab = _UnifiedMusicTab(
            query=query,
            on_download=self._on_download,
            on_ctx=self._show_ctx_menu,
            on_play=self._preview_row,
        )
        short = (query[:22] + "…") if len(query) > 22 else (query or "Поиск")
        idx = self.tabs.addTab(tab, f"🔎 {short}")
        self.tabs.setCurrentIndex(idx)
        return tab

    def _close_tab(self, idx: int):
        if self.tabs.count() <= 1:
            t = self._cur_tab()
            t.query = ""
            t.clear_items()
            self.tabs.setTabText(0, "🔎 Поиск")
        else:
            w = self.tabs.widget(idx)
            self.tabs.removeTab(idx)
            if w:
                w.deleteLater()
        if not self._restoring:
            self._save_tabs()

    def _on_tab_changed(self, _idx: int):
        t = self.tabs.currentWidget()
        if isinstance(t, _UnifiedMusicTab) and t.query:
            self.query_input.setText(t.query)
        if not self._restoring:
            self._save_tabs()

    def _set_status(self, text: str):
        self.status_lbl.setText(text)

    def _set_progress(self, val: int):
        self.pbar.setRange(0, 100)
        self.pbar.setValue(max(0, min(100, val)))

    def _set_busy(self, busy: bool):
        self._busy_dl = busy
        self.pbar.setVisible(busy)
        if busy:
            self.pbar.setRange(0, 0)
        else:
            self.pbar.setRange(0, 100)

    # ── Параллельный поиск ───────────────────────────────────────────────────

    def _on_search(self):
        query = self.query_input.text().strip()
        if not query:
            return
        sources = []
        if self.chk_mailru.isChecked():
            sources.append("mailru")
        if self.chk_youtube.isChecked():
            sources.append("youtube")
        if self.chk_vk.isChecked():
            sources.append("vk")
            if self._mw is not None and getattr(self._mw, "vk_page", None) is None:
                self._mw._ensure_page(self._mw.IDX_VK)
        if self.chk_ok.isChecked():
            sources.append("ok")
            if self._mw is not None and getattr(self._mw, "ok_page", None) is None:
                self._mw._ensure_page(self._mw.IDX_OK)
        if not sources:
            QMessageBox.information(self, "Поиск музыки", "Отметьте хотя бы один источник поиска.")
            return

        self._search_hist.add(query)
        tab = self._cur_tab()
        if tab.items and tab.query != query:
            tab = self._new_tab(query)
        tab.query = query
        tab.clear_items()
        short = (query[:22] + "…") if len(query) > 22 else query
        self.tabs.setTabText(self.tabs.indexOf(tab), f"🔎 {short}")

        tab._search_id += 1
        sid = tab._search_id
        self._active_searches[sid] = set(sources)
        self._search_notes[sid] = []

        self.pbar.setRange(0, 0)
        self.pbar.setVisible(True)
        names = ", ".join(_SOURCE_LABELS[s] for s in sources)
        self._set_status(f"Ищу «{query}» ({names})...")

        for src in sources:
            threading.Thread(
                target=self._search_source_worker,
                args=(tab, sid, src, query),
                daemon=True,
            ).start()

    def _search_source_worker(self, tab: _UnifiedMusicTab, sid: int, src: str, query: str):
        items = []
        note = ""
        try:
            if src == "mailru":
                from mailru_download import search_mailru_tracks
                raw = search_mailru_tracks(query, max_results=0)
                for t in raw:
                    items.append({
                        "source": "mailru",
                        "artist": t.get("artist", ""),
                        "title": t.get("title", ""),
                        "duration": t.get("duration", ""),
                        "extra": t.get("bitrate", ""),
                        "url": t.get("url", ""),
                        "owner_url": t.get("owner_url", ""),
                    })
                note = f"Mail.ru: {len(items)}"

            elif src == "youtube":
                import yt_dlp
                try:
                    yt_limit = max(10, min(500, int(str(settings.get("max_results", "100")).strip())))
                except (TypeError, ValueError):
                    yt_limit = 100
                opts = {
                    "quiet": True,
                    "no_warnings": True,
                    "extract_flat": True,
                    "skip_download": True,
                    "noplaylist": True,
                }
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(f"ytsearch{yt_limit}:{query}", download=False)
                entries = (info or {}).get("entries") or []
                for e in entries:
                    if not isinstance(e, dict):
                        continue
                    vid = e.get("id") or ""
                    url = e.get("webpage_url") or e.get("url") or (f"https://www.youtube.com/watch?v={vid}" if vid else "")
                    if not url:
                        continue
                    if not url.startswith("http"):
                        url = f"https://www.youtube.com/watch?v={url}"
                    channel = e.get("channel") or e.get("uploader") or ""
                    artist, title = _split_artist_title(e.get("title", ""), fallback_author=channel)
                    dur_s = e.get("duration") or 0
                    dur = format_invidious_duration(int(dur_s)) if dur_s else ""
                    items.append({
                        "source": "youtube",
                        "artist": artist,
                        "title": title,
                        "duration": dur,
                        "extra": channel,
                        "url": url,
                    })
                note = f"YouTube: {len(items)}"

            elif src == "vk":
                vk_page = getattr(self._mw, "vk_page", None)
                if vk_page is not None and not getattr(vk_page, "driver", None):
                    self._sig.status.emit("ВК: ожидаю запуск браузера...")
                    for _ in range(90):
                        if getattr(vk_page, "driver", None):
                            break
                        time.sleep(0.5)
                if vk_page is None or not getattr(vk_page, "driver", None):
                    note = "ВК: браузер не успел запуститься"
                else:
                    vk_page._worker_search_navigate(query)
                    time.sleep(1.5)
                    raw = vk_page._scroll_and_parse(0)
                    for t in raw:
                        items.append({
                            "source": "vk",
                            "artist": t.get("artist", ""),
                            "title": t.get("title", ""),
                            "duration": t.get("duration", ""),
                            "extra": t.get("owner", ""),
                            "url": t.get("url", ""),
                            "full_id": t.get("full_id", ""),
                        })
                    note = f"ВК: {len(items)}"

            elif src == "ok":
                ok_page = getattr(self._mw, "ok_page", None)
                if ok_page is not None and not getattr(ok_page, "driver", None):
                    self._sig.status.emit("ОК: ожидаю запуск браузера...")
                    for _ in range(90):
                        if getattr(ok_page, "driver", None):
                            break
                        time.sleep(0.5)
                if ok_page is None or not getattr(ok_page, "driver", None):
                    note = "ОК: браузер не успел запуститься"
                else:
                    from ok_window_qt import _LIST_TRACKS_JS, _SCROLL_MUSIC_STEP_JS
                    ok_page.driver.get("https://ok.ru/music/search/" + quote(query))
                    seen_ok = set()
                    raw = []
                    for _ in range(20):
                        time.sleep(0.35)
                        try:
                            batch = ok_page.driver.execute_script(_LIST_TRACKS_JS) or []
                        except Exception:
                            batch = []
                        if batch:
                            break
                    end_hits = 0
                    for _ in range(180):
                        try:
                            batch = ok_page.driver.execute_script(_LIST_TRACKS_JS) or []
                        except Exception:
                            batch = []
                        for t in batch:
                            k = (t.get("tid") or "", t.get("artist", ""), t.get("title", ""))
                            if k not in seen_ok:
                                seen_ok.add(k)
                                raw.append(t)
                        try:
                            info = ok_page.driver.execute_script(_SCROLL_MUSIC_STEP_JS) or {}
                        except Exception:
                            info = {}
                        time.sleep(0.25)
                        if not info.get("moved", False) and info.get("atEnd", False):
                            end_hits += 1
                            if end_hits >= 3:
                                break
                        else:
                            end_hits = 0
                    for t in raw:
                        items.append({
                            "source": "ok",
                            "artist": t.get("artist", ""),
                            "title": t.get("title", ""),
                            "duration": t.get("duration", ""),
                            "extra": "OK.ru",
                            "tid": t.get("tid", ""),
                            "url": "",
                        })
                    note = f"ОК: {len(items)}"
        except Exception as e:
            log_message(f"WARNING Unified search [{src}] '{query}': {e}")
            note = f"{_SOURCE_LABELS.get(src, src)}: ошибка"

        if items:
            self._sig.chunk_ready.emit(tab, sid, src, items)
        self._sig.source_done.emit(tab, sid, src, note)

    def _on_chunk_ready(self, tab: _UnifiedMusicTab, sid: int, _src: str, items: list):
        if tab._search_id != sid:
            return
        tab.append_items(items)

    def _on_source_done(self, tab: _UnifiedMusicTab, sid: int, src: str, note: str):
        if tab._search_id != sid:
            return
        if note:
            self._search_notes.setdefault(sid, []).append(note)
        pending = self._active_searches.get(sid)
        if pending is not None:
            pending.discard(src)
            if not pending:
                self._active_searches.pop(sid, None)
                self.pbar.setVisible(False)
                self.pbar.setRange(0, 100)
                summary = " · ".join(self._search_notes.pop(sid, []))
                self._set_status(f"Готово ({len(tab.items)} треков): {summary}")
                self._save_tabs()
            else:
                summary = " · ".join(self._search_notes.get(sid, []))
                self._set_status(f"Поиск... {summary}")

    # ── Предпрослушивание ────────────────────────────────────────────────────

    def _on_preview_ready(self, title: str, url: str, headers: dict, req_id: int):
        w = self.window()
        if not url:
            self._set_status(f"Не удалось получить поток для «{title}»")
            if w is not None and hasattr(w, "preview_bar"):
                w.preview_bar.time_lbl.setText("Ошибка")
            return
        if w is not None and hasattr(w, "play_audio_preview"):
            w.play_audio_preview(title, url, headers=headers or None, req_id=req_id)

    def _preview_row(self, tab: _UnifiedMusicTab, row: int):
        if tab is None or row < 0 or row >= tab.table.rowCount():
            return
        it = tab.table.item(row, 0)
        d = it.data(Qt.ItemDataRole.UserRole) if it else None
        if not isinstance(d, dict):
            return
        tab.table.selectRow(row)
        tab.table.scrollToItem(it)

        src = d.get("source", "")
        title = f"{d.get('artist', '')} — {d.get('title', '')}".strip(" —") or _SOURCE_LABELS.get(src, "Трек")
        w = self.window()
        if w is None or not hasattr(w, "play_audio_preview"):
            return

        if hasattr(w, "set_preview_navigation"):
            def _step(delta: int):
                r = row + delta
                while 0 <= r < tab.table.rowCount() and tab.table.isRowHidden(r):
                    r += delta
                if 0 <= r < tab.table.rowCount():
                    self._preview_row(tab, r)
            w.set_preview_navigation(lambda: _step(-1), lambda: _step(1))

        if src == "mailru" and d.get("url"):
            w.play_audio_preview(title, d["url"], headers={"Referer": "https://my.mail.ru/"})
            return

        req_id = w.show_preview_loading(title) if hasattr(w, "show_preview_loading") else 0

        def _worker():
            stream_url = ""
            headers = {}
            try:
                if src == "youtube" and d.get("url"):
                    import yt_dlp
                    opts = {
                        "quiet": True,
                        "no_warnings": True,
                        "format": "bestaudio[ext=m4a]/bestaudio/best",
                        "noplaylist": True,
                        "js_runtimes": {"node": {}},
                        "remote_components": ["ejs:github"],
                    }
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        info = ydl.extract_info(d["url"], download=False)
                    stream_url = (info or {}).get("url") or ""
                elif src == "ok" and d.get("tid"):
                    ok_page = getattr(self._mw, "ok_page", None)
                    if ok_page is not None and getattr(ok_page, "driver", None):
                        stream_url = ok_page._fetch_stream_by_tid(d["tid"]) or ""
                        headers = {"Referer": "https://ok.ru/"}
                elif src == "vk":
                    if d.get("url", "").startswith("http"):
                        stream_url = d["url"]
                        headers = {"Referer": "https://vk.com/"}
                    elif d.get("full_id"):
                        vk_page = getattr(self._mw, "vk_page", None)
                        if vk_page is not None and getattr(vk_page, "driver", None):
                            stream_url = vk_page._get_audio_url(d["full_id"]) or ""
                            headers = {"Referer": "https://vk.com/"}
            except Exception as e:
                log_message(f"WARNING Unified preview [{src}]: {e}")
            self._sig.preview_ready.emit(title, stream_url, headers, req_id)

        threading.Thread(target=_worker, daemon=True).start()

    # ── Контекстное меню и скачивание ────────────────────────────────────────

    def _show_ctx_menu(self, pos):
        tab = self._cur_tab()
        rows = sorted({idx.row() for idx in tab.table.selectedIndexes()})
        if not rows:
            return
        menu = QMenu(self)
        act_play = menu.addAction("▶ Прослушать")
        act_dl = menu.addAction("⬇ Скачать выбранные")
        menu.addSeparator()
        act_copy_name = menu.addAction("Копировать «Исполнитель — Название»")
        act_copy_url = menu.addAction("Копировать ссылку")

        act = menu.exec(tab.table.viewport().mapToGlobal(pos))
        if act == act_play:
            self._preview_row(tab, rows[0])
        elif act == act_dl:
            self._on_download()
        elif act in (act_copy_name, act_copy_url):
            from PyQt6.QtWidgets import QApplication
            lines = []
            for r in rows:
                it = tab.table.item(r, 0)
                d = it.data(Qt.ItemDataRole.UserRole) if it else None
                if not isinstance(d, dict):
                    continue
                if act == act_copy_url and d.get("url"):
                    lines.append(d["url"])
                elif act == act_copy_name:
                    s = f"{d.get('artist', '')} — {d.get('title', '')}".strip(" —")
                    if s:
                        lines.append(s)
            if lines:
                QApplication.clipboard().setText("\n".join(lines))
                self._set_status(f"Скопировано: {len(lines)}")

    def _on_download(self):
        if self._busy_dl:
            return
        tab = self._cur_tab()
        rows = sorted({idx.row() for idx in tab.table.selectedIndexes()})
        if not rows:
            QMessageBox.information(self, "Поиск музыки", "Выберите треки для скачивания.")
            return
        picked = []
        for r in rows:
            it = tab.table.item(r, 0)
            d = it.data(Qt.ItemDataRole.UserRole) if it else None
            if isinstance(d, dict):
                picked.append(d)
        if not picked:
            return

        folder = settings.get("download_folder") or os.path.expanduser("~")
        yt_items = [d for d in picked if d.get("source") == "youtube" and d.get("url")]
        direct_items = [d for d in picked if d.get("source") in ("mailru", "ok", "vk")]

        # YouTube — добавляем в общую очередь загрузок
        yt_added = 0
        for d in yt_items:
            if add_to_queue(d["url"]):
                yt_added += 1
        if yt_added > 0:
            if not _utils.is_download_active():
                threading.Thread(target=process_queue, daemon=True).start()
            self._set_status(f"YouTube: добавлено в очередь ({yt_added})")

        if not direct_items:
            return

        self._sig.busy.emit(True)

        def _dl_worker():
            import requests
            ok_cnt = 0
            total = len(direct_items)
            for idx, d in enumerate(direct_items, 1):
                src = d.get("source", "")
                artist = d.get("artist", "")
                title = d.get("title", "track")
                label = f"{artist} - {title}".strip(" -") or "track"
                self._sig.status.emit(f"Скачиваю {idx}/{total}: {label}")
                self._sig.progress.emit(int((idx - 1) / max(total, 1) * 100))

                if src == "mailru" and d.get("url"):
                    target_dir = _utils.resolve_download_folder(folder, source="Mail.ru", author=artist)
                    base = _utils.format_download_filename(title, author=artist, is_music=True)
                    path = os.path.join(target_dir, f"{base}.mp3")
                    k = 2
                    while os.path.exists(path):
                        path = os.path.join(target_dir, f"{base} ({k}).mp3")
                        k += 1
                    try:
                        r = requests.get(
                            d["url"],
                            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://my.mail.ru/"},
                            timeout=30,
                            stream=True,
                        )
                        r.raise_for_status()
                        with open(path, "wb") as f:
                            for chunk in r.iter_content(65536):
                                if chunk:
                                    f.write(chunk)
                        _utils.ensure_mp3_tags(path, artist=artist, title=title)
                        add_to_history(url=d["url"], title=f"[Mail.ru] {label}", format_type="MP3")
                        ok_cnt += 1
                        log_message(f"SUCCESS Unified Mail.ru: {os.path.basename(path)}")
                    except Exception as e:
                        log_message(f"ERROR Unified Mail.ru '{label}': {e}")

                elif src == "ok" and d.get("tid"):
                    ok_page = getattr(self._mw, "ok_page", None)
                    if ok_page is not None and getattr(ok_page, "driver", None):
                        url = ok_page._fetch_stream_by_tid(d["tid"])
                        if url and ok_page._download_stream(url, folder, artist, title):
                            ok_cnt += 1

                elif src == "vk" and d.get("full_id"):
                    vk_page = getattr(self._mw, "vk_page", None)
                    if vk_page is not None and getattr(vk_page, "driver", None):
                        target_dir = _utils.resolve_download_folder(folder, source="VK", author=artist)
                        base = _utils.format_download_filename(title, author=artist, is_music=True)
                        path = os.path.join(target_dir, f"{base}.mp3")
                        k = 2
                        while os.path.exists(path):
                            path = os.path.join(target_dir, f"{base} ({k}).mp3")
                            k += 1
                        if vk_page._dl_via_browser(d["full_id"], path):
                            _utils.ensure_mp3_tags(path, artist=artist, title=title)
                            add_to_history(url=path, title=f"[VK] {label}", format_type="MP3")
                            ok_cnt += 1

            self._sig.progress.emit(100)
            msg = f"Готово: скачано {ok_cnt} из {total}"
            if yt_added > 0:
                msg += f" (+ {yt_added} YouTube в очереди)"
            self._sig.status.emit(msg)
            self._sig.busy.emit(False)

        threading.Thread(target=_dl_worker, daemon=True).start()

    # ── Автосохранение вкладок ───────────────────────────────────────────────

    def _save_tabs(self):
        try:
            cur_idx = self.tabs.currentIndex()
            data = []
            for i in range(self.tabs.count()):
                t = self.tabs.widget(i)
                if not isinstance(t, _UnifiedMusicTab) or not t.items:
                    continue
                data.append({
                    "query": t.query,
                    "title": self.tabs.tabText(i),
                    "items": t.items[:2000],
                    "active": i == cur_idx,
                })
            atomic_write_json(_TABS_FILE, data, indent=2)
        except Exception as e:
            log_message(f"WARNING UnifiedMusic save tabs: {e}")

    def _restore_tabs(self):
        if not os.path.exists(_TABS_FILE):
            return
        try:
            with open(_TABS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not data:
                return
            if self.tabs.count() == 1:
                w0 = self.tabs.widget(0)
                if isinstance(w0, _UnifiedMusicTab) and not w0.items:
                    self.tabs.removeTab(0)
                    w0.deleteLater()
            active_idx = 0
            for td in data:
                items = td.get("items") or []
                t = self._new_tab(td.get("query", ""))
                idx = self.tabs.indexOf(t)
                if td.get("title"):
                    self.tabs.setTabText(idx, td["title"])
                t.append_items(items)
                if td.get("active"):
                    active_idx = idx
            if 0 <= active_idx < self.tabs.count():
                self.tabs.setCurrentIndex(active_idx)
        except Exception as e:
            log_message(f"WARNING UnifiedMusic restore tabs: {e}")
