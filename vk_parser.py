"""
Парсинг треков ВКонтакте (React-каталог, старый HTML audio_row, React-fiber DOM).
Вынесено из vk_window_qt.py в миксин VKParserMixin.
"""

import json
import re
import time
from bs4 import BeautifulSoup
from logger import log_message


class VKParserMixin:
    """Миксин парсинга треков со страниц ВКонтакте."""

    def _extract_catalog_tracks(self, max_count) -> list:
        """Извлекает треки из нового React-каталога ВК через JS (data-testid или семантические ссылки /audio)."""
        try:
            raw = self.driver.execute_script("""
                var rows = document.querySelectorAll('[data-testid="MusicTrackRow"]');
                if (rows.length > 0) {
                    return Array.from(rows).map(function(row){
                        var t = row.querySelector('[data-testid="MusicTrackRow_Title"]');
                        var a = row.querySelector('[data-testid="MusicTrackRow_Authors"]');
                        var d = row.querySelector('[data-testid="MusicTrackRow_Duration"]');
                        return {
                            href:   t ? (t.getAttribute('href') || '') : '',
                            title:  t ? t.textContent.trim() : '',
                            artist: a ? a.textContent.trim() : '',
                            dur:    d ? d.textContent.trim() : ''
                        };
                    });
                }

                // Фолбэк без data-testid: ищем все ссылки на треки /audio<owner>_<id>
                var audioRe = /^\\/audio-?\\d+_\\d+/;
                var durRe = /\\b(\\d{1,2}:\\d{2})\\b/g;
                var links = document.querySelectorAll('a[href*="/audio"]');
                var out = [];
                for (var i = 0; i < links.length; i++) {
                    var a = links[i];
                    var href = a.getAttribute('href') || '';
                    if (!audioRe.test(href)) continue;
                    var title = (a.textContent || '').trim();
                    if (!title) continue;

                    // Поднимаемся до контейнера строки трека (где есть длительность и не слишком много ссылок)
                    var row = a;
                    var isTopPlayer = false;
                    for (var up = 0; up < 6 && row.parentElement; up++) {
                        row = row.parentElement;
                        var txt = row.innerText || row.textContent || '';
                        if (txt.indexOf('Показать текст') !== -1 || txt.indexOf('Микс по треку') !== -1) {
                            isTopPlayer = true;
                            break;
                        }
                        if (/\\b\\d{1,2}:\\d{2}\\b/.test(txt) && row.querySelectorAll('a[href]').length <= 8) {
                            break;
                        }
                    }
                    if (isTopPlayer) continue;

                    // Исполнители: ссылки на /artist/... или /audio?performer=1...
                    var rowLinks = row.querySelectorAll('a[href]');
                    var artists = [];
                    for (var j = 0; j < rowLinks.length; j++) {
                        var rl = rowLinks[j];
                        if (rl === a) continue;
                        var rh = rl.getAttribute('href') || '';
                        if (rh.indexOf('/artist/') !== -1 || rh.indexOf('performer=1') !== -1) {
                            var at = (rl.textContent || '').trim();
                            if (at) artists.push(at);
                        }
                    }
                    var rowText = (row.innerText || row.textContent || '').trim();
                    var dur = '';
                    var dm, lastDur = '';
                    durRe.lastIndex = 0;
                    while ((dm = durRe.exec(rowText)) !== null) { lastDur = dm[1]; }
                    dur = lastDur;
                    if (dur === '0:00') continue;

                    // Если ссылок на исполнителя нет — пробуем взять вторую строку innerText
                    var artistStr = artists.join(', ');
                    if (!artistStr) {
                        var parts = rowText.split('\\n').map(function(s){ return s.trim(); }).filter(Boolean);
                        if (parts.length >= 2 && parts[1] !== dur && parts[1] !== title) {
                            artistStr = parts[1];
                        }
                    }

                    out.push({ href: href, title: title, artist: artistStr, dur: dur });
                }
                return out;
            """) or []
        except Exception as e:
            log_message(f"WARNING catalog JS extraction: {e}")
            return []

        results, seen = [], set()
        for item in raw:
            href = item.get("href", "")
            # /audio239175566_456240028_1984d66a48402a9eac → owner=239175566 id=456240028
            m = re.search(r'/audio(-?\d+)_(\d+)', href)
            if not m:
                continue
            owner_id, audio_id = m.group(1), m.group(2)
            full_id = f"{owner_id}_{audio_id}"
            if full_id in seen:
                continue
            seen.add(full_id)
            title  = (item.get("title", "") or "").strip()
            artist = (item.get("artist", "") or "").strip()
            if not title:
                continue
            dur = (item.get("dur", "") or "").strip()
            try:
                oi = int(owner_id)
                owner_disp = f"club{abs(oi)}" if oi < 0 else f"id{oi}"
            except ValueError:
                owner_disp = owner_id
            results.append((artist[:80], title[:120], dur, owner_disp, "", full_id))
            if max_count and len(results) >= max_count:
                break
        return results

    def _scroll_and_parse(self, count: int) -> list:
        limit = count if count > 0 else None
        # Новый React-каталог
        results = self._extract_catalog_tracks(limit)
        # Старый формат audio_row (фолбэк)
        if not results:
            results = self._parse_html(self.driver.page_source, limit)
        if limit and len(results) >= limit:
            return results[:limit]

        last_h = self.driver.execute_script("return document.body.scrollHeight")
        for i in range(500):
            self._sig.status.emit(f"Загружаю треки... ({len(results)}/{limit or '∞'})")
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1.5)
            new_results = self._extract_catalog_tracks(limit)
            if not new_results:
                new_results = self._parse_html(self.driver.page_source, limit)
            results = new_results if new_results else results
            if limit and len(results) >= limit:
                break
            new_h = self.driver.execute_script("return document.body.scrollHeight")
            if new_h == last_h:
                break
            last_h = new_h
        return results[:limit] if limit else results

    @staticmethod
    def _parse_html(html: str, max_count) -> list:
        if not html or len(html) < 100:
            return []
        soup = BeautifulSoup(html, "html.parser")
        rows = soup.find_all("div", class_=lambda c: c and "audio_row" in c)
        if not rows:
            rows = soup.find_all("div", attrs={"data-audio": True})
        results = []
        seen = set()
        for row in rows:
            try:
                if any(c in row.get("class", []) for c in ["audio_claimed"]):
                    continue
                data_attr = row.get("data-audio")
                if not data_attr:
                    continue
                data = json.loads(data_attr)
                if len(data) < 6:
                    continue
                audio_id, owner_id = str(data[0]), str(data[1])
                link = str(data[2]) if len(data) > 2 else ""
                if "audio_api_unavailable" in link:
                    continue
                title  = BeautifulSoup(str(data[3] or ""), "html.parser").get_text(strip=True)
                artist = BeautifulSoup(str(data[4] or ""), "html.parser").get_text(strip=True)
                if not title or "аудио доступно на vk.com" in title.lower():
                    continue
                total_sec = int(data[5] or 0)
                if total_sec <= 0:
                    continue
                duration = f"{total_sec//60}:{total_sec%60:02d}"
                full_id = f"{owner_id}_{audio_id}"
                if full_id in seen:
                    continue
                seen.add(full_id)
                try:
                    oi = int(owner_id)
                    owner_disp = f"club{abs(oi)}" if oi < 0 else f"id{oi}"
                except ValueError:
                    owner_disp = owner_id
                results.append((artist[:80], title[:120], duration, owner_disp, link, full_id))
                if max_count and len(results) >= max_count:
                    break
            except Exception:
                continue
        return results

    def _extract_dom_audio(self) -> list:
        """Считывает аудио из React-fiber всех отрисованных аудио-элементов
        (пост + комментарии). Возвращает [(artist,title,dur,owner,url,full_id)]."""
        js = r"""
return (function(){
    function fk(el){return Object.keys(el).find(k=>k.startsWith('__reactFiber')||k.startsWith('__reactInternalInstance'));}
    // Аудио-объект ВК: обязательно owner_id+id и характерные поля artist+duration
    function isAudio(o){
        return o&&typeof o==='object'&&!Array.isArray(o)
               &&o.owner_id!=null&&o.id!=null
               &&('artist' in o)&&('duration' in o);
    }
    var budget=20000;
    function scan(o,depth){
        if(budget-- <=0||!o||typeof o!=='object'||depth>3) return null;
        if(isAudio(o)) return o;
        if(Array.isArray(o)){
            for(var i=0;i<o.length&&i<50;i++){var r=scan(o[i],depth+1); if(r) return r;}
            return null;
        }
        for(var key in o){
            if(key[0]==='_'||key[0]==='$') continue;
            var v; try{v=o[key];}catch(e){continue;}
            if(v&&typeof v==='object'){var r=scan(v,depth+1); if(r) return r;}
        }
        return null;
    }
    var res=[],seen={};
    var sel='[data-testid="secondaryattachment"],[data-testid="comment_attach_audio"],'
           +'[data-testid*="udio"],[class*="udio"],[class*="Audio"]';
    var nodes=document.querySelectorAll(sel);
    for(var n=0;n<nodes.length;n++){
        var el=nodes[n],k=fk(el); if(!k) continue;
        var cur=el[k],found=null;
        for(var d=0;d<25&&cur&&!found;d++){
            if(cur.memoizedProps) found=scan(cur.memoizedProps,0);
            cur=cur.return;
        }
        if(found){
            var fid=found.owner_id+'_'+found.id;
            if(!seen[fid]){seen[fid]=1;
                var art=found.artist;
                if(!art&&found.main_artists&&found.main_artists.length)
                    art=found.main_artists.map(function(a){return a.name;}).join(', ');
                res.push([art||'',found.title||'',found.duration||0,
                          ''+found.owner_id,''+found.id,found.url||'']);
            }
        }
    }
    return JSON.stringify(res);
})()
"""
        try:
            raw = self.driver.execute_script(js)
            items = json.loads(raw) if raw else []
        except Exception as e:
            log_message(f"WARNING dom audio: {e}")
            return []
        results, seen = [], set()
        for art, title, dur, owner_id, aid, url in items:
            full_id = f"{owner_id}_{aid}"
            if not title or full_id in seen:
                continue
            seen.add(full_id)
            try:
                sec = int(dur)
            except (TypeError, ValueError):
                sec = 0
            dur_str = f"{sec//60}:{sec%60:02d}" if sec else ""
            try:
                oi = int(owner_id)
                owner_disp = f"club{abs(oi)}" if oi < 0 else f"id{oi}"
            except ValueError:
                owner_disp = str(owner_id)
            results.append((str(art)[:80], str(title)[:120], dur_str,
                            owner_disp, url or "", full_id))
        return results

    # ── Утилиты ──────────────────────────────────────────────────────────────

