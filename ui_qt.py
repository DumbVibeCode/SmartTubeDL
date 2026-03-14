"""
Окна интерфейса на PyQt6
"""

import threading
import webbrowser
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QTextEdit, QPushButton, QTableWidget,
    QTableWidgetItem, QComboBox, QCheckBox, QProgressBar,
    QGroupBox, QMessageBox, QMenu, QSplitter, QHeaderView,
    QAbstractItemView
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QAction, QClipboard, QGuiApplication

from styles import STYLESHEET_MINIMAL as STYLESHEET, COLORS_MINIMAL as COLORS
from config import settings, save_settings
from logger import log_message
from queues import add_to_queue, process_queue
from config import is_downloading


class DurationTableWidgetItem(QTableWidgetItem):
    """Элемент таблицы для длительности с правильной сортировкой"""

    def __init__(self, duration_str: str):
        super().__init__(duration_str)
        self.duration_seconds = self._parse_duration(duration_str)

    def _parse_duration(self, duration_str: str) -> int:
        """Преобразует строку длительности в секунды для сортировки"""
        if not duration_str or duration_str.strip() == "":
            return 0

        # Если это число (количество видео в плейлисте), возвращаем как есть
        if duration_str.isdigit():
            return int(duration_str) * 10000  # Большой множитель, чтобы плейлисты были в конце

        try:
            parts = duration_str.split(':')
            if len(parts) == 3:  # HH:MM:SS
                hours, minutes, seconds = map(int, parts)
                return hours * 3600 + minutes * 60 + seconds
            elif len(parts) == 2:  # MM:SS
                minutes, seconds = map(int, parts)
                return minutes * 60 + seconds
            elif len(parts) == 1:  # SS
                return int(parts[0])
        except (ValueError, AttributeError):
            return 0

        return 0

    def __lt__(self, other):
        """Сравнение для сортировки по секундам, а не по строке"""
        if isinstance(other, DurationTableWidgetItem):
            return self.duration_seconds < other.duration_seconds
        return super().__lt__(other)


class SearchWorker(QThread):
    """Поток для выполнения поиска"""
    finished = pyqtSignal(list)  # Результаты поиска
    error = pyqtSignal(str)      # Ошибка
    progress = pyqtSignal(int)   # Прогресс 0-100
    status = pyqtSignal(str)     # Промежуточный статус

    def __init__(self, query, search_type, order, max_results, api_key,
                 invidious_url, use_alternative, use_ytdlp, desc_filter):
        super().__init__()
        self.query = query
        self.search_type = search_type
        self.order = order
        self.max_results = max_results
        self.api_key = api_key
        self.invidious_url = invidious_url
        self.use_alternative = use_alternative
        self.use_ytdlp = use_ytdlp
        self.desc_filter = desc_filter

    def _extract_video_id(self, url):
        if not url:
            return None
        from description import extract_video_id
        return extract_video_id(url)

    def _load_descriptions_youtube(self, results):
        """Загружает описания batch-запросом к YouTube Data API (по 50 штук)"""
        import requests
        from database import insert_description

        video_ids = []
        for r in results:
            vid_id = self._extract_video_id(r.get('url', ''))
            if vid_id:
                video_ids.append(vid_id)

        if not video_ids or not self.api_key:
            return

        for i in range(0, len(video_ids), 50):
            batch = video_ids[i:i + 50]
            try:
                resp = requests.get(
                    'https://www.googleapis.com/youtube/v3/videos',
                    params={'key': self.api_key, 'part': 'snippet', 'id': ','.join(batch)},
                    timeout=15
                )
                if resp.status_code == 200:
                    for item in resp.json().get('items', []):
                        vid_id = item['id']
                        desc = item['snippet'].get('description', '')
                        insert_description(vid_id, desc)
            except Exception as e:
                log_message(f"[ERROR] Ошибка загрузки описания (YouTube API): {e}")

    def _load_descriptions_individual(self, results):
        """Загружает описания индивидуально через yt-dlp или BeautifulSoup"""
        import concurrent.futures
        from database import insert_description

        def fetch_one(result):
            url = result.get('url', '')
            vid_id = self._extract_video_id(url)
            if not vid_id:
                return
            desc = result.get('description', '')
            placeholder = desc in ('', 'Описание отсутствует', 'Описание недоступно (не загружалось)')
            if placeholder or len(desc) < 50:
                try:
                    if self.use_ytdlp:
                        from fetch import fetch_description_with_ytdlp
                        desc = fetch_description_with_ytdlp(url)
                    else:
                        from fetch import fetch_description_with_bs
                        desc = fetch_description_with_bs(url)
                except Exception as e:
                    log_message(f"[ERROR] Ошибка загрузки описания для {url}: {e}")
                    desc = ''
            if desc:
                insert_description(vid_id, desc)

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            pool.map(fetch_one, results)

    def run(self):
        try:
            from search import search_via_youtube_api, search_via_invidious, search_via_ytdlp
            from config import format_invidious_duration

            self.progress.emit(10)
            raw_results = None

            if self.use_ytdlp:
                log_message("INFO Поиск через yt-dlp...")
                raw_results = search_via_ytdlp(
                    self.query, self.max_results, self.search_type, False
                )
            elif self.use_alternative:
                log_message("INFO Поиск через Invidious API...")
                raw_results = search_via_invidious(
                    self.query, self.invidious_url, self.max_results,
                    self.search_type, self.order, False
                )
            else:
                log_message("INFO Поиск через YouTube API...")
                raw_results = search_via_youtube_api(
                    self.query, self.api_key, self.search_type,
                    self.order, self.max_results, False
                )

            self.progress.emit(80)

            # Приводим результаты к единому формату
            results = []
            if raw_results and 'items' in raw_results:
                for item in raw_results['items']:
                    result = {
                        'title': '',
                        'channel': '',
                        'duration': '',
                        'url': '',
                        'description': ''
                    }

                    # Формат yt-dlp и Invidious
                    if 'title' in item:
                        result['title'] = item.get('title', 'Без названия')
                        result['channel'] = item.get('author', 'Неизвестный')
                        result['description'] = item.get('description', '')

                        if 'lengthSeconds' in item:
                            result['duration'] = format_invidious_duration(item['lengthSeconds'])
                        elif 'video_count' in item:
                            result['duration'] = str(item['video_count'])

                        result['url'] = item.get('video_url', '')

                    # Формат YouTube API
                    elif 'snippet' in item:
                        result['title'] = item['snippet'].get('title', 'Без названия')
                        result['channel'] = item['snippet'].get('channelTitle', 'Неизвестный')
                        result['description'] = item['snippet'].get('description', '')
                        result['url'] = item.get('video_url', '')

                        if 'duration' in item:
                            result['duration'] = item['duration']

                        if 'video_count' in item:
                            result['duration'] = str(item['video_count'])

                    if result['title']:
                        results.append(result)

            # ── Фильтрация по описаниям через SQLite FTS5 ──
            if self.desc_filter and results:
                self.progress.emit(82)
                log_message(f"INFO Фильтрация по описаниям: '{self.desc_filter}'")
                self.status.emit(f"Загрузка описаний ({len(results)} видео)...")

                from database import connect_to_database, clear_descriptions_table, \
                    search_in_database, is_connected
                if not is_connected():
                    connect_to_database()
                clear_descriptions_table()

                if self.use_ytdlp or self.use_alternative:
                    self._load_descriptions_individual(results)
                else:
                    self._load_descriptions_youtube(results)

                # Дамп всех загруженных описаний в файл для отладки
                try:
                    import database as _db
                    all_rows = _db._conn.execute(
                        "SELECT video_id, description FROM video_descriptions"
                    ).fetchall()
                    with open("descriptions_debug.txt", "w", encoding="utf-8") as f:
                        f.write(f"Запрос: {self.query}\n")
                        f.write(f"Фильтр: {self.desc_filter}\n")
                        f.write(f"Загружено описаний: {len(all_rows)}\n")
                        f.write("=" * 60 + "\n\n")
                        for vid_id, desc in all_rows:
                            f.write(f"[{vid_id}]\n")
                            f.write(desc or "(пусто)")
                            f.write("\n" + "-" * 60 + "\n\n")
                    log_message(f"INFO Описания сохранены в descriptions_debug.txt ({len(all_rows)} шт.)")
                except Exception as dump_err:
                    log_message(f"[ERROR] Не удалось сохранить дамп описаний: {dump_err}")

                self.progress.emit(95)

                db_hits = search_in_database(self.desc_filter)
                matching_ids = {vid_id for vid_id, _ in db_hits}
                results = [
                    r for r in results
                    if self._extract_video_id(r.get('url', '')) in matching_ids
                ]
                log_message(f"INFO После фильтрации по описаниям: {len(results)}")

            self.progress.emit(100)
            self.finished.emit(results)

        except Exception as e:
            log_message(f"ERROR Ошибка поиска: {e}")
            import traceback
            log_message(f"Traceback: {traceback.format_exc()}")
            self.error.emit(str(e))


class SearchWindow(QMainWindow):
    """Окно поиска YouTube"""

    def __init__(self):
        super().__init__()

        self.setWindowTitle("Поиск YouTube")
        self.setMinimumSize(1000, 700)
        self.resize(1200, 800)

        # Центрируем окно
        self._center_window()

        # Данные
        self.video_urls = {}  # {row_index: url}
        self.video_descriptions = {}
        self.search_worker = None
        self.description_windows = []  # Список открытых окон описаний
        self.player_windows = []  # Список открытых окон видеоплееров

        # Счётчик загрузок
        self.total_downloads = 0

        # Создаём интерфейс
        self._setup_ui()

        # Загружаем последние результаты
        self._load_last_results()

        log_message("INFO Окно поиска открыто")

    def _center_window(self):
        """Центрирует окно на экране"""
        screen = QGuiApplication.primaryScreen().geometry()
        x = (screen.width() - self.width()) // 2
        y = (screen.height() - self.height()) // 2
        self.move(x, y)

    def _setup_ui(self):
        """Создаёт минималистичный интерфейс"""
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        # ── Поисковая строка ──
        search_bar = QWidget()
        search_bar.setObjectName("searchBar")
        search_row = QHBoxLayout(search_bar)
        search_row.setContentsMargins(16, 12, 16, 8)
        search_row.setSpacing(8)

        self.search_input = QLineEdit()
        self.search_input.setObjectName("searchInput")
        self.search_input.setPlaceholderText("Поиск на YouTube...")
        self.search_input.setText(settings.get("last_search_query", ""))
        self.search_input.returnPressed.connect(self._on_search)
        search_row.addWidget(self.search_input, 1)

        self.search_btn = QPushButton("Найти")
        self.search_btn.setObjectName("searchBtn")
        self.search_btn.clicked.connect(self._on_search)
        search_row.addWidget(self.search_btn)

        self.settings_btn = QPushButton("Настройки")
        self.settings_btn.setProperty("secondary", True)
        self.settings_btn.setCheckable(True)
        self.settings_btn.clicked.connect(self._toggle_settings)
        search_row.addWidget(self.settings_btn)

        layout.addWidget(search_bar)

        # ── Прогресс-бар (тонкий, под строкой поиска) ──
        self.progress = QProgressBar()
        self.progress.setObjectName("thinProgress")
        self.progress.setFixedHeight(3)
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        # ── Панель настроек (скрыта по умолчанию) ──
        self.settings_panel = QWidget()
        self.settings_panel.setObjectName("settingsPanel")
        self.settings_panel.setVisible(False)
        sp_layout = QVBoxLayout(self.settings_panel)
        sp_layout.setContentsMargins(16, 8, 16, 8)
        sp_layout.setSpacing(6)

        # Строка параметров
        params_row = QHBoxLayout()
        params_row.setSpacing(12)

        params_row.addWidget(QLabel("Тип:"))
        self.type_combo = QComboBox()
        self.type_combo.addItems(["video", "channel", "playlist"])
        self.type_combo.setCurrentText(settings.get("search_type", "video"))
        self.type_combo.currentTextChanged.connect(self._on_type_changed)
        params_row.addWidget(self.type_combo)

        params_row.addWidget(QLabel("Сортировка:"))
        self.order_combo = QComboBox()
        self.order_combo.addItems(["relevance", "date", "rating", "viewCount", "title"])
        self.order_combo.setCurrentText(settings.get("sort_order", "relevance"))
        params_row.addWidget(self.order_combo)

        params_row.addWidget(QLabel("Кол-во:"))
        self.max_combo = QComboBox()
        self.max_combo.addItems(["10", "20", "30", "50", "100", "200"])
        self.max_combo.setCurrentText(settings.get("max_results", "10"))
        params_row.addWidget(self.max_combo)

        params_row.addStretch()
        sp_layout.addLayout(params_row)

        # Чекбоксы + API ключи
        opts_row = QHBoxLayout()
        opts_row.setSpacing(12)

        self.check_alternative = QCheckBox("Invidious")
        self.check_alternative.setChecked(settings.get("use_alternative_api", False))
        self.check_alternative.stateChanged.connect(self._update_checkboxes)
        opts_row.addWidget(self.check_alternative)

        self.check_ytdlp = QCheckBox("yt-dlp")
        self.check_ytdlp.setChecked(settings.get("use_ytdlp_search", False))
        self.check_ytdlp.stateChanged.connect(self._update_checkboxes)
        opts_row.addWidget(self.check_ytdlp)

        self.check_save = QCheckBox("Сохранять")
        self.check_save.setChecked(settings.get("save_settings_on_exit", False))
        opts_row.addWidget(self.check_save)

        opts_row.addStretch()

        self.api_input = QLineEdit()
        self.api_input.setPlaceholderText("API Key")
        self.api_input.setText(settings.get("youtube_api_key", ""))
        self.api_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_input.setMaximumWidth(200)
        opts_row.addWidget(self.api_input)

        self.invidious_input = QLineEdit()
        self.invidious_input.setPlaceholderText("Invidious URL")
        self.invidious_input.setText(settings.get("invidious_url", "http://localhost:3000"))
        self.invidious_input.setMaximumWidth(200)
        opts_row.addWidget(self.invidious_input)

        sp_layout.addLayout(opts_row)

        # Строка фильтра по описаниям
        desc_row = QHBoxLayout()
        desc_row.setSpacing(8)
        desc_row.addWidget(QLabel("Фильтр в описаниях:"))
        self.desc_filter_input = QLineEdit()
        self.desc_filter_input.setPlaceholderText(
            "Слово или фраза в описании видео (оставьте пустым, чтобы не фильтровать)..."
        )
        self.desc_filter_input.setText(settings.get("desc_filter", ""))
        self.desc_filter_input.setMaximumWidth(500)
        self.desc_filter_input.returnPressed.connect(self._on_search)
        desc_row.addWidget(self.desc_filter_input)
        desc_row.addStretch()
        sp_layout.addLayout(desc_row)

        layout.addWidget(self.settings_panel)

        # ── Таблица результатов (максимум площади) ──
        self.table = QTableWidget()
        self.table.setObjectName("resultsTable")
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Название", "Канал", "Длительность"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.setAlternatingRowColors(False)
        self.table.setShowGrid(False)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.doubleClicked.connect(self._on_double_click)
        self.table.verticalHeader().setVisible(False)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setHighlightSections(False)

        layout.addWidget(self.table, 1)

        # ── Нижняя панель (статус + кнопки + лог) ──
        bottom = QWidget()
        bottom.setObjectName("bottomBar")
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(16, 6, 16, 8)
        bottom_layout.setSpacing(4)

        # Строка с кнопками и статусом
        action_row = QHBoxLayout()
        action_row.setSpacing(8)

        self.download_btn = QPushButton("Скачать")
        self.download_btn.setObjectName("searchBtn")
        self.download_btn.clicked.connect(self._on_download)
        action_row.addWidget(self.download_btn)

        self.status_label = QLabel("Готово к поиску")
        self.status_label.setProperty("secondary", True)
        self.status_label.setStyleSheet(f"color: {COLORS['text_secondary']};")
        action_row.addWidget(self.status_label, 1)

        self.log_toggle_btn = QPushButton("Лог")
        self.log_toggle_btn.setProperty("secondary", True)
        self.log_toggle_btn.setCheckable(True)
        self.log_toggle_btn.clicked.connect(self._toggle_log)
        action_row.addWidget(self.log_toggle_btn)

        clear_log_btn = QPushButton("Очистить")
        clear_log_btn.setProperty("secondary", True)
        clear_log_btn.clicked.connect(self._clear_log)
        action_row.addWidget(clear_log_btn)

        bottom_layout.addLayout(action_row)

        # Прогресс загрузки
        self.download_progress = QProgressBar()
        self.download_progress.setObjectName("downloadProgress")
        self.download_progress.setFixedHeight(4)
        self.download_progress.setTextVisible(False)
        self.download_progress.setVisible(False)
        bottom_layout.addWidget(self.download_progress)

        self.download_label = QLabel("")
        self.download_label.setProperty("secondary", True)
        self.download_label.setVisible(False)
        bottom_layout.addWidget(self.download_label)

        # Лог (скрыт по умолчанию)
        self.log_text = QTextEdit()
        self.log_text.setObjectName("logPanel")
        self.log_text.setReadOnly(True)
        self.log_text.setFixedHeight(100)
        self.log_text.setVisible(False)
        bottom_layout.addWidget(self.log_text)

        layout.addWidget(bottom)

        # Таймер для обновления лога
        self.log_timer = QTimer()
        self.log_timer.timeout.connect(self._update_log)
        self.log_timer.start(1000)

    def _toggle_settings(self):
        """Сворачивает/разворачивает панель настроек"""
        visible = not self.settings_panel.isVisible()
        self.settings_panel.setVisible(visible)
        self.settings_btn.setChecked(visible)

    def _toggle_log(self):
        """Сворачивает/разворачивает панель лога"""
        visible = not self.log_text.isVisible()
        self.log_text.setVisible(visible)
        self.log_toggle_btn.setChecked(visible)

    def update_download_progress(self, status: str, progress: int):
        """Обновляет прогресс загрузки файлов в окне поиска"""
        self._update_download_bar()

    def _update_download_bar(self):
        """Обновляет прогресс-бар по количеству файлов"""
        if self.total_downloads <= 0:
            self.download_progress.setVisible(False)
            self.download_label.setVisible(False)
            return

        from queues import get_queue_count

        queue_count = get_queue_count()
        completed = self.total_downloads - queue_count

        # Всё скачано
        if completed >= self.total_downloads and not is_downloading:
            self.download_progress.setValue(100)
            self.download_label.setText(f"Скачано: {self.total_downloads} из {self.total_downloads}")
            self.download_progress.setVisible(True)
            self.download_label.setVisible(True)
            # Скрываем через 3 секунды
            QTimer.singleShot(3000, self._hide_download_bar)
            return

        # Ограничиваем, чтобы completed не превышал total
        completed = max(0, min(completed, self.total_downloads))

        percent = int((completed / self.total_downloads) * 100)
        self.download_progress.setVisible(True)
        self.download_label.setVisible(True)
        self.download_progress.setValue(percent)
        self.download_label.setText(f"Скачано {completed} из {self.total_downloads}")

    def _hide_download_bar(self):
        """Скрывает прогресс-бар загрузки"""
        self.download_progress.setVisible(False)
        self.download_label.setVisible(False)
        self.total_downloads = 0

    def _update_checkboxes(self):
        """Обновляет состояние взаимоисключающих чекбоксов"""
        if self.check_alternative.isChecked():
            self.check_ytdlp.setChecked(False)
        elif self.check_ytdlp.isChecked():
            self.check_alternative.setChecked(False)

    def _on_type_changed(self, text):
        """Обработка смены типа поиска"""
        is_video = text == "video"
        self.desc_filter_input.setEnabled(is_video)
        if not is_video:
            self.desc_filter_input.clear()

    def _on_search(self):
        """Запускает поиск"""
        query = self.search_input.text().strip()
        if not query:
            self._set_status("Введите поисковый запрос!", "error")
            return

        # Сохраняем настройки
        settings["last_search_query"] = query
        settings["search_type"] = self.type_combo.currentText()
        settings["sort_order"] = self.order_combo.currentText()
        settings["max_results"] = self.max_combo.currentText()
        settings["youtube_api_key"] = self.api_input.text()
        settings["invidious_url"] = self.invidious_input.text()
        settings["use_alternative_api"] = self.check_alternative.isChecked()
        settings["use_ytdlp_search"] = self.check_ytdlp.isChecked()
        settings["desc_filter"] = self.desc_filter_input.text().strip()
        settings["save_settings_on_exit"] = self.check_save.isChecked()

        # Проверяем API ключ
        if not self.check_alternative.isChecked() and not self.check_ytdlp.isChecked():
            if not self.api_input.text().strip():
                self._set_status("Укажите YouTube API Key или используйте альтернативный метод", "warning")
                return

        # Запускаем поиск в отдельном потоке
        self._set_status("Поиск...", "info")
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.search_btn.setEnabled(False)

        self.search_worker = SearchWorker(
            query=query,
            search_type=self.type_combo.currentText(),
            order=self.order_combo.currentText(),
            max_results=self.max_combo.currentText(),
            api_key=self.api_input.text(),
            invidious_url=self.invidious_input.text(),
            use_alternative=self.check_alternative.isChecked(),
            use_ytdlp=self.check_ytdlp.isChecked(),
            desc_filter=self.desc_filter_input.text().strip()
        )

        self.search_worker.finished.connect(self._on_search_finished)
        self.search_worker.error.connect(self._on_search_error)
        self.search_worker.progress.connect(self.progress.setValue)
        self.search_worker.status.connect(lambda msg: self._set_status(msg, "info") if msg else None)
        self.search_worker.start()

    def _on_search_finished(self, results):
        """Обработка результатов поиска"""
        self.search_btn.setEnabled(True)
        self.progress.setVisible(False)

        if not results:
            self._set_status("Ничего не найдено", "warning")
            return

        # Очищаем таблицу
        self.table.setRowCount(0)
        self.video_urls.clear()
        self.video_descriptions.clear()

        # Заполняем таблицу
        for item in results:
            row = self.table.rowCount()
            self.table.insertRow(row)

            title = item.get("title", "Без названия")
            channel = item.get("channel", item.get("author", ""))
            duration = item.get("duration", "")
            url = item.get("url", "")
            description = item.get("description", "Описание отсутствует")

            # Отладочное логирование
            log_message(f"DEBUG UI: Saving description for row {row}, length: {len(description)}")

            self.table.setItem(row, 0, QTableWidgetItem(title))
            self.table.setItem(row, 1, QTableWidgetItem(channel))
            self.table.setItem(row, 2, DurationTableWidgetItem(duration))

            self.video_urls[row] = url
            self.video_descriptions[row] = description

        self._set_status(f"Найдено: {len(results)}", "success")

        # Сохраняем результаты
        settings["last_search_results"] = results
        log_message(f"INFO Найдено {len(results)} результатов")

    def _on_search_error(self, error):
        """Обработка ошибки поиска"""
        self.search_btn.setEnabled(True)
        self.progress.setVisible(False)
        self._set_status(f"Ошибка: {error}", "error")

    def _load_last_results(self):
        """Загружает последние результаты поиска"""
        results = settings.get("last_search_results", [])
        if results:
            self._on_search_finished(results)
            self._set_status(f"Восстановлено: {len(results)}", "info")

    def _set_status(self, text, status_type="info"):
        """Устанавливает текст статуса"""
        colors = {
            "info": COLORS["info"],
            "success": COLORS["success"],
            "warning": COLORS["warning"],
            "error": COLORS["error"],
        }
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {colors.get(status_type, COLORS['info'])};")

    def _show_context_menu(self, pos):
        """Показывает контекстное меню"""
        if not self.table.selectedItems():
            return

        menu = QMenu(self)

        action_copy = menu.addAction("Копировать URL")
        action_copy.triggered.connect(self._copy_url)

        action_download = menu.addAction("Добавить в очередь")
        action_download.triggered.connect(self._on_download)

        action_browser = menu.addAction("Открыть в браузере")
        action_browser.triggered.connect(self._open_in_browser)

        action_watch = menu.addAction("Смотреть видео")
        action_watch.triggered.connect(self._watch_video)

        menu.addSeparator()

        action_description = menu.addAction("Показать описание")
        action_description.triggered.connect(self._show_description)

        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _get_selected_rows(self):
        """Возвращает список выбранных строк"""
        return list(set(item.row() for item in self.table.selectedItems()))

    def _copy_url(self):
        """Копирует URL в буфер обмена"""
        rows = self._get_selected_rows()
        if rows:
            url = self.video_urls.get(rows[0], "")
            if url:
                # Обновляем время копирования, чтобы мониторинг буфера не начал скачивание
                from clipboard_utils import update_last_copy_time
                update_last_copy_time()

                QGuiApplication.clipboard().setText(url)
                self._set_status("URL скопирован", "success")
                log_message(f"INFO URL скопирован: {url}")

    def _open_in_browser(self):
        """Открывает видео в браузере"""
        rows = self._get_selected_rows()
        if rows:
            url = self.video_urls.get(rows[0], "")
            if url:
                webbrowser.open(url)
                self._set_status("Открыто в браузере", "info")

    def _watch_video(self):
        """Открывает встроенный плеер для просмотра видео"""
        rows = self._get_selected_rows()
        if not rows:
            return

        row = rows[0]
        title = self.table.item(row, 0).text()
        url = self.video_urls.get(row, "")

        if not url:
            self._set_status("URL не найден", "warning")
            return

        # Создаём окно плеера
        from video_player import VideoPlayerWindow
        from styles import STYLESHEET_MINIMAL as STYLESHEET

        player_window = VideoPlayerWindow(title, url)
        player_window.setStyleSheet(STYLESHEET)
        player_window.show()
        player_window.raise_()
        player_window.activateWindow()

        # Сохраняем ссылку на окно, чтобы оно не было удалено сборщиком мусора
        self.player_windows.append(player_window)

        # Удаляем окно из списка при закрытии
        player_window.destroyed.connect(
            lambda: self.player_windows.remove(player_window) if player_window in self.player_windows else None
        )

        self._set_status("Открыт видеоплеер", "info")
        log_message(f"INFO Открыт видеоплеер для: {title}")

    def _show_description(self):
        """Показывает описание видео"""
        rows = self._get_selected_rows()
        if not rows:
            return

        row = rows[0]
        title = self.table.item(row, 0).text()
        description = self.video_descriptions.get(row, "Описание отсутствует")
        url = self.video_urls.get(row, "")

        # Отладочное логирование
        log_message(f"DEBUG Opening description window for row {row}")
        log_message(f"DEBUG Description length: {len(description)}")

        # Если описание короткое (обрезанное Search API), загружаем полное
        if len(description) < 200 and url:
            log_message("DEBUG Description is short, fetching full description...")
            full_description = self._fetch_full_description(url)
            if full_description and len(full_description) > len(description):
                description = full_description
                # Сохраняем полное описание
                self.video_descriptions[row] = description
                log_message(f"DEBUG Full description loaded, length: {len(description)}")

        # Создаём и показываем окно описания
        from description import DescriptionWindow
        from styles import STYLESHEET_MINIMAL as STYLESHEET

        desc_window = DescriptionWindow(title, description, url)
        desc_window.setStyleSheet(STYLESHEET)
        desc_window.show()
        desc_window.raise_()
        desc_window.activateWindow()

        # Сохраняем ссылку на окно, чтобы оно не было удалено сборщиком мусора
        self.description_windows.append(desc_window)

        # Удаляем окно из списка при закрытии
        desc_window.destroyed.connect(lambda: self.description_windows.remove(desc_window) if desc_window in self.description_windows else None)

        log_message(f"INFO Открыто окно описания для: {title}")

    def _fetch_full_description(self, url):
        """Загружает полное описание видео через YouTube API"""
        try:
            import requests
            from description import extract_video_id

            video_id = extract_video_id(url)
            if not video_id:
                log_message("DEBUG Could not extract video ID from URL")
                return None

            api_key = self.api_input.text().strip()
            if not api_key:
                log_message("DEBUG No API key available")
                return None

            videos_url = "https://www.googleapis.com/youtube/v3/videos"
            params = {
                'key': api_key,
                'part': 'snippet',
                'id': video_id
            }

            response = requests.get(videos_url, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data.get('items'):
                    description = data['items'][0]['snippet'].get('description', '')
                    log_message(f"DEBUG Fetched full description from API, length: {len(description)}")
                    return description
            else:
                log_message(f"DEBUG API request failed: {response.status_code}")
        except Exception as e:
            log_message(f"DEBUG Error fetching full description: {e}")

        return None

    def _on_double_click(self, index):
        """Двойной клик - добавить в очередь"""
        self._on_download()

    def _on_download(self):
        """Добавляет выбранные видео в очередь загрузки"""
        rows = self._get_selected_rows()
        if not rows:
            self._set_status("Выберите видео для загрузки", "warning")
            return

        added = 0
        for row in rows:
            url = self.video_urls.get(row, "")
            if url and add_to_queue(url):
                added += 1
                log_message(f"INFO Добавлено в очередь: {url}")

        if added > 0:
            self.total_downloads += added
            self._set_status(f"Добавлено в очередь: {added}", "success")
            self._update_download_bar()

            # Запускаем обработку очереди
            if not is_downloading:
                threading.Thread(target=process_queue, daemon=True).start()
        else:
            self._set_status("Видео уже в очереди", "warning")

    def _update_log(self):
        """Обновляет лог из файла"""
        try:
            from logger import LOG_FILE
            import os

            if os.path.exists(LOG_FILE):
                with open(LOG_FILE, "r", encoding="utf-8") as f:
                    # Читаем последние 50 строк
                    lines = f.readlines()[-50:]
                    current_text = self.log_text.toPlainText()
                    new_text = "".join(lines)

                    if new_text != current_text:
                        self.log_text.setPlainText(new_text)
                        # Прокручиваем вниз
                        self.log_text.verticalScrollBar().setValue(
                            self.log_text.verticalScrollBar().maximum()
                        )
        except Exception:
            pass

    def _clear_log(self):
        """Очищает файл лога"""
        try:
            from logger import LOG_FILE, log_message

            with open(LOG_FILE, "w", encoding="utf-8") as f:
                f.write("")

            log_message("INFO Лог очищен")
            self.log_text.clear()
        except Exception as e:
            from logger import log_message
            log_message(f"ERROR Ошибка при очистке лога: {e}")

    def closeEvent(self, event):
        """Закрытие окна - прячем, а не закрываем (приложение живёт в трее)"""
        # Сохраняем настройки
        settings["last_search_query"] = self.search_input.text()
        settings["search_type"] = self.type_combo.currentText()
        settings["sort_order"] = self.order_combo.currentText()
        settings["max_results"] = self.max_combo.currentText()
        settings["save_settings_on_exit"] = self.check_save.isChecked()

        if self.check_save.isChecked():
            save_settings(settings)

        # Прячем окно вместо закрытия - приложение продолжает работать в трее
        event.ignore()
        self.hide()
        log_message("INFO Окно поиска скрыто (приложение работает в трее)")
