"""neizvestniy-geniy.ru downloader."""
import re
import requests
from logger import log_message

NEIZVESTNIY_RE = re.compile(
    r'^https?://(?:www\.)?neizvestniy-geniy\.ru/users/\d+/works/',
    re.I
)
MP3_RE   = re.compile(r'["\'](/mp3/\d{4}/\d{2}/\d+\.mp3)["\']')
TITLE_RE = re.compile(r'Прослушать:\s*([^<"\']+)')

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    'Accept-Language': 'ru-RU,ru;q=0.9',
}

BASE = 'https://www.neizvestniy-geniy.ru'


def is_neizvestniy_url(url: str) -> bool:
    return bool(NEIZVESTNIY_RE.match(url.strip()))


def _get(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or 'utf-8'
    return r.text


def _fetch_work(work_url: str) -> tuple[str, str]:
    """Возвращает (title, mp3_url) для одной страницы произведения."""
    html = _get(work_url)
    mp3_url = ''
    m = MP3_RE.search(html)
    if m:
        mp3_url = BASE + m.group(1)
    title = ''
    t = TITLE_RE.search(html)
    if t:
        title = t.group(1).strip()
    return title, mp3_url


def fetch_works(listing_url: str, status_cb=None) -> tuple[list[dict], str]:
    """
    Парсит страницу /users/N/works/, возвращает (tracks, author).
    tracks = [{title, artist, url, duration}]
    """
    if status_cb:
        status_cb("Загружаю список произведений...")

    html = _get(listing_url)

    # Имя автора из заголовка страницы
    author = ''
    h1 = re.search(r'<h1[^>]*>([^<]+)</h1>', html)
    if h1:
        author = h1.group(1).strip()

    # Ссылки на произведения: /cat/music/GENRE/ID.html
    seen: set[str] = set()
    work_links: list[tuple[str, str]] = []  # (link_title, work_url)
    for m in re.finditer(
        r'href="(/cat/music/[^/"]+/(\d+)\.html)[^"]*"[^>]*>\s*([^<]+?)\s*</a>',
        html
    ):
        href, work_id, link_title = m.group(1), m.group(2), m.group(3).strip()
        if work_id in seen or not link_title:
            continue
        seen.add(work_id)
        work_links.append((link_title, BASE + href))

    log_message(f"INFO neizvestniy: найдено {len(work_links)} произведений на странице")

    results = []
    for i, (link_title, work_url) in enumerate(work_links, 1):
        if status_cb:
            status_cb(f"[{i}/{len(work_links)}] {link_title[:50]}...")
        try:
            page_title, mp3_url = _fetch_work(work_url)
        except Exception as e:
            log_message(f"WARNING neizvestniy: не удалось загрузить {work_url}: {e}")
            continue
        if not mp3_url:
            log_message(f"WARNING neizvestniy: MP3 не найден на {work_url}")
            continue
        title = page_title or link_title
        results.append({'title': title, 'artist': author, 'url': mp3_url, 'duration': ''})

    log_message(f"INFO neizvestniy: итого треков с MP3: {len(results)}, автор: {author}")
    return results, author
