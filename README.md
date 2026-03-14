# SmartTubeDL

**SmartTubeDL** — настольное приложение для поиска и загрузки видео с YouTube.
Главная фишка — полноценный поиск с фильтрами, сортировкой и просмотром каналов/плейлистов прямо в интерфейсе, а не просто вставка ссылки.

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![PyQt6](https://img.shields.io/badge/UI-PyQt6-green)
![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey)
![License](https://img.shields.io/badge/License-MIT-yellow)

---

## Возможности

### Поиск
- Поиск по YouTube через официальный API, Invidious или yt-dlp (без API-ключа)
- Фильтры: тип (видео / канал / плейлист), сортировка по релевантности / дате / просмотрам
- Сортировка результатов по названию, длительности, каналу прямо в таблице
- Просмотр полного списка видео канала или плейлиста с выбором нужных
- Поиск по описаниям видео

### Загрузка
- Форматы: MP4 (видео) и MP3 (аудио)
- Качество видео: 1080p / 720p / 480p
- Очередь загрузок — можно добавлять несколько видео сразу
- Автоматический перехват ссылок YouTube из буфера обмена
- История загрузок с возможностью повторной загрузки

### Интерфейс
- Живёт в системном трее, не занимает место на панели задач
- Уведомления о начале и завершении загрузки
- Окно отладки с цветными логами в реальном времени
- Минималистичный дизайн (PyQt6)

---

## Скриншоты

> *(будут добавлены)*

---

## Требования

- **Windows 10/11**
- **Python 3.10+**
- **ffmpeg** — для конвертации в MP3/MP4
  Скачать: https://ffmpeg.org/download.html
  Добавить в PATH или положить рядом с программой
- **Node.js** (опционально) — для загрузки некоторых видео YouTube
  Скачать: https://nodejs.org

---

## Установка

```bash
git clone https://github.com/DumbVibeCode/SmartTubeDL.git
cd SmartTubeDL
pip install -r requirements.txt
```

Скопируй файл настроек:
```bash
copy settings.json.example settings.json
```

Запуск:
```bash
python app_qt.py
```

---

## Настройка

Открой `settings.json` и при необходимости укажи:

| Параметр | Описание |
|---|---|
| `download_folder` | Папка для сохранения файлов |
| `youtube_api_key` | API-ключ YouTube Data v3 (необязательно — без него поиск работает через yt-dlp) |
| `invidious_url` | URL локального сервера Invidious (необязательно) |
| `download_format` | `"mp4"` или `"mp3"` |
| `video_quality` | `"1080p"`, `"720p"` или `"480p"` |

Получить YouTube API ключ: https://console.cloud.google.com → YouTube Data API v3

---

## База данных (необязательно)

Если хочешь кэшировать описания видео в PostgreSQL, скопируй и заполни:
```bash
copy config.json.example config.json
```

Без этого программа работает в полном объёме.

---

## Стек

- [PyQt6](https://pypi.org/project/PyQt6/) — интерфейс
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — загрузка видео
- [ffmpeg](https://ffmpeg.org) — конвертация
- [aiohttp](https://docs.aiohttp.org) — асинхронные запросы к API

---

## Лицензия

MIT
