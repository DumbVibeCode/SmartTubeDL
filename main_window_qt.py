"""
Единое главное окно SmartTubeDL (UI v2).
Объединяет все разделы (YouTube, ВК, ОК, Mail.ru, По ссылкам, Очередь, История,
Настройки + Обновления, Логи) с левой боковой панелью и постоянной нижней панелью статуса.
"""

import os
import re
import threading

from PyQt6.QtCore import Qt, QTimer, QObject, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QListWidget, QListWidgetItem, QStackedWidget, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QComboBox, QCheckBox,
    QProgressBar, QTextEdit, QFileDialog, QMessageBox, QFrame, QTabWidget
)

import styles as _styles
from styles import COLORS_MINIMAL as COLORS
from config import settings, save_settings, format_invidious_duration
from download_history import load_download_history
from logger import log_message
from queues import add_to_queue, process_queue, get_queue_count
import utils as _utils


# ── Раздел: Mail.ru (отдельный пункт под плейлисты и будущий поиск) ──────────

class _MailRuSignals(QObject):
    tracks_ready = pyqtSignal(list, str)   # (tracks, owner)
    status = pyqtSignal(str)
    progress = pyqtSignal(int)
    busy = pyqtSignal(bool)
    error = pyqtSignal(str)


class MailRuPage(QWidget):
    """Раздел Mail.ru: загрузка плейлистов (с заделом под полноценный поиск)."""

    def __init__(self):
        super().__init__()
        self._tracks = []
        self._owner = ""
        self._busy = False
        self._sig = _MailRuSignals()
        self._sig.tracks_ready.connect(self._populate)
        self._sig.status.connect(self.status_label_set)
        self._sig.progress.connect(self._set_progress)
        self._sig.busy.connect(self._set_busy)
        self._sig.error.connect(lambda m: QMessageBox.warning(self, "Mail.ru", m))
        self._setup_ui()

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        hdr = QLabel("Mail.ru — Музыка")
        f = QFont()
        f.setPointSize(12)
        f.setBold(True)
        hdr.setFont(f)
        lay.addWidget(hdr)

        row = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Вставьте ссылку на плейлист my.mail.ru/music/playlists/...")
        self.url_input.returnPressed.connect(self._on_fetch)
        row.addWidget(self.url_input, 1)

        self.btn_fetch = QPushButton("Загрузить список")
        self.btn_fetch.clicked.connect(self._on_fetch)
        row.addWidget(self.btn_fetch)
        lay.addLayout(row)

        flt_row = QHBoxLayout()
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("Фильтр по названию или исполнителю...")
        self.filter_input.textChanged.connect(self._apply_filter)
        flt_row.addWidget(self.filter_input, 1)

        self.btn_dl = QPushButton("Скачать выбранные")
        self.btn_dl.clicked.connect(self._on_download)
        flt_row.addWidget(self.btn_dl)
        lay.addLayout(flt_row)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Исполнитель", "Название", "Длительность"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.doubleClicked.connect(lambda _idx: self._on_download())
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setColumnWidth(0, 220)
        lay.addWidget(self.table, 1)

        foot = QHBoxLayout()
        self.status_lbl = QLabel("Вставьте ссылку на плейлист Mail.ru")
        foot.addWidget(self.status_lbl, 1)
        self.pbar = QProgressBar()
        self.pbar.setMaximumWidth(220)
        self.pbar.setVisible(False)
        foot.addWidget(self.pbar)
        lay.addLayout(foot)

    def status_label_set(self, text: str):
        self.status_lbl.setText(text)

    def _set_progress(self, val: int):
        self.pbar.setVisible(0 <= val < 100)
        if val >= 0:
            self.pbar.setValue(val)

    def _set_busy(self, busy: bool):
        self._busy = busy
        self.btn_fetch.setEnabled(not busy)
        self.btn_dl.setEnabled(not busy)

    def open_url(self, url: str):
        self.url_input.setText(url)
        self._on_fetch()

    def _on_fetch(self):
        if self._busy:
            return
        url = self.url_input.text().strip()
        if not url:
            return
        from mailru_download import is_mailru_url, _fetch_tracks
        if not is_mailru_url(url):
            QMessageBox.information(self, "Mail.ru", "Укажите ссылку вида https://my.mail.ru/music/...")
            return
        self._sig.busy.emit(True)
        self._sig.status.emit("Загружаю список треков Mail.ru...")

        def _worker():
            try:
                tracks, owner = _fetch_tracks(url)
                self._sig.tracks_ready.emit(tracks, owner or "")
                self._sig.status.emit(f"Найдено треков: {len(tracks)}" + (f" ({owner})" if owner else ""))
            except Exception as e:
                log_message(f"ERROR MailRuPage fetch: {e}")
                self._sig.error.emit(f"Не удалось загрузить плейлист:\n{e}")
                self._sig.status.emit("Ошибка загрузки списка")
            finally:
                self._sig.busy.emit(False)

        threading.Thread(target=_worker, daemon=True).start()

    def _populate(self, tracks: list, owner: str):
        self._tracks = tracks
        self._owner = owner
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(tracks))
        for i, t in enumerate(tracks):
            it_a = QTableWidgetItem(t.get("artist", ""))
            it_a.setData(Qt.ItemDataRole.UserRole, t)
            self.table.setItem(i, 0, it_a)
            self.table.setItem(i, 1, QTableWidgetItem(t.get("title", "")))
            self.table.setItem(i, 2, QTableWidgetItem(t.get("duration", "")))
        self.table.setSortingEnabled(True)

    def _apply_filter(self, text: str):
        q = text.strip().lower()
        for r in range(self.table.rowCount()):
            a = (self.table.item(r, 0).text() if self.table.item(r, 0) else "").lower()
            t = (self.table.item(r, 1).text() if self.table.item(r, 1) else "").lower()
            self.table.setRowHidden(r, bool(q) and (q not in a and q not in t))

    def _on_download(self):
        if self._busy:
            return
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()})
        if not rows:
            QMessageBox.information(self, "Mail.ru", "Выберите треки для скачивания.")
            return
        picked = []
        for r in rows:
            it = self.table.item(r, 0)
            if it:
                d = it.data(Qt.ItemDataRole.UserRole)
                if d:
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
                if not url:
                    continue
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
                except Exception as e:
                    log_message(f"ERROR Mail.ru download '{base}': {e}")
            self._sig.progress.emit(100)
            self._sig.status.emit(f"Готово: скачано {ok_cnt} из {total}")
            self._sig.busy.emit(False)

        threading.Thread(target=_worker, daemon=True).start()


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
        self.search_input.setMaximumWidth(280)
        self.search_input.textChanged.connect(self._apply_filter)
        top.addWidget(self.search_input)

        self.fmt_combo = QComboBox()
        self.fmt_combo.addItems(["Все форматы", "MP4", "MP3"])
        self.fmt_combo.currentTextChanged.connect(self._apply_filter)
        top.addWidget(self.fmt_combo)

        btn_refresh = QPushButton("Обновить")
        btn_refresh.setProperty("secondary", True)
        btn_refresh.clicked.connect(lambda: self.reload(force=True))
        top.addWidget(btn_refresh)
        lay.addLayout(top)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Дата", "Название", "Формат", "Длительность"])
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
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(0, 145)
        self.table.setColumnWidth(2, 70)
        self.table.setColumnWidth(3, 95)
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

    def reload(self, force: bool = False):
        from download_history import DOWNLOAD_HISTORY_FILE
        try:
            mtime = os.path.getmtime(DOWNLOAD_HISTORY_FILE) if os.path.exists(DOWNLOAD_HISTORY_FILE) else 0.0
        except OSError:
            mtime = 0.0
        if not force and mtime == self._last_mtime and self._entries:
            return
        self._last_mtime = mtime
        self._entries = load_download_history()
        self._apply_filter()

    def _apply_filter(self):
        q = self.search_input.text().strip().lower()
        fmt = self.fmt_combo.currentText()
        filtered = []
        for entry in self._entries:
            title = entry.get("title", "Без названия")
            rfmt = str(entry.get("format", "N/A")).upper()
            if q and q not in title.lower():
                continue
            if fmt in ("MP4", "MP3") and rfmt != fmt:
                continue
            filtered.append(entry)

        self.table.setUpdatesEnabled(False)
        try:
            self.table.setRowCount(len(filtered))
            for row, entry in enumerate(filtered):
                date = entry.get("date", "N/A")
                title = entry.get("title", "Без названия")
                rfmt = str(entry.get("format", "N/A")).upper()
                dur_s = entry.get("duration", 0)
                dur = format_invidious_duration(dur_s) if dur_s else "—"
                it0 = QTableWidgetItem(date)
                it0.setData(Qt.ItemDataRole.UserRole, entry.get("url", ""))
                self.table.setItem(row, 0, it0)
                self.table.setItem(row, 1, QTableWidgetItem(title))
                self.table.setItem(row, 2, QTableWidgetItem(rfmt))
                self.table.setItem(row, 3, QTableWidgetItem(dur))
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

        gl.addStretch()
        tabs.addTab(gen, "Основные настройки")
        tabs.addTab(self._updater, "Обновления утилит")
        lay.addWidget(tabs, 1)

    def sync_from_settings(self):
        self.dir_edit.setText(settings.get("download_folder", ""))
        for w in (self.fmt_combo, self.qual_combo, self.browser_combo,
                  self.chk_auto, self.chk_conv, self.chk_desc, self.chk_save_exit):
            w.blockSignals(True)
        self.fmt_combo.setCurrentText(settings.get("download_format", "mp4"))
        self.qual_combo.setCurrentText(settings.get("video_quality", "1080p"))
        self.browser_combo.setCurrentText(settings.get("cookies_browser", "firefox"))
        self.chk_auto.setChecked(settings.get("auto_capture_enabled", True))
        self.chk_conv.setChecked(settings.get("conversion_enabled", True))
        self.chk_desc.setChecked(settings.get("download_with_description", False))
        self.chk_save_exit.setChecked(settings.get("save_settings_on_exit", True))
        for w in (self.fmt_combo, self.qual_combo, self.browser_combo,
                  self.chk_auto, self.chk_conv, self.chk_desc, self.chk_save_exit):
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
        save_settings(settings)
        self.settings_changed.emit()


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
        sidebar_wrap = QFrame()
        sidebar_wrap.setFixedWidth(182)
        sidebar_wrap.setStyleSheet(
            f"QFrame {{ background-color: {COLORS['bg_secondary']}; "
            f"border-right: 1px solid {COLORS['border']}; }}"
        )
        sb_lay = QVBoxLayout(sidebar_wrap)
        sb_lay.setContentsMargins(8, 12, 8, 10)
        sb_lay.setSpacing(6)

        brand = QLabel("  SmartTubeDL")
        bf = QFont()
        bf.setPointSize(11)
        bf.setBold(True)
        brand.setFont(bf)
        brand.setStyleSheet(f"color: {COLORS['accent']}; border: none; padding-bottom: 6px;")
        sb_lay.addWidget(brand)

        self.nav = QListWidget()
        self.nav.setStyleSheet(f"""
            QListWidget {{
                background: transparent;
                border: none;
                outline: none;
                font-size: 13px;
            }}
            QListWidget::item {{
                padding: 9px 10px;
                border-radius: 6px;
                margin-bottom: 2px;
                color: {COLORS['text_primary']};
            }}
            QListWidget::item:hover {{
                background-color: {COLORS['bg_primary']};
            }}
            QListWidget::item:selected {{
                background-color: {COLORS['accent']};
                color: #ffffff;
                font-weight: bold;
            }}
        """)
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

        body.addWidget(sidebar_wrap)

        # ── Стек разделов ────────────────────────────────────────────────────
        self.stack = QStackedWidget()
        for _ in self._nav_items:
            self.stack.addWidget(QWidget())   # ленивые заглушки
        body.addWidget(self.stack, 1)

        outer.addLayout(body, 1)

        # ── Постоянная нижняя панель (Footer Bar) ────────────────────────────
        footer = QFrame()
        footer.setFixedHeight(40)
        footer.setStyleSheet(
            f"QFrame {{ background-color: {COLORS['bg_secondary']}; "
            f"border-top: 1px solid {COLORS['border']}; }}"
        )
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(12, 4, 12, 4)
        fl.setSpacing(10)

        self.footer_status = QLabel("Ожидание...")
        self.footer_status.setStyleSheet("border: none;")
        fl.addWidget(self.footer_status, 1)

        self.footer_progress = QProgressBar()
        self.footer_progress.setFixedWidth(150)
        self.footer_progress.setFixedHeight(14)
        self.footer_progress.setVisible(False)
        fl.addWidget(self.footer_progress)

        self.btn_pause = QPushButton("⏸ Пауза")
        self.btn_pause.setProperty("secondary", True)
        self.btn_pause.setFixedHeight(26)
        self.btn_pause.clicked.connect(self._toggle_pause)
        fl.addWidget(self.btn_pause)

        self.quick_fmt = QComboBox()
        self.quick_fmt.addItems(["MP4 1080p", "MP4 720p", "MP4 480p", "MP3"])
        self.quick_fmt.setFixedHeight(26)
        self.quick_fmt.currentTextChanged.connect(self._on_quick_fmt_changed)
        fl.addWidget(self.quick_fmt)

        self.btn_folder = QPushButton()
        self.btn_folder.setProperty("secondary", True)
        self.btn_folder.setFixedHeight(26)
        self.btn_folder.setToolTip("Открыть папку загрузок в Проводнике")
        self.btn_folder.clicked.connect(self._open_download_folder)
        fl.addWidget(self.btn_folder)

        outer.addWidget(footer)

        self.sync_footer_settings()
        # По умолчанию открываем YouTube
        self.nav.setCurrentRow(self.IDX_YOUTUBE)

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
