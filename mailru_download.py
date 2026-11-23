"""Mail.ru music playlist downloader."""
import re
import os
import time
import threading
import requests

from logger import log_message

MAILRU_RE = re.compile(r'^https?://my\.mail\.ru/music/', re.I)


def is_mailru_url(url: str) -> bool:
    return bool(MAILRU_RE.match(url.strip()))


def _safe_name(text: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', '_', text).strip() or 'track'


_AJAX_URL = "https://my.mail.ru/cgi-bin/my/ajax"
_AJAX_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://my.mail.ru/music/",
}


def search_mailru_tracks(query: str, max_results: int = 200) -> list[dict]:
    """Ищет треки на my.mail.ru через официальный AJAX-эндпоинт с пагинацией.
    Возвращает [{title, artist, duration, bitrate, owner, url}]."""
    import json as _json
    query = (query or "").strip()
    if not query:
        return []

    tracks = []
    seen = set()
    offset = 0
    page_size = 100
    cap = max_results if max_results > 0 else 1000

    while len(tracks) < cap:
        limit = min(page_size, cap - len(tracks))
        sp = _json.dumps({
            "music": {"limit": limit, "offset": offset},
            "playlist": {"limit": 0, "offset": 0},
            "album": {"limit": 0, "offset": 0},
            "artist": {"limit": 0, "offset": 0},
        })
        resp = requests.get(
            _AJAX_URL,
            params={
                "ajax_call": "1",
                "func_name": "music.search",
                "mna": "",
                "mnb": "",
                "arg_query": query,
                "arg_extended": "1",
                "arg_search_params": sp,
            },
            headers=_AJAX_HEADERS,
            timeout=20,
        )
        resp.raise_for_status()
        payload = resp.json()
        if not (isinstance(payload, list) and len(payload) >= 4 and isinstance(payload[3], dict)):
            break
        batch = payload[3].get("MusicData") or []
        if not batch:
            break

        added = 0
        for item in batch:
            mp3_url = (item.get("URL") or "").strip()
            if not mp3_url:
                continue
            if mp3_url.startswith("//"):
                mp3_url = "https:" + mp3_url
            if mp3_url in seen:
                continue
            seen.add(mp3_url)

            br = item.get("BitRate") or 0
            owner_dir = (item.get("OwnerDir") or "").strip()
            owner_url = f"https://my.mail.ru{owner_dir}" if owner_dir.startswith("/") else ""
            tracks.append({
                "artist":    (item.get("Author") or "").strip(),
                "title":     (item.get("Name") or "").strip() or f"track_{len(tracks)+1}",
                "duration":  (item.get("DurationStr") or item.get("Duration") or "").strip(),
                "bitrate":   f"{br} kbps" if br else "",
                "owner":     (item.get("OwnerName") or "").strip(),
                "owner_url": owner_url,
                "url":       mp3_url,
            })
            added += 1
            if len(tracks) >= cap:
                break

        if len(batch) < limit or added == 0:
            break
        offset += len(batch)

    log_message(f"INFO mailru search tracks '{query}': найдено {len(tracks)}")
    return tracks


def search_mailru_playlists(query: str, max_results: int = 100) -> list[dict]:
    """Ищет плейлисты на my.mail.ru. Возвращает [{title, owner, tracks_count, url}]."""
    import json as _json
    query = (query or "").strip()
    if not query:
        return []

    playlists = []
    seen = set()
    offset = 0
    page_size = 50
    cap = max_results if max_results > 0 else 300

    while len(playlists) < cap:
        limit = min(page_size, cap - len(playlists))
        sp = _json.dumps({
            "music": {"limit": 0, "offset": 0},
            "playlist": {"limit": limit, "offset": offset},
            "album": {"limit": 0, "offset": 0},
            "artist": {"limit": 0, "offset": 0},
        })
        resp = requests.get(
            _AJAX_URL,
            params={
                "ajax_call": "1",
                "func_name": "music.search",
                "mna": "",
                "mnb": "",
                "arg_query": query,
                "arg_extended": "1",
                "arg_search_params": sp,
            },
            headers=_AJAX_HEADERS,
            timeout=20,
        )
        resp.raise_for_status()
        payload = resp.json()
        if not (isinstance(payload, list) and len(payload) >= 4 and isinstance(payload[3], dict)):
            break
        batch = payload[3].get("PlaylistData") or []
        if not batch:
            break

        added = 0
        for item in batch:
            url_id = (item.get("URLID") or "").strip()
            pid = str(item.get("ID") or "").strip()
            if not url_id and not pid:
                continue
            pl_url = f"https://my.mail.ru/music/playlists/{url_id or pid}"
            if pl_url in seen:
                continue
            seen.add(pl_url)

            owner_dir = (item.get("OwnerDir") or "").strip()
            owner_url = f"https://my.mail.ru{owner_dir}" if owner_dir.startswith("/") else ""
            playlists.append({
                "title":        (item.get("Name") or "Плейлист").strip(),
                "owner":        (item.get("OwnerName") or "").strip(),
                "owner_url":    owner_url,
                "tracks_count": str(item.get("TracksCount") or item.get("TracksCount_hr") or ""),
                "url":          pl_url,
            })
            added += 1
            if len(playlists) >= cap:
                break

        if len(batch) < limit or added == 0:
            break
        offset += len(batch)

    log_message(f"INFO mailru search playlists '{query}': найдено {len(playlists)}")
    return playlists


def _fetch_tracks(url: str) -> tuple[list[dict], str]:
    """
    Парсит страницу плейлиста mail.ru.
    Возвращает (tracks, owner), где tracks = [{title, artist, duration, url}].
    """
    import json as _json
    headers = {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/131.0.0.0 Safari/537.36'
        ),
        'Accept-Language': 'ru-RU,ru;q=0.9',
    }
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    html = resp.text

    # Владелец: <span class="b-music__playlist-header__owner__name ...">Имя</span>
    owner = 'Mail.ru'
    owner_m = re.search(r'b-music__playlist-header__owner__name[^>]*>([^<]+)<', html)
    if owner_m:
        owner = owner_m.group(1).strip()

    tracks = []
    seen: set[str] = set()
    dur_re = re.compile(r'itemprop=["\']duration["\'][^>]*>([^<]+)<')

    # Данные треков: <script class="data-song" type="text/plain">{...}</script>
    for m in re.finditer(
        r'<script[^>]+class="data-song"[^>]*>\s*(.*?)\s*</script>',
        html, re.DOTALL
    ):
        try:
            data = _json.loads(m.group(1))
            mp3_url = data.get('url', '')
            if not mp3_url:
                continue
            if mp3_url.startswith('//'):
                mp3_url = 'https:' + mp3_url
            if mp3_url in seen:
                continue
            seen.add(mp3_url)

            # Длительность из HTML перед тегом <script>
            preceding = html[max(0, m.start() - 300): m.start()]
            dur_m = dur_re.search(preceding)
            duration = dur_m.group(1).strip() if dur_m else ''

            tracks.append({
                'title':    data.get('name', f'track_{len(tracks)+1}'),
                'artist':   data.get('author', ''),
                'duration': duration,
                'url':      mp3_url,
            })
        except Exception as e:
            log_message(f"DEBUG mailru json parse: {e}")

    log_message(f"INFO mailru: найдено {len(tracks)} треков, владелец: {owner}")
    return tracks, owner


def download_mailru_playlist(
    url: str,
    save_folder: str,
    status_cb=None,
    progress_cb=None,
    done_cb=None,
):
    """
    Скачивает все треки плейлиста mail.ru в save_folder.
    status_cb(str)      — текст статуса
    progress_cb(float)  — прогресс 0-100
    done_cb(ok, fail)   — вызывается по завершении
    """
    def _status(msg):
        if status_cb:
            status_cb(msg)

    def _progress(pct):
        if progress_cb:
            progress_cb(pct)

    _status("Загружаю страницу Mail.ru...")
    try:
        tracks, _owner = _fetch_tracks(url)
    except Exception as e:
        log_message(f"ERROR mailru fetch: {e}")
        _status(f"Ошибка загрузки страницы: {e}")
        if done_cb:
            done_cb(0, 1)
        return

    if not tracks:
        _status("Треки не найдены — возможно, страница требует авторизации")
        if done_cb:
            done_cb(0, 0)
        return

    os.makedirs(save_folder, exist_ok=True)
    dl_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://my.mail.ru/',
    }

    ok = fail = 0
    total = len(tracks)

    for i, track in enumerate(tracks, 1):
        artist = track.get('artist', '')
        title  = track.get('title', f'track_{i}')
        mp3_url = track['url']

        base = _safe_name(f"{artist} - {title}" if artist else title)
        path = os.path.join(save_folder, base + '.mp3')
        cnt, orig = 1, path
        while os.path.exists(path):
            path = f"{orig[:-4]} ({cnt}).mp3"
            cnt += 1

        _status(f"[{i}/{total}] {base[:50]}...")
        _progress((i - 1) / total * 100)

        try:
            r = requests.get(mp3_url, headers=dl_headers, stream=True, timeout=60)
            r.raise_for_status()
            with open(path, 'wb') as f:
                for chunk in r.iter_content(8192):
                    if chunk:
                        f.write(chunk)
            ok += 1
            log_message(f"SUCCESS mailru: {base}")
        except Exception as e:
            fail += 1
            log_message(f"ERROR mailru download '{base}': {e}")

        time.sleep(0.1)

    _progress(100)
    msg = f"Mail.ru: скачано {ok}" + (f", ошибок: {fail}" if fail else "")
    _status(msg)
    log_message(f"INFO mailru: {msg}")
    if done_cb:
        done_cb(ok, fail)
