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
# Состояние окна (запрос, найденное, позиция в списке) между запусками
_OK_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ok_search_state.json")


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


# ── JS для музыки (треки в shadow DOM: <wm-track>, <wm-track-play-button>) ──────
# Музыка OK отдаётся прямым MP3 с musicd.okcdn.ru/v0/stream по подписанной ссылке.
# API получения ссылки закрыт CORS, поэтому ссылку ловим из CDP при проигрывании:
# кликаем play по нужному треку → в сетевых логах появляется musicd .../stream.
_WALK_JS = (
    "function* walk(root){var els=root.querySelectorAll('*');"
    "for(var i=0;i<els.length;i++){var e=els[i];yield e;"
    "if(e.shadowRoot)yield* walk(e.shadowRoot);}}\n"
)

# Перечислить треки по порядку DOM: [{tid, title, artist, album, duration}]
_LIST_TRACKS_JS = _WALK_JS + r"""
var out=[];
for (var e of walk(document)){
    if (!e.tagName || e.tagName.toLowerCase()!=='wm-track') continue;
    var tid = '', propArtist = '', propAlbum = '';
    try {
        if (e.props && e.props.track) {
            var trk = e.props.track;
            if (trk.id != null) tid = String(trk.id);
            propArtist = (trk.ensemble || trk.artistsStr || '').trim();
            propAlbum = (trk.album && trk.album.name ? String(trk.album.name) : '').trim();
        }
    } catch(_){}
    var artist='', album='';
    for (var x of walk(e)){
        if (x.classList && x.classList.contains('artist') && !artist) artist=(x.innerText||'').trim();
        if (x.classList && x.classList.contains('album')  && !album)  album=(x.innerText||'').trim();
    }
    if (!artist) artist = propArtist;
    if (!album)  album  = propAlbum;
    var lines=(e.innerText||'').split('\n').map(function(s){return s.trim();}).filter(Boolean);
    var title=lines.length?lines[0]:'';
    var dur='';
    for (var li=lines.length-1; li>=0; li--){
        if (/^\d{1,2}:\d{2}(:\d{2})?$/.test(lines[li])){ dur=lines[li]; break; }
    }
    out.push({tid:tid, title:title, artist:artist, album:album, duration:dur});
}
return out;
"""

# Мгновенно получить подписанный URL musicd .../stream (уже с clientHash) через store.player.playTrack(tid)
# Без прокрутки списка, без кликов по кнопке Play и без спиннеров в браузере!
_FETCH_STREAM_BY_TID_JS = _WALK_JS + r"""
var tid = Number(arguments[0]);
var done = arguments[arguments.length - 1];
var player = null;
for (var e of walk(document)) {
    try {
        if (e.store && e.store.player && typeof e.store.player.playTrack === 'function') {
            player = e.store.player;
            break;
        }
    } catch(_){}
}
if (!player) return done({ok: false, err: 'store.player not found'});
var prevSrc = (player.state && player.state.currentTrackSrc) || '';
Promise.resolve(player.playTrack(tid)).then(
    function(){
        var deadline = Date.now() + 8000;
        (function poll(){
            var src = (player.state && player.state.currentTrackSrc)
                   || (player.audio && player.audio.currentSrc) || '';
            if (src && src.indexOf('musicd.okcdn.ru') !== -1 && (src !== prevSrc || src.indexOf('fid=' + tid) !== -1)) {
                try { player.pause(); } catch(_){}
                return done({ok: true, play: src});
            }
            if (Date.now() > deadline) {
                try { player.pause(); } catch(_){}
                return done({ok: false, err: 'timeout waiting currentTrackSrc'});
            }
            setTimeout(poll, 100);
        })();
    },
    function(err){
        try { player.pause(); } catch(_){}
        done({ok: false, err: String(err)});
    }
);
"""

# Кликнуть play по i-му <wm-track> (строго внутри самого трека, порядок = _LIST_TRACKS_JS)
_PLAY_TRACK_JS = _WALK_JS + r"""
var i=arguments[0], tracks=[];
for (var e of walk(document)){
    if (e.tagName && e.tagName.toLowerCase()==='wm-track') tracks.push(e);
}
if (i>=tracks.length) return false;
var tr = tracks[i], btn = null;
for (var x of walk(tr)){
    if (x.tagName && x.tagName.toLowerCase()==='wm-track-play-button'){ btn = x; break; }
}
if (!btn) return false;
var b = btn.querySelector('button') || btn;
try{ tr.scrollIntoView({block:'center'}); }catch(_){}
b.click();
return true;
"""

# Остановить любое проигрывание (аудио может быть в shadow DOM)
_STOP_AUDIO_JS = _WALK_JS + r"""
for (var e of walk(document)){
    if (e.tagName && e.tagName.toLowerCase()==='audio'){
        try{ e.pause(); }catch(_){}
    }
}
return true;
"""

# Прокрутить контейнер списка треков на ОДИН экран вниз (с перекрытием 30%),
# чтобы при скачивании не перескакивать треки в виртуализированном списке.
_SCROLL_MUSIC_STEP_JS = _WALK_JS + r"""
function up(n){
    var p = n.parentNode;
    if (p && p.nodeType === 11 && p.host) return p.host;
    if (p) return p;
    return (n.assignedSlot) ? n.assignedSlot : null;
}
var tracks = [];
for (var e of walk(document)){
    if (e.tagName && e.tagName.toLowerCase()==='wm-track') tracks.push(e);
}
if (!tracks.length) return {moved: false, atEnd: true};
var node = tracks[0], best = null;
for (var i=0; i<40 && node; i++){
    try{
        var st = getComputedStyle(node);
        var oy = st.overflowY, o = st.overflow;
        if ((oy==='auto'||oy==='scroll'||o==='auto'||o==='scroll') &&
            (node.scrollHeight - node.clientHeight > 40)){ best = node; break; }
    }catch(_){}
    node = up(node);
}
if (best){
    var before = best.scrollTop;
    var step = Math.max(250, Math.floor(best.clientHeight * 0.7));
    best.scrollTop = Math.min(best.scrollHeight, before + step);
    var after = best.scrollTop;
    var atEnd = (after + best.clientHeight >= best.scrollHeight - 5);
    return {moved: Math.abs(after - before) > 2, atEnd: atEnd, scrollTop: after, scrollHeight: best.scrollHeight};
}
var wb = window.scrollY;
window.scrollBy(0, Math.max(300, Math.floor(window.innerHeight * 0.7)));
var wa = window.scrollY;
return {moved: Math.abs(wa - wb) > 2, atEnd: Math.abs(wa - wb) <= 2};
"""

# Прокрутить ВНУТРЕННИЙ контейнер списка треков (не окно): от трека поднимаемся
# по дереву, пересекая границы shadow DOM, до ближайшего прокручиваемого предка.
_SCROLL_MUSIC_JS = _WALK_JS + r"""
function up(n){
    var p = n.parentNode;
    if (p && p.nodeType === 11 && p.host) return p.host;   // ShadowRoot → host
    if (p) return p;
    return (n.assignedSlot) ? n.assignedSlot : null;
}
var tracks = [];
for (var e of walk(document)){
    if (e.tagName && e.tagName.toLowerCase()==='wm-track') tracks.push(e);
}
if (!tracks.length) return {n:0, scrolled:false};
var node = tracks[0], best = null;
for (var i=0; i<40 && node; i++){
    try{
        var st = getComputedStyle(node);
        var oy = st.overflowY, o = st.overflow;
        if ((oy==='auto'||oy==='scroll'||o==='auto'||o==='scroll') &&
            (node.scrollHeight - node.clientHeight > 40)){ best = node; break; }
    }catch(_){}
    node = up(node);
}
if (best){ best.scrollTop = best.scrollHeight; }
else { try{ tracks[tracks.length-1].scrollIntoView(false); }catch(_){ } }
return {n: tracks.length, scrolled: !!best};
"""

# Прокрутить список треков на указанную позицию scrollTop (быстрый прыжок к нужной зоне)
_SCROLL_TO_JS = _WALK_JS + r"""
var targetY = Math.max(0, arguments[0] || 0);
function up(n){var p=n.parentNode;if(p&&p.nodeType===11&&p.host)return p.host;if(p)return p;return n.assignedSlot||null;}
var first=null;
for (var e of walk(document)){ if(e.tagName&&e.tagName.toLowerCase()==='wm-track'){ first=e; break; } }
if(!first) return 0;
var node=first;
for (var i=0;i<40&&node;i++){
    try{ var st=getComputedStyle(node); var oy=st.overflowY,o=st.overflow;
        if((oy==='auto'||oy==='scroll'||o==='auto'||o==='scroll')&&(node.scrollHeight-node.clientHeight>40)){
            node.scrollTop = Math.min(node.scrollHeight, targetY);
            return node.scrollTop;
        }
    }catch(_){}
    node=up(node);
}
try{ window.scrollTo(0, targetY); }catch(_){}
return window.scrollY || 0;
"""

# Получить текущий scrollTop контейнера списка треков
_GET_SCROLL_TOP_JS = _WALK_JS + r"""
function up(n){var p=n.parentNode;if(p&&p.nodeType===11&&p.host)return p.host;if(p)return p;return n.assignedSlot||null;}
var first=null;
for (var e of walk(document)){ if(e.tagName&&e.tagName.toLowerCase()==='wm-track'){ first=e; break; } }
if(!first) return 0;
var node=first;
for (var i=0;i<40&&node;i++){
    try{ var st=getComputedStyle(node); var oy=st.overflowY,o=st.overflow;
        if((oy==='auto'||oy==='scroll'||o==='auto'||o==='scroll')&&(node.scrollHeight-node.clientHeight>40)){
            return node.scrollTop;
        }
    }catch(_){}
    node=up(node);
}
return window.scrollY || 0;
"""

# Прокрутить список треков в САМЫЙ ВЕРХ (перед перечислением/загрузкой)
_SCROLL_TOP_JS = _WALK_JS + r"""
function up(n){var p=n.parentNode;if(p&&p.nodeType===11&&p.host)return p.host;if(p)return p;return n.assignedSlot||null;}
var first=null;
for (var e of walk(document)){ if(e.tagName&&e.tagName.toLowerCase()==='wm-track'){ first=e; break; } }
if(!first) return false;
var node=first;
for (var i=0;i<40&&node;i++){
    try{ var st=getComputedStyle(node); var oy=st.overflowY,o=st.overflow;
        if((oy==='auto'||oy==='scroll'||o==='auto'||o==='scroll')&&(node.scrollHeight-node.clientHeight>40)){ node.scrollTop=0; break; }
    }catch(_){}
    node=up(node);
}
try{ window.scrollTo(0,0); }catch(_){}
return true;
"""


def _safe_name(name: str) -> str:
    """Убирает недопустимые для имени файла символы (Windows)."""
    import re
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", name or "").strip()
    name = re.sub(r"\s+", " ", name)
    return name[:150] or "track"


class OKSearchWindow(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.driver = None
        self._sig = _Sig()
        self._mode = "music"      # 'music' | 'video' — задаётся последним поиском
        self._busy = False        # идёт поиск или загрузка
        self._results = []        # последние результаты (для сохранения состояния)
        self._restoring = False   # True во время восстановления (не сохранять)

        self._build_ui()
        self._connect_signals()
        self._restore_state()

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
            "Музыка: исполнитель/название. Видео: ссылка ok.ru/profile/… или ok.ru/group/…"
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
        self.table.currentCellChanged.connect(self._on_current_cell_changed)
        self.table.setSortingEnabled(True)   # сортировка по клику на заголовок
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
        self._sig.search_done.connect(self._on_search_done)
        self._sig.error.connect(lambda m: QMessageBox.critical(self, "Ошибка", m))

    def _on_search_done(self):
        self.search_btn.setEnabled(True)
        self.dl_btn.setEnabled(self.table.rowCount() > 0)

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
        if self._busy:
            self._sig.status.emit("Занято — дождитесь завершения")
            return
        import re
        from urllib.parse import quote
        raw = self.query_input.text().strip()
        if not raw:
            return
        low = raw.lower()
        is_url = low.startswith("http") or re.search(r'(?:www\.|m\.)?ok\.ru/|odnoklassniki\.ru/', low)

        # Музыка: простой текст (поиск) или ссылка с /music
        if (not is_url) or ("/music" in low):
            if is_url:
                page = raw if low.startswith("http") else "https://" + raw.lstrip("/")
            else:
                page = "https://ok.ru/music/search/" + quote(raw)
            self._mode = "music"
            self._start_search("Открываю музыку...", self._worker_music, page)
            return

        # Видео: ссылка на профиль/группу/конкретное видео
        page = self._normalize_video_page(raw)
        if page is None:
            QMessageBox.information(
                self, "Не разобрал ссылку",
                "Для видео вставьте ссылку на профиль/группу OK или на видео "
                "ok.ru/video/…\nДля музыки введите исполнителя или название."
            )
            return
        self._mode = "video"
        if re.search(r'/video/\d+', page):
            self._populate_table([{"href": page, "title": raw, "duration": ""}])
            self._sig.status.emit("Готово: 1 видео")
            return
        self._start_search("Открываю страницу видео...", self._worker_videos, page)

    def _start_search(self, status: str, worker, page: str):
        self._busy = True
        self.search_btn.setEnabled(False)
        self.dl_btn.setEnabled(False)
        self._sig.status.emit(status)
        self._sig.show_progress.emit(True)
        threading.Thread(target=worker, args=(page,), daemon=True).start()

    # ── Музыка (поиск / страница) ─────────────────────────────────────────────

    @staticmethod
    def _track_key(t: dict) -> tuple:
        """Идентичность трека: исполнитель|название|альбом|длительность."""
        return (t.get("artist", ""), t.get("title", ""),
                t.get("album", ""), t.get("duration", ""))

    def _worker_music(self, page_url: str):
        try:
            self.driver.get(page_url)
            time.sleep(5)
            try:
                self.driver.execute_script(_SCROLL_TOP_JS)
            except Exception:
                pass
            # Список виртуализирован: в DOM только «окно» треков. Скроллим и
            # накапливаем уникальные по сигнатуре, пока появляются новые.
            seen = set()
            tracks = []
            stall = 0
            for _ in range(80):
                try:
                    cur_y = int(self.driver.execute_script(_GET_SCROLL_TOP_JS) or 0)
                except Exception:
                    cur_y = 0
                rows = self.driver.execute_script(_LIST_TRACKS_JS) or []
                added = 0
                for r in rows:
                    k = self._track_key(r)
                    if k not in seen:
                        seen.add(k)
                        r["scroll_top"] = cur_y
                        tracks.append(r)
                        added += 1
                self._sig.status.emit(f"Загрузка треков... {len(tracks)}")
                if added == 0:
                    stall += 1
                    if stall >= 3:
                        break
                else:
                    stall = 0
                if len(tracks) >= 500:
                    break
                try:
                    self.driver.execute_script(_SCROLL_MUSIC_JS)
                except Exception:
                    pass
                time.sleep(1.1)
            log_message(f"INFO OK: собрано треков: {len(tracks)} ({page_url})")
            self._sig.results_ready.emit(tracks)
            self._sig.status.emit(f"Готово: {len(tracks)} треков")
        except Exception as e:
            log_message(f"ERROR OK музыка: {e}")
            self._sig.error.emit(f"Не удалось получить треки:\n{e}")
        finally:
            self._busy = False
            self._sig.show_progress.emit(False)
            self._sig.search_done.emit()

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
            self._busy = False
            self._sig.show_progress.emit(False)
            self._sig.search_done.emit()

    def _populate_table(self, results: list):
        self._results = list(results)
        self.table.setSortingEnabled(False)   # не даём Qt перемешивать при вставке
        self.table.setRowCount(0)
        if self._mode == "music":
            self.table.setColumnCount(3)
            self.table.setHorizontalHeaderLabels(["Исполнитель", "Название", "Длит."])
            hh = self.table.horizontalHeader()
            hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
            for t in results:
                r = self.table.rowCount()
                self.table.insertRow(r)
                it0 = QTableWidgetItem(t.get("artist", ""))
                # альбом не показываем, но держим для точной сигнатуры при загрузке
                it0.setData(Qt.ItemDataRole.UserRole, t.get("album", ""))
                self.table.setItem(r, 0, it0)
                self.table.setItem(r, 1, QTableWidgetItem(t.get("title", "")))
                self.table.setItem(r, 2, QTableWidgetItem(t.get("duration", "")))
        else:
            self.table.setColumnCount(3)
            self.table.setHorizontalHeaderLabels(["Название", "Длит.", "Ссылка"])
            hh = self.table.horizontalHeader()
            hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
            for v in results:
                r = self.table.rowCount()
                self.table.insertRow(r)
                self.table.setItem(r, 0, QTableWidgetItem(v.get("title", "")))
                self.table.setItem(r, 1, QTableWidgetItem(v.get("duration", "")))
                self.table.setItem(r, 2, QTableWidgetItem(v.get("href", "")))
        self.table.setSortingEnabled(True)
        self.dl_btn.setEnabled(self.table.rowCount() > 0)
        if not self._restoring:
            self._save_state()

    def _selected_rows(self) -> list:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if not rows:
            rows = list(range(self.table.rowCount()))   # ничего не выбрано → все
        return rows

    def _selected_urls(self) -> list:
        urls, seen = [], set()
        for r in self._selected_rows():
            it = self.table.item(r, 2)
            u = it.text().strip() if it else ""
            if u and u not in seen:
                seen.add(u)
                urls.append(u)
        return urls

    def _selected_tracks(self) -> list:
        """[{artist,title,album,duration}] для выбранных строк музыки."""
        out = []
        for r in self._selected_rows():
            it0 = self.table.item(r, 0)
            it1 = self.table.item(r, 1)
            it2 = self.table.item(r, 2)
            if it0 is None:
                continue
            out.append({
                "artist": it0.text(),
                "title": it1.text() if it1 else "",
                "album": it0.data(Qt.ItemDataRole.UserRole) or "",
                "duration": it2.text() if it2 else "",
            })
        return out

    def _download_selected(self):
        if self._busy:
            self._sig.status.emit("Занято — дождитесь завершения")
            return
        if self._mode == "music":
            tracks = self._selected_tracks()
            if not tracks:
                QMessageBox.information(self, "Нет треков", "Список пуст — сначала найдите музыку.")
                return
            self._busy = True
            self.dl_btn.setEnabled(False)
            self.search_btn.setEnabled(False)
            self._sig.show_progress.emit(True)
            threading.Thread(target=self._worker_music_download, args=(tracks,), daemon=True).start()
            return
        # Видео → общая очередь загрузок (штатный конвейер, yt-dlp)
        urls = self._selected_urls()
        if not urls:
            QMessageBox.information(self, "Нет видео", "Список пуст — сначала найдите видео.")
            return
        from queues import add_to_queue, process_queue
        from utils import is_download_active
        for u in urls:
            add_to_queue(u)
        log_message(f"INFO OK: в очередь добавлено видео: {len(urls)}")
        self._sig.status.emit(f"В очередь добавлено: {len(urls)} — качаю по очереди")
        if not is_download_active():
            threading.Thread(target=process_queue, daemon=True).start()

    # ── Загрузка музыки: play → захват стрима → прямое скачивание MP3 ──────────

    def _drain_stream(self):
        """Возвращает последний пойманный URL аудиопотока musicd .../stream."""
        found = None
        try:
            for entry in self.driver.get_log("performance"):
                try:
                    msg = json.loads(entry["message"])["message"]
                except Exception:
                    continue
                if msg.get("method") not in (
                    "Network.requestWillBeSent", "Network.responseReceived"):
                    continue
                p = msg.get("params", {})
                u = ((p.get("response", {}) or {}).get("url")
                     or (p.get("request", {}) or {}).get("url") or "")
                if "musicd.okcdn.ru" in u and "/stream" in u:
                    found = u
        except Exception:
            pass
        return found

    def _capture_stream_url(self, idx: int, timeout: float = 12.0):
        """Кликает play по idx-му треку и ловит URL аудиопотока из CDP-логов."""
        try:
            self.driver.execute_cdp_cmd("Network.enable", {})
            self.driver.execute_cdp_cmd("Network.clearBrowserCache", {})
        except Exception:
            pass
        self._drain_stream()   # очистить накопленное
        for attempt in range(2):
            try:
                if not self.driver.execute_script(_PLAY_TRACK_JS, idx):
                    return None
            except Exception as e:
                log_message(f"WARNING OK play[{idx}]: {e}")
                return None
            wait_sec = timeout if attempt == 0 else 6.0
            deadline = time.time() + wait_sec
            while time.time() < deadline:
                time.sleep(0.4)
                u = self._drain_stream()
                if u:
                    return u
            # Если первый клик попал на уже играющий трек (поставил паузу),
            # второй клик запустит воспроизведение заново.
            if attempt == 0:
                log_message(f"DEBUG OK play[{idx}]: повторный клик для захвата стрима")
        return None

    def _fetch_stream_by_tid(self, tid: str) -> str | None:
        """Мгновенно получает URL MP3 через внутренний store.api.post('play', {tid})."""
        if not tid or not self.driver:
            return None
        try:
            self.driver.set_script_timeout(15)
            res = self.driver.execute_async_script(_FETCH_STREAM_BY_TID_JS, tid)
            if isinstance(res, dict) and res.get("ok") and res.get("play"):
                return res["play"]
            if isinstance(res, dict) and res.get("err"):
                log_message(f"DEBUG OK store.api.post(play, {tid}): {res['err']}")
        except Exception as e:
            log_message(f"WARNING OK _fetch_stream_by_tid({tid}): {e}")
        return None

    def _worker_music_download(self, tracks: list):
        folder = ""
        try:
            from config import settings
            folder = settings.get("download_folder") or os.path.expanduser("~")
        except Exception:
            folder = os.path.expanduser("~")

        total = len(tracks)
        done = 0
        ok_count = 0

        # ── Быстрый путь: треки с известным tid скачиваем напрямую через store.api ──
        # Без прокрутки списка, без кликов Play, работает при любой сортировке таблицы!
        fallback_tracks = []
        for t in tracks:
            tid = str(t.get("tid") or "").strip()
            if not tid:
                fallback_tracks.append(t)
                continue
            artist, title = t.get("artist", ""), t.get("title", "")
            label = f"{artist} — {title}".strip(" —") or title or "трек"
            done += 1
            self._sig.status.emit(f"Скачиваю {done}/{total}: {label}")
            self._sig.progress.emit((done - 1) / max(total, 1) * 100)

            url = self._fetch_stream_by_tid(tid)
            if url and self._download_stream(url, folder, artist, title):
                ok_count += 1
            else:
                # Если прямой API не отдал ссылку — пробуем запасной путь через клик Play
                log_message(f"INFO OK: fallback через браузер для «{label}»")
                done -= 1
                fallback_tracks.append(t)

        # ── Запасной путь (для старых сохранённых списков без tid) ──────────────────
        remaining = {self._track_key(t): t for t in fallback_tracks}
        if remaining:
            known_ys = [t["scroll_top"] for t in remaining.values() if isinstance(t.get("scroll_top"), (int, float))]
            start_y = max(0, int(min(known_ys)) - 300) if known_ys else 0
            try:
                if start_y > 0:
                    self.driver.execute_script(_SCROLL_TO_JS, start_y)
                    time.sleep(0.35)
                else:
                    self.driver.execute_script(_SCROLL_TOP_JS)
                    time.sleep(0.5)
            except Exception:
                pass

            end_hits = 0
            max_steps = 250
            steps = 0
            jumped_to_top = (start_y == 0)
            while remaining and steps < max_steps:
                try:
                    rows = self.driver.execute_script(_LIST_TRACKS_JS) or []
                except Exception:
                    rows = []
                hit_i = None
                hit_key = None
                for i, r in enumerate(rows):
                    k = self._track_key(r)
                    if k in remaining:
                        hit_i, hit_key = i, k
                        break
                if hit_i is None:
                    steps += 1
                    info = {}
                    try:
                        cur_y = int(self.driver.execute_script(_GET_SCROLL_TOP_JS) or 0)
                        rem_ys = [t["scroll_top"] for t in remaining.values()
                                  if isinstance(t.get("scroll_top"), (int, float)) and t["scroll_top"] > cur_y + 900]
                        if rem_ys:
                            jump_y = max(0, int(min(rem_ys)) - 300)
                            new_y = int(self.driver.execute_script(_SCROLL_TO_JS, jump_y) or 0)
                            info = {"moved": abs(new_y - cur_y) > 2, "atEnd": False}
                        else:
                            info = self.driver.execute_script(_SCROLL_MUSIC_STEP_JS) or {}
                    except Exception:
                        pass
                    time.sleep(0.18)
                    if not info.get("moved", False) and info.get("atEnd", False):
                        end_hits += 1
                        if end_hits >= 3:
                            if not jumped_to_top and remaining:
                                jumped_to_top = True
                                end_hits = 0
                                try:
                                    self.driver.execute_script(_SCROLL_TOP_JS)
                                    time.sleep(0.4)
                                except Exception:
                                    pass
                                continue
                            break
                    else:
                        end_hits = 0
                    continue

                end_hits = 0
                t = remaining.pop(hit_key)
                artist, title = t.get("artist", ""), t.get("title", "")
                label = f"{artist} — {title}".strip(" —") or title or "трек"
                done += 1
                self._sig.status.emit(f"Скачиваю {done}/{total}: {label}")
                self._sig.progress.emit((done - 1) / max(total, 1) * 100)

                url = self._capture_stream_url(hit_i)
                try:
                    self.driver.execute_script(_STOP_AUDIO_JS)
                except Exception:
                    pass
                if url and self._download_stream(url, folder, artist, title):
                    ok_count += 1
                else:
                    log_message(f"WARNING OK: не удалось скачать «{label}»")
                time.sleep(0.3)

            if remaining:
                missed = ", ".join(
                    f"{t.get('artist', '')} - {t.get('title', '')}".strip(" -")
                    for t in list(remaining.values())[:5]
                )
                log_message(f"WARNING OK: не найдены в списке при прокрутке ({len(remaining)} шт.): {missed}")

            try:
                self.driver.execute_script(_STOP_AUDIO_JS)
            except Exception:
                pass

        self._sig.progress.emit(100)
        self._sig.status.emit(f"Готово: скачано {ok_count} из {total}")
        self._busy = False
        self._sig.show_progress.emit(False)
        self._sig.search_done.emit()

    def _download_stream(self, url: str, folder: str, artist: str, title: str) -> bool:
        import urllib.request
        base = _safe_name(f"{artist} - {title}".strip(" -")) or "track"
        path = os.path.join(folder, base + ".mp3")
        k = 1
        while os.path.exists(path):
            path = os.path.join(folder, f"{base} ({k}).mp3")
            k += 1
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://ok.ru/"})
            with urllib.request.urlopen(req, timeout=30) as resp, open(path, "wb") as f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
            log_message(f"SUCCESS OK музыка: {os.path.basename(path)}")
            return True
        except Exception as e:
            log_message(f"ERROR OK скачивание «{artist} - {title}»: {e}")
            try:
                if os.path.exists(path) and os.path.getsize(path) == 0:
                    os.remove(path)
            except Exception:
                pass
            return False

    def _show_ctx_menu(self, pos):
        if self.table.rowCount() == 0:
            return
        menu = QMenu(self)
        act_dl = menu.addAction("⬇ Скачать выбранные")
        act_copy = None
        act_copy_track = None
        if self._mode == "video":
            act_copy = menu.addAction("Копировать ссылку")
        else:
            act_copy_track = menu.addAction("Скопировать исполнителя и название")
        act = menu.exec(self.table.viewport().mapToGlobal(pos))
        if act == act_dl:
            self._download_selected()
        elif act_copy is not None and act == act_copy:
            urls = self._selected_urls()
            if urls:
                from PyQt6.QtWidgets import QApplication
                QApplication.clipboard().setText("\n".join(urls))
                self._sig.status.emit(f"Скопировано ссылок: {len(urls)}")
        elif act_copy_track is not None and act == act_copy_track:
            lines = []
            for t in self._selected_tracks():
                artist, title = t.get("artist", ""), t.get("title", "")
                line = f"{artist} — {title}".strip(" —") or title
                if line:
                    lines.append(line)
            if lines:
                from PyQt6.QtWidgets import QApplication
                QApplication.clipboard().setText("\n".join(lines))
                self._sig.status.emit(f"Скопировано: {len(lines)}")

    # ── Сохранение состояния окна ─────────────────────────────────────────────

    def _on_current_cell_changed(self, *args):
        # Запоминаем позицию курсора в списке (не во время наполнения таблицы)
        if not self._restoring:
            self._save_state()

    def _save_state(self):
        try:
            data = {
                "mode": self._mode,
                "query": self.query_input.text(),
                "results": self._results,
                "current_row": self.table.currentRow(),
            }
            hh = self.table.horizontalHeader()
            if hh.isSortIndicatorShown():
                data["sort_col"] = hh.sortIndicatorSection()
                data["sort_order"] = hh.sortIndicatorOrder().value
            with open(_OK_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log_message(f"WARNING OK save state: {e}")

    def _restore_state(self):
        if not os.path.exists(_OK_STATE_FILE):
            return
        try:
            with open(_OK_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return
        self.query_input.setText(data.get("query", ""))
        results = data.get("results") or []
        if not results:
            return
        self._restoring = True
        try:
            self._mode = data.get("mode", "music")
            self._populate_table(results)
            self._restore_row(data.get("current_row", -1), data)
        finally:
            self._restoring = False

    def _restore_row(self, row: int, entry: dict):
        # Сначала сортировка (двигает строки), потом выделение строки
        sc = entry.get("sort_col", -1)
        if sc is not None and sc >= 0:
            self.table.sortItems(sc, Qt.SortOrder(entry.get("sort_order", 0)))
        if 0 <= row < self.table.rowCount():
            self.table.setCurrentCell(row, 0)
            self.table.scrollTo(
                self.table.model().index(row, 0),
                QAbstractItemView.ScrollHint.PositionAtCenter,
            )

    def closeEvent(self, event):
        self._save_state()
        event.accept()
        self.hide()

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
