"""
Локализация интерфейса (RU / EN) и управление редакциями SmartTubeDL (full / youtube).

Приоритет определения редакции:
  1) явный вызов set_edition(...) (например, из аргумента --edition youtube|full)
  2) переменная окружения SMARTTUBEDL_EDITION
  3) файл edition.txt рядом с приложением (удобно для релизных сборок)
  4) значение по умолчанию: "full"
"""

import locale
import os
import sys

_EDITION: str | None = None
_LANGUAGE: str | None = None


def _detect_edition() -> str:
    # 1. CLI аргумент --edition <name>
    argv = sys.argv[1:]
    for i, arg in enumerate(argv):
        if arg == "--edition" and i + 1 < len(argv):
            val = argv[i + 1].strip().lower()
            if val in ("full", "youtube"):
                return val
        elif arg.startswith("--edition="):
            val = arg.split("=", 1)[1].strip().lower()
            if val in ("full", "youtube"):
                return val

    # 2. Переменная окружения
    env_val = os.environ.get("SMARTTUBEDL_EDITION", "").strip().lower()
    if env_val in ("full", "youtube"):
        return env_val

    # 3. Файл-маркер edition.txt рядом с приложением
    marker = os.path.join(os.path.dirname(os.path.abspath(__file__)), "edition.txt")
    if os.path.isfile(marker):
        try:
            with open(marker, "r", encoding="utf-8") as f:
                val = f.read().strip().lower()
                if val in ("full", "youtube"):
                    return val
        except OSError:
            pass

    return "full"


def get_edition() -> str:
    global _EDITION
    if _EDITION is None:
        _EDITION = _detect_edition()
    return _EDITION


def set_edition(edition: str) -> None:
    global _EDITION
    val = (edition or "full").strip().lower()
    _EDITION = val if val in ("full", "youtube") else "full"


def is_youtube_only() -> bool:
    return get_edition() == "youtube"


def _detect_system_lang() -> str:
    if is_youtube_only():
        return "en"
    try:
        loc = (locale.getlocale()[0] or "").lower()
        if any(loc.startswith(p) for p in ("ru", "uk", "be", "kk", "russian")):
            return "ru"
    except Exception:
        pass
    return "ru"


def get_language() -> str:
    global _LANGUAGE
    if _LANGUAGE is None:
        try:
            from config import settings
            pref = str(settings.get("language", "auto") or "auto").strip().lower()
        except Exception:
            pref = "auto"
        _LANGUAGE = pref if pref in ("ru", "en") else _detect_system_lang()
    return _LANGUAGE


def set_language(lang: str) -> None:
    global _LANGUAGE
    val = (lang or "auto").strip().lower()
    _LANGUAGE = val if val in ("ru", "en") else _detect_system_lang()


# ── Словари переводов ────────────────────────────────────────────────────────

_STRINGS: dict[str, dict[str, str]] = {
    "ru": {
        # Боковая панель
        "nav_youtube": "▶  YouTube",
        "nav_vk": "🎵  ВКонтакте",
        "nav_ok": "🟠  Одноклассники",
        "nav_mailru": "✉  Mail.ru",
        "nav_music": "🔎  Поиск музыки",
        "nav_links": "🔗  По ссылкам",
        "nav_queue": "📥  Очередь",
        "nav_queue_n": "📥  Очередь ({n})",
        "nav_history": "🕘  История",
        "nav_settings": "⚙  Настройки",
        "nav_logs": "📋  Логи",

        # Футер и мини-плеер
        "status_idle": "Ожидание...",
        "btn_pause": "⏸ Пауза",
        "btn_resume": "▶ Продолжить",
        "tip_open_folder": "Открыть папку загрузок в Проводнике",
        "preview_play_tip": "Воспроизвести / Пауза (Пробел)",
        "preview_prev_tip": "Предыдущий трек",
        "preview_next_tip": "Следующий трек",
        "preview_stop_tip": "Остановить",
        "preview_vol_tip": "Громкость предпрослушивания",
        "preview_close_tip": "Закрыть мини-плеер",
        "preview_loading": "Загрузка...",
        "preview_buffer": "Буфер...",
        "preview_error": "Ошибка",

        # Трей
        "tray_open": "Открыть SmartTubeDL",
        "tray_youtube": "Поиск YouTube",
        "tray_vk": "Музыка ВКонтакте",
        "tray_ok": "Одноклассники",
        "tray_links": "Загрузить по ссылкам",
        "tray_queue": "Очередь загрузок",
        "tray_history": "История загрузок",
        "tray_settings": "Настройки",
        "tray_logs": "Логи",
        "tray_format": "Формат загрузки",
        "tray_quality": "Качество видео",
        "tray_auto_capture": "Автоперехват ссылок",
        "tray_convert": "Конвертация (MP3/MP4)",
        "tray_desc": "Скачивать с описаниями",
        "tray_folder": "Открыть папку загрузок",
        "tray_restart": "Перезапустить",
        "tray_exit": "Выход",

        # Раздел «По ссылкам»
        "links_title": "Загрузка по ссылкам",
        "links_hint_full": "Вставьте одну или несколько ссылок (YouTube, каналы/плейлисты, VK, OK, Mail.ru, Неизвестный Гений) — по одной на строку:",
        "links_hint_yt": "Вставьте одну или несколько ссылок YouTube (видео, каналы или плейлисты) — по одной на строку:",
        "links_placeholder_full": "https://www.youtube.com/watch?v=...\nhttps://www.youtube.com/@channel\nhttps://my.mail.ru/music/...",
        "links_placeholder_yt": "https://www.youtube.com/watch?v=...\nhttps://www.youtube.com/@channel\nhttps://www.youtube.com/playlist?list=...",
        "links_add_btn": "Добавить в очередь",
        "links_paste_btn": "Вставить из буфера",
        "links_clear_btn": "Очистить",
        "links_no_valid": "Нет валидных ссылок.",
        "links_done": "Готово: {parts}",

        # Раздел «История»
        "history_title": "История загрузок",
        "history_search_ph": "Поиск по названию...",
        "history_all_sources": "Все источники",
        "history_all_formats": "Все форматы",
        "btn_refresh": "Обновить",
        "col_date": "Дата",
        "col_source": "Источник",
        "col_title": "Название",
        "col_format": "Формат",
        "col_duration": "Длительность",
        "btn_dl_selected": "Скачать выбранные",
        "btn_open_dl_folder": "Открыть папку загрузок",
        "btn_export_txt": "Экспорт (.txt)",
        "btn_delete_selected": "Удалить выбранные",
        "history_shown": "Показано: {shown} из {total}",
        "ctx_dl_again": "⬇ Скачать выбранные заново",
        "ctx_reveal_file": "📂 Показать файл в Проводнике",
        "ctx_copy_urls": "Копировать ссылки / пути",
        "ctx_copy_titles": "Копировать названия",
        "ctx_del_history": "🗑 Удалить выбранные из истории",

        # Раздел «Настройки»
        "settings_title": "Настройки и обновления",
        "settings_tab_main": "Основные настройки",
        "settings_tab_updates": "Обновления утилит",
        "settings_folder_lbl": "Папка загрузок:",
        "btn_browse": "Обзор...",
        "settings_format_lbl": "Формат:",
        "settings_quality_lbl": "Качество видео:",
        "settings_cookies_lbl": "Браузер для cookies YouTube:",
        "settings_lang_lbl": "Язык / Language:",
        "settings_lang_auto": "Авто (по системе)",
        "settings_restart_hint": "Перезапустите приложение для применения языка",
        "settings_restart_btn": "Перезапустить сейчас",
        "settings_rate_lbl": "Лимит скорости (YouTube):",
        "settings_rate_unlimited": "Без ограничений",
        "settings_retries_lbl": "Повторов при обрыве:",
        "settings_chk_auto": "Автоперехват ссылок YouTube из буфера обмена",
        "settings_chk_conv": "Включить конвертацию (MP3 / MP4)",
        "settings_chk_tags": "Дописывать / чинить ID3-теги MP3 (UTF-16, если пустые или битые)",
        "settings_chk_desc": "Скачивать с описаниями (сохранять .txt рядом)",
        "settings_chk_save_exit": "Сохранять настройки при выходе",
        "settings_org_hdr": "Сортировка по подпапкам и шаблоны имён",
        "settings_subfolders_lbl": "Подпапки:",
        "settings_filename_lbl": "Имя файла:",
        "sub_none": "Всё в одну папку",
        "sub_source_full": "По источнику (YouTube / VK / OK / Mail.ru)",
        "sub_source_yt": "По источнику (YouTube)",
        "sub_author": "По каналу / исполнителю",
        "sub_source_author": "Источник / Канал (исполнитель)",
        "tpl_default": "По умолчанию (Музыка: Автор - Название; YouTube: Название)",
        "tpl_default_yt": "По умолчанию (Название)",
        "tpl_author_title": "Автор - Название",
        "tpl_title_only": "Только название",
        "tpl_date_title": "ГГГГ-ММ-ДД - Название",
        "settings_example": "Пример: {path}",
        "settings_yt_api_hdr": "YouTube API / Invidious (необязательно — поиск работает и через yt-dlp)",

        # Раздел «YouTube»
        "yt_search_ph": "Поиск на YouTube (запрос или ссылка на канал/плейлист)...",
        "yt_btn_search": "Найти",
        "yt_btn_settings": "Настройки",
        "yt_tip_history": "История запросов",
        "yt_tip_theme": "Переключить тёмную/светлую тему",
        "yt_filter_ph": "Фильтр по названию, каналу...",
        "yt_col_title": "Название",
        "yt_col_channel": "Канал",
        "yt_col_duration": "Длительность",
        "yt_tab_search": "Поиск",
        "yt_tab_new": "Новая вкладка",
        "yt_lbl_type": "Тип:",
        "yt_lbl_sort": "Сортировка:",
        "yt_lbl_count": "Кол-во:",
        "yt_chk_thumbs": "Превью",
        "yt_chk_save": "Сохранять",
        "yt_btn_start": "Запустить",
        "yt_lbl_desc_filter": "Фильтр в описаниях:",
        "yt_desc_filter_ph": "Слово или фраза в описании видео (оставьте пустым, чтобы не фильтровать)...",
        "yt_status_ready": "Готово к поиску",
        "yt_btn_save_list": "Сохранить список",
        "yt_btn_load_list": "Загрузить список",
        "yt_btn_dl_selected": "⬇ Скачать выбранные",
        "yt_ctx_copy_url": "Копировать URL",
        "yt_ctx_add_queue": "Добавить в очередь",
        "yt_ctx_open_browser": "Открыть в браузере",
        "yt_ctx_watch": "Смотреть видео",
        "yt_ctx_show_desc": "Показать описание",

        # Раздел «Очередь»
        "queue_title": "Очередь загрузок",
        "queue_col_status": "Статус",
        "queue_col_title": "Название",
        "queue_btn_pause": "Пауза",
        "queue_btn_resume": "Возобновить",
        "queue_btn_delete": "Удалить выбранное",
        "queue_btn_import": "Импорт (.txt)",
        "queue_btn_export": "Экспорт (.txt)",
        "queue_btn_clear": "Очистить",
        "queue_st_dl": "↓ Загружается",
        "queue_st_wait": "⏳ Ожидание",
        "queue_st_pause": "⏸ На паузе",

        # Раздел «Обновления утилит»
        "upd_title": "Версии и обновления утилит",
        "upd_col_comp": "Компонент",
        "upd_col_purpose": "Назначение",
        "upd_col_installed": "Установлено",
        "upd_col_latest": "Доступно",
        "upd_col_status": "Статус",
        "upd_log_ph": "Здесь появится вывод процесса обновления...",
        "upd_chk_auto": "Автопроверка раз в 7 дней",
        "upd_btn_check": "Проверить",
        "upd_btn_pip": "Обновить yt-dlp и модули",
        "upd_btn_winget": "Обновить ffmpeg / Node (winget)",
        "upd_checking": "Проверяю установленные версии и PyPI...",
        "upd_done": "Проверка завершена.",
        "upd_st_no_pypi": "нет связи с PyPI",
        "upd_st_avail": "есть обновление",
        "upd_st_ok": "актуально",
        "upd_st_installed": "установлен",
        "upd_st_missing": "не найден",
        "upd_via_winget": "через winget",
        "upd_desc_ytdlp_full": "Загрузка YouTube / VK / OK",
        "upd_desc_ytdlp_yt": "Загрузка видео и аудио YouTube",
        "upd_desc_ejs": "Решение YouTube n-challenge",
        "upd_desc_curl": "TLS-имперсонация запросов",
        "upd_desc_ffmpeg": "Конвертация MP3 / MP4",
        "upd_desc_node": "Решение YouTube n-challenge",

        # Раздел «Логи»
        "logs_title": "Логи приложения",
        "logs_btn_all": "Все",
        "logs_chk_wrap": "Перенос строк",
        "logs_btn_clear": "Очистить лог",
        "logs_btn_min": "Свернуть",
    },

    "en": {
        # Sidebar
        "nav_youtube": "▶  YouTube",
        "nav_vk": "🎵  VK Music",
        "nav_ok": "🟠  OK.ru",
        "nav_mailru": "✉  Mail.ru",
        "nav_music": "🔎  Music Search",
        "nav_links": "🔗  From Links",
        "nav_queue": "📥  Queue",
        "nav_queue_n": "📥  Queue ({n})",
        "nav_history": "🕘  History",
        "nav_settings": "⚙  Settings",
        "nav_logs": "📋  Logs",

        # Footer & mini-player
        "status_idle": "Idle...",
        "btn_pause": "⏸ Pause",
        "btn_resume": "▶ Resume",
        "tip_open_folder": "Open downloads folder in Explorer",
        "preview_play_tip": "Play / Pause (Space)",
        "preview_prev_tip": "Previous track",
        "preview_next_tip": "Next track",
        "preview_stop_tip": "Stop",
        "preview_vol_tip": "Preview volume",
        "preview_close_tip": "Close mini-player",
        "preview_loading": "Loading...",
        "preview_buffer": "Buffering...",
        "preview_error": "Error",

        # Tray
        "tray_open": "Open SmartTubeDL",
        "tray_youtube": "YouTube Search",
        "tray_vk": "VK Music",
        "tray_ok": "OK.ru",
        "tray_links": "Download from Links",
        "tray_queue": "Download Queue",
        "tray_history": "Download History",
        "tray_settings": "Settings",
        "tray_logs": "Logs",
        "tray_format": "Download Format",
        "tray_quality": "Video Quality",
        "tray_auto_capture": "Auto-capture Clipboard Links",
        "tray_convert": "Convert (MP3/MP4)",
        "tray_desc": "Save Video Descriptions",
        "tray_folder": "Open Downloads Folder",
        "tray_restart": "Restart",
        "tray_exit": "Exit",

        # Links Page
        "links_title": "Download from Links",
        "links_hint_full": "Paste one or more URLs (YouTube, channels/playlists, VK, OK, Mail.ru) — one per line:",
        "links_hint_yt": "Paste one or more YouTube URLs (videos, channels, or playlists) — one per line:",
        "links_placeholder_full": "https://www.youtube.com/watch?v=...\nhttps://www.youtube.com/@channel\nhttps://my.mail.ru/music/...",
        "links_placeholder_yt": "https://www.youtube.com/watch?v=...\nhttps://www.youtube.com/@channel\nhttps://www.youtube.com/playlist?list=...",
        "links_add_btn": "Add to Queue",
        "links_paste_btn": "Paste from Clipboard",
        "links_clear_btn": "Clear",
        "links_no_valid": "No valid URLs found.",
        "links_done": "Done: {parts}",

        # History Page
        "history_title": "Download History",
        "history_search_ph": "Search by title...",
        "history_all_sources": "All Sources",
        "history_all_formats": "All Formats",
        "btn_refresh": "Refresh",
        "col_date": "Date",
        "col_source": "Source",
        "col_title": "Title",
        "col_format": "Format",
        "col_duration": "Duration",
        "btn_dl_selected": "Download Selected",
        "btn_open_dl_folder": "Open Downloads Folder",
        "btn_export_txt": "Export (.txt)",
        "btn_delete_selected": "Delete Selected",
        "history_shown": "Shown: {shown} of {total}",
        "ctx_dl_again": "⬇ Download Selected Again",
        "ctx_reveal_file": "📂 Show File in Explorer",
        "ctx_copy_urls": "Copy Links / Paths",
        "ctx_copy_titles": "Copy Titles",
        "ctx_del_history": "🗑 Delete Selected from History",

        # Settings Page
        "settings_title": "Settings & Updates",
        "settings_tab_main": "General Settings",
        "settings_tab_updates": "Tool Updates",
        "settings_folder_lbl": "Downloads Folder:",
        "btn_browse": "Browse...",
        "settings_format_lbl": "Format:",
        "settings_quality_lbl": "Video Quality:",
        "settings_cookies_lbl": "Browser for YouTube cookies:",
        "settings_lang_lbl": "Language / Язык:",
        "settings_lang_auto": "Auto (system)",
        "settings_restart_hint": "Restart the app to apply the new language",
        "settings_restart_btn": "Restart Now",
        "settings_rate_lbl": "Speed Limit (YouTube):",
        "settings_rate_unlimited": "Unlimited",
        "settings_retries_lbl": "Retries on Error:",
        "settings_chk_auto": "Auto-capture YouTube links from clipboard",
        "settings_chk_conv": "Enable conversion (MP3 / MP4)",
        "settings_chk_tags": "Fix / write MP3 ID3 tags (UTF-16 when missing or broken)",
        "settings_chk_desc": "Download video descriptions (.txt alongside video)",
        "settings_chk_save_exit": "Save settings on exit",
        "settings_org_hdr": "Subfolders & Filename Templates",
        "settings_subfolders_lbl": "Subfolders:",
        "settings_filename_lbl": "Filename:",
        "sub_none": "All in one folder",
        "sub_source_full": "By source (YouTube / VK / OK / Mail.ru)",
        "sub_source_yt": "By source (YouTube)",
        "sub_author": "By channel / artist",
        "sub_source_author": "Source / Channel (Artist)",
        "tpl_default": "Default (Music: Artist - Title; YouTube: Title)",
        "tpl_default_yt": "Default (Title)",
        "tpl_author_title": "Author - Title",
        "tpl_title_only": "Title only",
        "tpl_date_title": "YYYY-MM-DD - Title",
        "settings_example": "Example: {path}",
        "settings_yt_api_hdr": "YouTube API / Invidious (optional — search also works via yt-dlp)",

        # YouTube Page
        "yt_search_ph": "Search YouTube (query or channel/playlist URL)...",
        "yt_btn_search": "Search",
        "yt_btn_settings": "Options",
        "yt_tip_history": "Search history",
        "yt_tip_theme": "Toggle dark/light theme",
        "yt_filter_ph": "Filter by title, channel...",
        "yt_col_title": "Title",
        "yt_col_channel": "Channel",
        "yt_col_duration": "Duration",
        "yt_tab_search": "Search",
        "yt_tab_new": "New Tab",
        "yt_lbl_type": "Type:",
        "yt_lbl_sort": "Sort:",
        "yt_lbl_count": "Count:",
        "yt_chk_thumbs": "Thumbnails",
        "yt_chk_save": "Save settings",
        "yt_btn_start": "Start",
        "yt_lbl_desc_filter": "Description filter:",
        "yt_desc_filter_ph": "Word or phrase in video description (leave empty to skip)...",
        "yt_status_ready": "Ready to search",
        "yt_btn_save_list": "Save List",
        "yt_btn_load_list": "Load List",
        "yt_btn_dl_selected": "⬇ Download Selected",
        "yt_ctx_copy_url": "Copy URL",
        "yt_ctx_add_queue": "Add to Queue",
        "yt_ctx_open_browser": "Open in Browser",
        "yt_ctx_watch": "Watch Video",
        "yt_ctx_show_desc": "Show Description",

        # Queue Page
        "queue_title": "Download Queue",
        "queue_col_status": "Status",
        "queue_col_title": "Title",
        "queue_btn_pause": "Pause",
        "queue_btn_resume": "Resume",
        "queue_btn_delete": "Delete Selected",
        "queue_btn_import": "Import (.txt)",
        "queue_btn_export": "Export (.txt)",
        "queue_btn_clear": "Clear",
        "queue_st_dl": "↓ Downloading",
        "queue_st_wait": "⏳ Queued",
        "queue_st_pause": "⏸ Paused",

        # Tool Updates Page
        "upd_title": "Tool Versions & Updates",
        "upd_col_comp": "Component",
        "upd_col_purpose": "Purpose",
        "upd_col_installed": "Installed",
        "upd_col_latest": "Available",
        "upd_col_status": "Status",
        "upd_log_ph": "Update process output will appear here...",
        "upd_chk_auto": "Auto-check every 7 days",
        "upd_btn_check": "Check",
        "upd_btn_pip": "Update yt-dlp & modules",
        "upd_btn_winget": "Update ffmpeg / Node (winget)",
        "upd_checking": "Checking installed versions and PyPI...",
        "upd_done": "Check completed.",
        "upd_st_no_pypi": "PyPI unreachable",
        "upd_st_avail": "update available",
        "upd_st_ok": "up to date",
        "upd_st_installed": "installed",
        "upd_st_missing": "not found",
        "upd_via_winget": "via winget",
        "upd_desc_ytdlp_full": "YouTube / VK / OK downloader",
        "upd_desc_ytdlp_yt": "YouTube video & audio downloader",
        "upd_desc_ejs": "YouTube n-challenge solver",
        "upd_desc_curl": "TLS impersonation for HTTP requests",
        "upd_desc_ffmpeg": "MP3 / MP4 conversion",
        "upd_desc_node": "YouTube n-challenge runtime",

        # Logs Page
        "logs_title": "Application Logs",
        "logs_btn_all": "All",
        "logs_chk_wrap": "Word wrap",
        "logs_btn_clear": "Clear Log",
        "logs_btn_min": "Minimize",
    },
}


def tr(key: str, **kwargs) -> str:
    """Возвращает переведённую строку для текущего языка."""
    lang = get_language()
    table = _STRINGS.get(lang) or _STRINGS["ru"]
    text = table.get(key) or _STRINGS["ru"].get(key) or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except Exception:
            return text
    return text


_STATUS_EXACT_EN = {
    "Ожидание...": "Idle...",
    "Готово!": "Done!",
    "Готово": "Done",
    "Готово к поиску": "Ready to search",
    "Поиск...": "Searching...",
    "Загрузка...": "Downloading...",
    "На паузе": "Paused",
    "Отмена загрузки...": "Cancelling download...",
    "Загрузка отменена": "Download cancelled",
    "Ничего не найдено": "Nothing found",
    "Введите поисковый запрос!": "Enter a search query!",
    "Открыта вкладка плейлиста": "Playlist tab opened",
    "Открыта вкладка канала": "Channel tab opened",
    "Укажите YouTube API Key или используйте альтернативный поиск": "Specify YouTube API Key or enable alternative search",
    "Нет результатов для сохранения": "No results to save",
    "URL скопирован": "URL copied",
    "Открыто в браузере": "Opened in browser",
    "URL не найден": "URL not found",
    "Открыт видеоплеер": "Video player opened",
    "Выберите видео для загрузки": "Select videos to download",
    "Видео уже в очереди": "Already in queue",
}

_STATUS_PREFIX_EN = (
    ("Найдено: ", "Found: "),
    ("Восстановлено: ", "Restored: "),
    ("Добавлено в очередь: ", "Added to queue: "),
    ("Сохранено: ", "Saved: "),
    ("Загружено: ", "Loaded: "),
    ("Ошибка сохранения: ", "Save error: "),
    ("Ошибка загрузки: ", "Load error: "),
    ("Ошибка: ", "Error: "),
    ("Фильтр: ", "Filter: "),
)


def translate_status(text: str) -> str:
    """Переводит динамические статусные строки (для EN-режима)."""
    if not text or get_language() != "en":
        return text
    s = str(text)
    if s in _STATUS_EXACT_EN:
        return _STATUS_EXACT_EN[s]
    for ru_p, en_p in _STATUS_PREFIX_EN:
        if s.startswith(ru_p):
            rest = s[len(ru_p):]
            rest = rest.replace(" из ", " of ").replace(" результатов", " results")
            return en_p + rest
    return s

