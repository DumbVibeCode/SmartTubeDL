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


def _fetch_tracks(url: str) -> list[dict]:
    """Парсит страницу плейлиста mail.ru, возвращает [{title, artist, url}]."""
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

    tracks = []
    seen: set[str] = set()

    # Данные треков лежат в <script class="data-song" type="text/plain">{...}</script>
    for m in re.finditer(
        r'<script[^>]+class="data-song"[^>]*>\s*(.*?)\s*</script>',
        html, re.DOTALL
    ):
        try:
            data = _json.loads(m.group(1))
            mp3_url = data.get('url', '')
            if not mp3_url:
                continue
            # URL может быть protocol-relative (//moosic.my.mail.ru/...)
            if mp3_url.startswith('//'):
                mp3_url = 'https:' + mp3_url
            if mp3_url in seen:
                continue
            seen.add(mp3_url)
            tracks.append({
                'title':  data.get('name', f'track_{len(tracks)+1}'),
                'artist': data.get('author', ''),
                'url':    mp3_url,
            })
        except Exception as e:
            log_message(f"DEBUG mailru json parse: {e}")

    log_message(f"INFO mailru: найдено {len(tracks)} треков")
    return tracks


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
        tracks = _fetch_tracks(url)
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
