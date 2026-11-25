"""
Загрузчик аудио и видео ВКонтакте (m3u8, прямые MP3, HTTP Range, yt-dlp, браузерный захват).
Вынесено из vk_window_qt.py в миксин VKDownloadMixin.
"""

import json
import os
import re
import subprocess
import time
import threading
import requests

import utils as _utils
from logger import log_message
from config import settings

try:
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
except Exception:
    By = None
    WebDriverWait = None


class VKDownloadMixin:
    """Миксин загрузки аудио и видео ВКонтакте."""

    def _dl_single_worker(self, d: dict, path: str):
        label = f"{d['artist']} - {d['title']}"
        is_mailru = d["full_id"].startswith("mailru:")
        is_ng     = d["full_id"].startswith("ng:")
        is_direct = is_mailru or is_ng
        source_lbl = "Mail.ru" if is_mailru else ("НГ" if is_ng else "ВК")
        key = f"vk:{d['full_id']}"
        _utils.queue_titles[key] = f"[{source_lbl}] {label}"
        _utils.current_vk_key = key
        self._sig.show_progress.emit(True)
        self._sig.progress.emit(0)
        self._tray_status("Загрузка...", 0)
        ok = False
        if not is_direct and self.driver:
            ok = self._dl_via_browser(d["full_id"], path)
        if not ok and d["url"].startswith("http"):
            if is_mailru:
                referer = "https://my.mail.ru/"
            elif is_ng:
                referer = "https://www.neizvestniy-geniy.ru/"
            else:
                referer = "https://vk.com/"
            if ".m3u8" in d["url"]:
                ok = self._dl_m3u8(d["url"], path)
            else:
                ok = self._dl_direct(d["url"], path, referer=referer)
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

    def _dl_batch_worker(self, rows: list[dict], folder: str, vk_query: str = "", total_rows: int = 0):
        self._batch_mode = True
        # Если треки без URL и браузер не на странице со списком треков — перегружаем поиск
        if vk_query and self.driver and any(not d.get("url") for d in rows):
            try:
                has_rows = self._rows_present()
                if not has_rows:
                    self._sig.status.emit("Обновляю список треков в браузере...")
                    q = vk_query.strip()
                    if q.startswith("http") or "vk.com" in q:
                        nav_url = q if q.startswith("http") else "https://" + q
                        self.driver.get(nav_url)
                    else:
                        # Текстовый запрос: повторяем навигацию как при поиске
                        self._worker_search_navigate(q)
                    WebDriverWait(self.driver, 15).until(lambda d: self._rows_present())
            except Exception as e:
                log_message(f"WARNING dl_batch: не удалось обновить список: {e}")

        total = len(rows)
        self._sig.show_progress.emit(True)
        is_wall_post = bool(self._parse_wall_url(vk_query))
        num_width = len(str(total_rows if total_rows > 0 else total))

        # ── Фаза 1: собираем ссылки ───────────────────────────────────────────
        self._sig.status.emit(f"Собираю ссылки на треки (0/{total})...")
        jobs = []
        for i, d in enumerate(rows, 1):
            num = d.get("row_num", i)
            artist = d.get("artist", "")
            title = d.get("title", "")
            target_dir = _utils.resolve_download_folder(folder, source="VK", author=artist)
            formatted = _utils.format_download_filename(title, author=artist, is_music=True)
            if is_wall_post:
                base = _safe_name(f"{str(num).zfill(num_width)}. {formatted}") or f"track_{str(num).zfill(num_width)}"
            else:
                base = formatted or f"track_{i}"
            path = os.path.join(target_dir, base + ".mp3")
            cnt, orig = 1, path
            while os.path.exists(path):
                path = f"{orig[:-4]} ({cnt}).mp3"
                cnt += 1

            url = d.get("url", "")
            is_mailru = d["full_id"].startswith("mailru:")
            is_ng     = d["full_id"].startswith("ng:")
            is_direct = is_mailru or is_ng

            if not url and not is_direct and self.driver:
                self._sig.status.emit(f"Получаю ссылку {i}/{total}...")
                self._sig.progress.emit(i / total * 30)
                url = self._get_audio_url(d["full_id"]) or ""

            jobs.append({
                "d": d, "url": url, "path": path,
                "is_mailru": is_mailru, "is_ng": is_ng,
            })

        # Заполняем VK-очередь
        _utils.vk_queue = [
            {"key": f"vk:{j['d']['full_id']}", "label": f"{j['d']['artist']} - {j['d']['title']}"}
            for j in jobs
        ]
        for item in _utils.vk_queue:
            _utils.queue_titles[item["key"]] = f"[ВК] {item['label']}"

        # ── Фаза 2: скачиваем в 4 потока ─────────────────────────────────────
        self._sig.status.emit(f"Скачиваю {total} треков...")
        ok_count = fail_count = 0
        failed_jobs = []
        lock = threading.Lock()
        completed = 0
        start_t = time.time()

        def _do_download(job):
            d, url, path = job["d"], job["url"], job["path"]
            ok = False
            if url.startswith("http"):
                if ".m3u8" in url:
                    ok = self._dl_m3u8(url, path)
                elif job["is_mailru"]:
                    ok = self._dl_direct(url, path, referer="https://my.mail.ru/")
                elif job["is_ng"]:
                    ok = self._dl_direct(url, path, referer="https://www.neizvestniy-geniy.ru/")
                else:
                    ok = self._dl_direct(url, path, referer="https://vk.com/")
            _utils.queue_titles.pop(f"vk:{d['full_id']}", None)
            return ok

        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = {ex.submit(_do_download, job): job for job in jobs}
            for fut in as_completed(futures):
                job = futures[fut]
                d = job["d"]
                try:
                    ok = fut.result()
                except Exception as e:
                    log_message(f"ERROR dl_batch future: {e}")
                    ok = False
                with lock:
                    completed += 1
                    elapsed = time.time() - start_t
                    eta = _fmt_sec((elapsed / completed) * (total - completed)) if completed > 1 else "..."
                    self._sig.batch.emit(f"[{completed}/{total}] ~{eta}")
                    self._sig.progress.emit(30 + completed / total * 70)
                    self._tray_status("Загрузка...", int(30 + completed / total * 70))
                    if ok:
                        ok_count += 1
                        _add_vk_history(d["artist"], d["title"], job["path"])
                    else:
                        fail_count += 1
                        failed_jobs.append(job)

        _utils.current_vk_key = ""
        _utils.vk_queue = []

        # Повторные попытки (последовательно)
        for attempt in range(2):
            if not failed_jobs:
                break
            retry_left = []
            for job in failed_jobs:
                d = job["d"]
                ok = False
                if self.driver:
                    ok = self._dl_via_browser(d["full_id"], job["path"])
                if not ok and job["url"].startswith("http"):
                    if ".m3u8" in job["url"]:
                        ok = self._dl_m3u8(job["url"], job["path"])
                    else:
                        ok = self._dl_direct(job["url"], job["path"])
                if ok:
                    ok_count += 1
                    fail_count -= 1
                    _add_vk_history(d["artist"], d["title"], job["path"])
                else:
                    retry_left.append(job)
            failed_jobs = retry_left
            time.sleep(1)

        if failed_jobs:
            try:
                failed_data = [j["d"] for j in failed_jobs]
                with open(os.path.join(folder, "failed_tracks.json"), "w", encoding="utf-8") as f:
                    json.dump(failed_data, f, ensure_ascii=False, indent=2)
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

    def _dl_via_search(self, d: dict, path: str) -> bool:
        """Ищет трек на VK по исполнителю+названию и скачивает через browser."""
        try:
            q = quote_plus(f"{d['artist']} {d['title']}")
            url = f"https://vk.com/audio?q={q}&section=search"
            self.driver.get(url)
            try:
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.CLASS_NAME, "audio_row"))
                )
            except Exception:
                return False
            full_id = d.get("full_id", "")
            # Сначала ищем точное совпадение по full_id
            if full_id:
                els = self.driver.find_elements(
                    By.CSS_SELECTOR, f'div.audio_row[data-full-id="{full_id}"]'
                )
                if els:
                    return self._dl_via_browser(full_id, path)
            # Берём первый результат
            rows = self._parse_html(self.driver.page_source, 1)
            if rows:
                return self._dl_via_browser(rows[0][5], path)
            return False
        except Exception as e:
            log_message(f"WARNING _dl_via_search: {e}")
            return False

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
            # Сбрасываем накопленные performance-логи, чтобы видеть только свежие запросы
            try:
                self.driver.get_log("performance")
            except Exception:
                pass

            # Новый React-каталог: ищем строку по href ссылки /audio{full_id}
            el = self.driver.execute_script("""
                var fid = arguments[0];
                var rows = document.querySelectorAll('[data-testid="MusicTrackRow"]');
                for (var i = 0; i < rows.length; i++) {
                    var t = rows[i].querySelector('[data-testid="MusicTrackRow_Title"]');
                    if (t && (t.getAttribute('href') || '').indexOf('/audio' + fid) === 0) {
                        return rows[i];
                    }
                }
                // Фолбэк без data-testid: поднимаемся от ссылки /audio<fid> до строки,
                // содержащей кнопку воспроизведения [aria-label*="рослуш"]
                var links = document.querySelectorAll('a[href^="/audio' + fid + '"]');
                for (var j = 0; j < links.length; j++) {
                    var row = links[j];
                    var topBar = false;
                    var matchedRow = null;
                    for (var up = 0; up < 8 && row.parentElement; up++) {
                        row = row.parentElement;
                        var txt = row.textContent || '';
                        if (txt.indexOf('Показать текст') !== -1 || txt.indexOf('Микс по треку') !== -1) {
                            topBar = true; break;
                        }
                        if ((row.querySelector('[aria-label*="рослуш"]') || row.querySelector('[aria-label*="Приостан"]') || row.querySelector('[aria-label*="Слушать"]')) && row.querySelectorAll('a[href]').length <= 8) {
                            matchedRow = row;
                            break;
                        }
                    }
                    if (!topBar && matchedRow) return matchedRow;
                }
                // Если открыта индивидуальная страница трека /audio<fid> — берём главную кнопку Play
                if ((location.pathname || '').indexOf('/audio' + fid) === 0) {
                    var btn = document.querySelector('[data-testid="AudioTrackPage_PlayButton"]')
                           || document.querySelector('main [aria-label*="рослуш"]')
                           || document.querySelector('main [aria-label*="Слушать"]')
                           || document.querySelector('main [aria-label*="Воспроизв"]')
                           || document.querySelector('[aria-label*="рослуш"]');
                    if (btn) return btn;
                }
                return null;
            """, full_id)

            if el is not None:
                # Кнопка воспроизведения карточки (VKUI Tappable)
                play = self.driver.execute_script("""
                    var row = arguments[0];
                    return row.querySelector('.vkitAudioRow__tappable--JQxgn')
                        || row.querySelector('[aria-label*="рослуш"]')
                        || row.querySelector('[data-testid="MusicTrackRow_PlaybackControls"]')
                        || row.querySelector('[role="button"]');
                """, el)
                target = play or el
                try:
                    self.driver.execute_script(
                        "arguments[0].scrollIntoView({block:'center'});", target)
                    time.sleep(0.3)
                except Exception:
                    pass
                # В новой вёрстке ВК поверх кнопки «Начать прослушивание» лежит
                # прозрачный оверлей VKUI-Tappable, поэтому искусственный dispatchEvent
                # на внутреннем div не запускает воспроизведение. Сначала пробуем
                # реальный клик по координатам через ActionChains, затем фолбэк dispatchEvent.
                clicked = False
                try:
                    from selenium.webdriver.common.action_chains import ActionChains
                    ActionChains(self.driver).move_to_element(target).click().perform()
                    clicked = True
                except Exception as _ae:
                    log_message(f"DEBUG ActionChains click fallback: {_ae}")
                if not clicked:
                    try:
                        self.driver.execute_script("""
                            var el = arguments[0];
                            var r = el.getBoundingClientRect();
                            var cx = r.left + r.width/2, cy = r.top + r.height/2;
                            var opt = {bubbles:true, cancelable:true, view:window,
                                       clientX:cx, clientY:cy, button:0, buttons:1};
                            ['pointerover','pointerenter','pointermove','pointerdown',
                             'mousedown','pointerup','mouseup','click'].forEach(function(t){
                                var E = t.indexOf('pointer')===0 ? PointerEvent : MouseEvent;
                                try { el.dispatchEvent(new E(t, opt)); } catch(e){}
                            });
                        """, target)
                    except Exception as _de:
                        log_message(f"WARNING dispatch click failed: {_de}")
            else:
                # Старый формат audio_row
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
                    log_message(f"WARNING _get_audio_url: трек не найден для {full_id}, page={self.driver.current_url[:60]}")
                    return None
                try:
                    play = el.find_element(By.CSS_SELECTOR, ".audio_play_wrap, .audio_row__play_btn, .audio_row__cover")
                    self.driver.execute_script("arguments[0].click();", play)
                except Exception:
                    self.driver.execute_script("arguments[0].click();", el)

            # Захватываем реальный сетевой запрос аудио. Новый плеер ВК использует
            # MSE, поэтому audio.src = blob: и для скачивания бесполезен — берём
            # .m3u8 / сегменты из performance-логов.
            m3u8 = fallback = direct = None
            total_reqs = 0
            sample = None
            for _ in range(20):
                time.sleep(0.3)
                try:
                    logs = self.driver.get_log("performance")
                except Exception:
                    logs = []
                for entry in logs:
                    try:
                        msg = json.loads(entry["message"])
                        params = msg.get("message", {}).get("params", {})
                        url = (params.get("request", {}).get("url", "")
                               or params.get("response", {}).get("url", ""))
                    except Exception:
                        continue
                    if not url:
                        continue
                    total_reqs += 1
                    if (".m3u8" in url or "/seg-" in url or "vkuseraudio" in url
                            or "vkuservideo" in url) and sample is None:
                        sample = url[:120]
                    if ".m3u8" in url:
                        m3u8 = url
                    elif "/seg-" in url and not fallback:
                        fallback = url
                    elif (("vkuseraudio" in url or "userapi" in url)
                          and (".mp3" in url or "/audio/" in url or "/amp" in url or "/s/v1/" in url)
                          and not any(ext in url.lower() for ext in (".jpg", ".jpeg", ".png", ".webp", ".svg"))
                          and not direct):
                        direct = url
                if m3u8 or direct:
                    break
                # Прямой mp3-файл (старые треки) через audio.src, но не blob:
                if not direct:
                    src = self.driver.execute_script("""
                        try { var a=document.querySelector('audio');
                            if(a&&a.src&&a.src.indexOf('blob:')!==0&&a.src.length>10)
                                return a.src;} catch(e){}
                        return null;
                    """)
                    if src:
                        direct = src

            try:
                self.driver.execute_script(
                    "try{var a=document.querySelector('audio');if(a)a.pause();}catch(e){}")
            except Exception:
                pass

            log_message(
                f"INFO _get_audio_url: m3u8={bool(m3u8)}, seg={bool(fallback)}, "
                f"direct={bool(direct)}, reqs={total_reqs}, sample={sample}")
            if m3u8:
                return m3u8
            if fallback and "/seg-" in fallback:
                return fallback.rsplit("/seg-", 1)[0] + "/index.m3u8"
            return direct
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
                tail = []
                for line in proc.stdout:
                    line = line.strip()
                    if line:
                        tail.append(line)
                        tail[:] = tail[-8:]
                    if "[download]" in line and "%" in line:
                        try:
                            pct = float(line.split("%")[0].split()[-1])
                            self._sig.progress.emit(pct)
                            self._sig.status.emit(f"Скачиваю: {pct:.1f}%")
                        except Exception:
                            pass
                proc.wait()
                if proc.returncode != 0:
                    log_message(f"ERROR VK m3u8 rc={proc.returncode} | {os.path.basename(path)} "
                                f"| url={url[:90]} | {' | '.join(tail[-4:])}")
                return proc.returncode == 0
            except FileNotFoundError:
                return self._dl_m3u8_manual(url, path)
            except Exception as e:
                log_message(f"ERROR VK m3u8: {e} | url={url[:90]}")
                return False
        else:
            return self._dl_direct(url, path)

    def _dl_direct(self, url: str, path: str, referer: str = "https://vk.com/") -> bool:
        if not REQUESTS_OK:
            return False
        try:
            cookies = {}
            if self.driver and "vk.com" in referer:
                for c in self.driver.get_cookies():
                    cookies[c["name"]] = c["value"]
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": referer,
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

    # ── Браузерный захват ВИДЕО (для закрытых видео, недоступных yt-dlp) ──────

    def download_video_via_browser(self, video_url: str, save_path: str,
                                   status_cb=None, want_mp3: bool = False):
        """Скачивает VK-видео через залогиненный браузер: открывает страницу
        видео, перехватывает m3u8/mp4 видеопотока из CDP-логов и качает его
        yt-dlp (HLS→mp4). Нужен открытый и залогиненный VK-браузер (self.driver).
        Возвращает путь к файлу или None."""
        if not getattr(self, "driver", None):
            log_message("INFO VK: браузер не запущен — закрытое видео скачать нельзя")
            return None
        lock = getattr(self, "_browser_dl_lock", None)
        if lock is None:
            lock = self._browser_dl_lock = threading.Lock()
        with lock:
            try:
                candidates, title = self._grab_video_stream(video_url, status_cb)
                if not candidates:
                    log_message("WARNING VK video: не удалось получить ссылки потока")
                    return None
                # Снимаем реальные заголовки/статус запроса плеера для первой
                # прогрессивной ссылки — okcdn отвечает только на «свои» заголовки.
                real_hdrs = None
                first_url = next((u for l, u in candidates if l.startswith("url")), None)
                if first_url:
                    status, hdrs = self._probe_stream_in_browser(first_url)
                    log_message(f"INFO VK video probe: status={status} "
                                f"hdr_keys={sorted(hdrs.keys())}")
                    if hdrs:
                        log_message(f"INFO VK video probe hdrs: "
                                    f"{ {k: str(v)[:80] for k, v in list(hdrs.items())[:20]} }")
                        real_hdrs = hdrs

                for label, stream_url in candidates:
                    log_message(f"INFO VK video: пробую поток {label}")
                    out = self._dl_video_stream(label, stream_url, save_path, title,
                                                status_cb, want_mp3, real_hdrs)
                    if out:
                        log_message(f"INFO VK video: успешно через {label}")
                        return out
                log_message("WARNING VK video: ни один из потоков не скачался")
                return None
            except Exception as e:
                log_message(f"ERROR VK video via browser: {e}")
                return None

    def _grab_video_stream(self, video_url: str, status_cb=None):
        """Берёт данные видео через authenticated fetch к al_video.php прямо в
        залогиненном браузере (same-origin, сессия настоящая) и вытаскивает
        ссылку потока из player.params. Возвращает (url_потока, заголовок)."""
        d = self.driver
        if status_cb:
            status_cb("Получаю данные видео…", 0)
        m = re.search(r'video(-?\d+_\d+)', video_url)
        if not m:
            log_message(f"WARNING VK video: не разобрал id из {video_url}")
            return None, "vk_video"
        vid = m.group(1)
        ml = re.search(r'[?&](?:pl|list)=(-?[\w]+)', video_url)
        list_id = ml.group(1) if ml else ""

        # fetch должен быть same-origin с al_video.php → идём на vk.ru
        try:
            cur = d.current_url or ""
        except Exception:
            cur = ""
        if not cur.startswith("https://vk.ru/"):
            d.get("https://vk.ru/")
            time.sleep(1.0)

        js = r"""
            var vid = arguments[0], list = arguments[1], cb = arguments[arguments.length-1];
            var body = 'act=show&al=1&video=' + encodeURIComponent(vid);
            if (list) body += '&list=' + encodeURIComponent(list);
            fetch('/al_video.php?act=show&al=1', {
                method: 'POST',
                headers: {'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8',
                          'X-Requested-With':'XMLHttpRequest'},
                body: body, credentials: 'include'
            }).then(function(r){ return r.text(); })
              .then(function(t){ cb(t); })
              .catch(function(e){ cb('ERR:' + e); });
        """
        try:
            d.set_script_timeout(30)
            raw = d.execute_async_script(js, vid, list_id)
        except Exception as e:
            log_message(f"ERROR VK video fetch: {e}")
            return None, "vk_video"
        if not raw or str(raw).startswith("ERR:"):
            log_message(f"WARNING VK video fetch пусто/ошибка: {str(raw)[:120]}")
            return None, "vk_video"

        try:
            i = raw.find("{")
            data = json.loads(raw[i:]) if i >= 0 else {}
            payload = data.get("payload") or []
            code = payload[0] if payload else None
            inner = payload[1] if len(payload) > 1 else []
            opts = inner[-1] if isinstance(inner, list) and inner else {}
            params = ((opts.get("player") or {}).get("params") or []) if isinstance(opts, dict) else []
            p = params[0] if params else {}
        except Exception as e:
            log_message(f"WARNING VK video parse: {e}; head={str(raw)[:120]}")
            return None, "vk_video"

        from html import unescape
        title = (p.get("md_title") or p.get("md_author") or "").strip() or "vk_video"
        # Кандидаты по приоритету: лучший mp4 → HLS → DASH. Перебираем, пока
        # какой-то не скачается (прямой mp4 иногда привязан к IP/сессии).
        candidates = []
        for key in ("url2160", "url1440", "url1080", "url720", "url480", "url360", "url240", "url144"):
            if p.get(key):
                candidates.append((key, unescape(p[key])))
        for key in ("hls_fmp4", "hls", "dash_sep", "dash_webm"):
            if p.get(key):
                candidates.append((key, unescape(p[key])))
        log_message(f"INFO VK video grab(al_video): code={code!r} "
                    f"cands={[k for k, _ in candidates]}")
        return candidates, title

    # Заголовки, как у <video> плеера VK/OK (без Origin — у медиазапросов его нет).
    # Range + Sec-Fetch-Dest:video + Accept-Encoding:identity — то, что шлёт
    # реальный медиаэлемент; без них okcdn отвечает 400.
    _VK_CDN_HEADERS = {
        "Referer": "https://vkvideo.ru/",
        "Accept": "*/*",
        "Range": "bytes=0-",
        "Sec-Fetch-Dest": "video",
        "Sec-Fetch-Mode": "no-cors",
        "Sec-Fetch-Site": "cross-site",
        "Accept-Encoding": "identity;q=1, *;q=0",
    }

    def _probe_stream_in_browser(self, url: str):
        """Внедряет <video> с url и снимает через CDP реальные заголовки запроса
        и статус ответа, которые даёт сам Chrome. Возвращает (status, headers)."""
        d = self.driver
        # На vkvideo.ru CSP разрешает медиа с okcdn и Referer будет правильным
        try:
            if not (d.current_url or "").startswith("https://vkvideo.ru/"):
                d.get("https://vkvideo.ru/")
                time.sleep(0.8)
        except Exception:
            pass
        try:
            d.execute_cdp_cmd("Network.enable", {})
        except Exception:
            pass
        try:
            d.get_log("performance")   # очищаем старые логи
        except Exception:
            pass
        js = r"""
            var url = arguments[0], cb = arguments[arguments.length-1];
            try {
                var v = document.createElement('video');
                v.muted = true; v.preload = 'auto'; v.src = url;
                (document.body || document.documentElement).appendChild(v);
                try { v.load(); } catch(e){}
                var p = v.play(); if (p && p.catch) p.catch(function(){});
                setTimeout(function(){ try{ v.pause(); v.remove(); }catch(e){}; cb('done'); }, 5000);
            } catch(e) { cb('ERR:' + e); }
        """
        try:
            d.set_script_timeout(15)
            d.execute_async_script(js, url)
        except Exception as e:
            log_message(f"WARNING VK video probe script: {e}")
        headers, status, reqids = {}, None, set()
        try:
            logs = d.get_log("performance")
        except Exception:
            logs = []
        for entry in logs:
            try:
                msg = json.loads(entry["message"])["message"]
                method = msg.get("method")
                params = msg.get("params", {})
            except Exception:
                continue
            if method == "Network.requestWillBeSent":
                u = params.get("request", {}).get("url", "")
                if "okcdn" in u or "vkuservideo" in u or "vkvd" in u:
                    h = params.get("request", {}).get("headers", {}) or {}
                    if h:
                        headers = h
                    reqids.add(params.get("requestId"))
            elif method == "Network.responseReceived":
                if params.get("requestId") in reqids and status is None:
                    status = params.get("response", {}).get("status")
        return status, headers

    def _dl_video_stream(self, label: str, url: str, save_path: str, title: str,
                         status_cb=None, want_mp3: bool = False, real_hdrs=None):
        """Качает поток: прямой mp4 (url*) — потоковым GET через curl_cffi,
        HLS/DASH — через yt-dlp. Затем, при need, извлекает mp3."""
        safe = re.sub(r'[\\/*?:"<>|]', "_", title).strip() or "vk_video"
        out_base = os.path.join(save_path, safe)
        log_message(f"INFO VK video dl url: {url[:140]}")
        is_hls = label.startswith("hls") or ".m3u8" in url.lower()
        if is_hls:
            out = self._dl_video_ytdlp(url, out_base, status_cb)
        else:
            out = self._dl_video_direct(url, out_base + ".mp4", status_cb, real_hdrs)
        if not out:
            return None
        if want_mp3:
            mp3 = self._video_to_mp3(out)
            return mp3 or out
        return out

    # Кол-во параллельных соединений для обхода троттлинга okcdn (на соединение)
    _VK_DL_CONNECTIONS = 6

    def _dl_video_direct(self, url: str, out_path: str, status_cb=None, real_hdrs=None):
        """Качает прямой mp4. okcdn троттлит каждое соединение до скорости
        воспроизведения, поэтому качаем несколькими параллельными Range-
        запросами. Если размер неизвестен/сервер не поддерживает Range —
        откатываемся на одно соединение."""
        try:
            from curl_cffi import requests as _cr
        except Exception as e:
            log_message(f"WARNING VK video direct: curl_cffi недоступен ({e})")
            return None
        if real_hdrs:
            headers = {k: v for k, v in real_hdrs.items()
                       if not k.startswith(":")
                       and k.lower() not in ("host", "content-length", "connection", "range")}
        else:
            headers = {k: v for k, v in self._VK_CDN_HEADERS.items() if k.lower() != "range"}

        total = self._probe_size(_cr, url, headers)
        if total > 2 * 1024 * 1024:
            if self._dl_ranges_parallel(_cr, url, headers, out_path, total, status_cb):
                return out_path
            log_message("WARNING VK video: параллельно не вышло, качаю одним потоком")
        return self._dl_single(_cr, url, headers, out_path, status_cb)

    def _probe_size(self, _cr, url, headers):
        """Узнаёт полный размер файла по Content-Range (запрос bytes=0-0)."""
        r = None
        try:
            h = dict(headers)
            h["Range"] = "bytes=0-0"
            r = _cr.get(url, headers=h, impersonate="chrome", stream=True,
                        timeout=30, allow_redirects=True)
            if r.status_code >= 400:
                log_message(f"ERROR VK video size probe HTTP {r.status_code}")
                return 0
            cr_hdr = r.headers.get("content-range") or r.headers.get("Content-Range") or ""
            if "/" in cr_hdr:
                tail = cr_hdr.rsplit("/", 1)[1].strip()
                return int(tail) if tail.isdigit() else 0
        except Exception as e:
            log_message(f"WARNING VK video size probe: {e}")
        finally:
            try:
                if r is not None:
                    r.close()
            except Exception:
                pass
        return 0

    def _dl_ranges_parallel(self, _cr, url, headers, out_path, total, status_cb):
        """Качает файл несколькими соединениями по диапазонам байт."""
        import concurrent.futures
        n = self._VK_DL_CONNECTIONS
        part = total // n
        ranges = []
        for i in range(n):
            start = i * part
            end = total - 1 if i == n - 1 else (start + part - 1)
            ranges.append((start, end))
        try:
            with open(out_path, "wb") as f:
                f.truncate(total)
        except Exception as e:
            log_message(f"WARNING VK video preallocate: {e}")
            return False

        done = [0]
        t0 = [time.time()]
        failed = [False]
        wlock = threading.Lock()
        try:
            fh = open(out_path, "r+b")
        except Exception as e:
            log_message(f"WARNING VK video open r+b: {e}")
            return False

        def worker(rng):
            start, end = rng
            if failed[0]:
                return
            r = None
            try:
                h = dict(headers)
                h["Range"] = f"bytes={start}-{end}"
                r = _cr.get(url, headers=h, impersonate="chrome", stream=True,
                            timeout=300, allow_redirects=True)
                if r.status_code != 206:
                    log_message(f"ERROR VK video range HTTP {r.status_code} (ждали 206)")
                    failed[0] = True
                    return
                pos = start
                for chunk in r.iter_content(chunk_size=262144):
                    if failed[0]:
                        return
                    if not chunk:
                        continue
                    with wlock:
                        fh.seek(pos)
                        fh.write(chunk)
                        done[0] += len(chunk)
                        if status_cb and time.time() - t0[0] > 0.3:
                            t0[0] = time.time()
                            status_cb(f"Качаю видео: {done[0]/total*100:.1f}%",
                                      int(done[0] / total * 100))
                    pos += len(chunk)
            except Exception as e:
                log_message(f"ERROR VK video range dl: {e}")
                failed[0] = True
            finally:
                try:
                    if r is not None:
                        r.close()
                except Exception:
                    pass

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=n) as ex:
                list(ex.map(worker, ranges))
        finally:
            try:
                fh.close()
            except Exception:
                pass

        if failed[0] or done[0] < total * 0.999:
            log_message(f"WARNING VK video parallel: failed={failed[0]} done={done[0]}/{total}")
            return False
        log_message(f"INFO VK video: скачано параллельно {n} соединениями, {done[0]} байт")
        return True

    def _dl_single(self, _cr, url, headers, out_path, status_cb):
        """Запасной путь: одно соединение."""
        h = dict(headers)
        h.setdefault("Range", "bytes=0-")
        r = None
        try:
            r = _cr.get(url, headers=h, impersonate="chrome", stream=True,
                        timeout=120, allow_redirects=True)
            if r.status_code >= 400:
                log_message(f"ERROR VK video direct HTTP {r.status_code}")
                return None
            total = int(r.headers.get("content-length") or 0)
            done = 0
            t0 = time.time()
            with open(out_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=262144):
                    if chunk:
                        f.write(chunk)
                        done += len(chunk)
                        if status_cb and total and time.time() - t0 > 0.3:
                            t0 = time.time()
                            status_cb(f"Качаю видео: {done/total*100:.1f}%",
                                      int(done / total * 100))
            if os.path.getsize(out_path) < 10240:
                log_message("WARNING VK video direct: файл подозрительно мал")
                return None
            return out_path
        except Exception as e:
            log_message(f"ERROR VK video direct dl: {e}")
            try:
                if os.path.exists(out_path):
                    os.remove(out_path)
            except Exception:
                pass
            return None
        finally:
            try:
                if r is not None:
                    r.close()
            except Exception:
                pass

    def _dl_video_ytdlp(self, url: str, out_base: str, status_cb=None):
        """HLS/DASH через yt-dlp (с impersonate и браузерным Referer)."""
        cmd = ["yt-dlp", "--no-warnings", "--newline", "--impersonate", "chrome",
               "--add-header", "Referer: https://vkvideo.ru/",
               "--merge-output-format", "mp4",
               "-o", out_base + ".%(ext)s", url]
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            tail = []
            for line in proc.stdout:
                line = line.strip()
                if line:
                    tail.append(line)
                    if len(tail) > 6:
                        tail.pop(0)
                if "[download]" in line and "%" in line:
                    try:
                        pct = float(line.split("%")[0].split()[-1])
                        if status_cb:
                            status_cb(f"Качаю видео: {pct:.1f}%", int(pct))
                    except Exception:
                        pass
            proc.wait()
            if proc.returncode != 0:
                log_message(f"ERROR VK video yt-dlp rc={proc.returncode}; вывод: {' | '.join(tail)}")
                return None
        except FileNotFoundError:
            log_message("ERROR VK video: yt-dlp не найден в PATH")
            return None
        except Exception as e:
            log_message(f"ERROR VK video ytdlp: {e}")
            return None
        import glob
        cand = glob.glob(out_base + ".*")
        return cand[0] if cand else None

    def _video_to_mp3(self, src: str):
        """Извлекает аудио в mp3 через ffmpeg (для формата mp3)."""
        dst = os.path.splitext(src)[0] + ".mp3"
        try:
            r = subprocess.run(
                ["ffmpeg", "-y", "-i", src, "-vn", "-acodec", "libmp3lame",
                 "-q:a", "2", dst],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            if r.returncode == 0 and os.path.exists(dst):
                try:
                    os.remove(src)
                except Exception:
                    pass
                return dst
        except Exception as e:
            log_message(f"WARNING VK video->mp3: {e}")
        return None

    # ── Сохранение / загрузка вкладок ────────────────────────────────────────

