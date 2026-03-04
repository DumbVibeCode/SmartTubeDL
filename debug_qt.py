"""
Окно отладки с просмотром логов и подсветкой синтаксиса (PyQt6)
"""

import os
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTextEdit, QApplication, QCheckBox
)
from PyQt6.QtGui import QTextCharFormat, QColor, QFont, QTextCursor
from PyQt6.QtCore import Qt, QTimer
from logger import LOG_FILE, log_message


class DebugWindow(QWidget):
    """Окно отладки с подсветкой синтаксиса"""

    def __init__(self):
        super().__init__()

        self.setWindowTitle("Режим отладки - Логи")
        self.setMinimumSize(800, 400)
        self.resize(900, 500)

        # Центрируем окно
        screen = QApplication.primaryScreen().geometry()
        x = (screen.width() - self.width()) // 2
        y = (screen.height() - self.height()) // 2
        self.move(x, y)

        # Текущий фильтр
        self.current_filter = None

        # Отслеживание изменений файла
        self._last_file_size = -1
        self._pending_update = False
        self._word_wrap = False

        # Создаём интерфейс
        self._create_ui()

        # Загружаем логи
        self._force_load()

        # Автообновление — проверяем каждые 2 секунды, но обновляем только при изменениях
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self._check_for_updates)
        self.update_timer.start(2000)

    def _create_ui(self):
        """Создаёт интерфейс окна"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # Текстовое поле с логами
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 9))
        self.log_text.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self.log_text, 1)

        # Панель кнопок
        btn_layout = QHBoxLayout()

        # Кнопки фильтрации
        self.btn_info = QPushButton("INFO")
        self.btn_info.setCheckable(True)
        self.btn_info.clicked.connect(lambda: self._filter_logs("INFO"))
        btn_layout.addWidget(self.btn_info)

        self.btn_success = QPushButton("SUCCESS")
        self.btn_success.setCheckable(True)
        self.btn_success.clicked.connect(lambda: self._filter_logs("SUCCESS"))
        btn_layout.addWidget(self.btn_success)

        self.btn_warning = QPushButton("WARNING")
        self.btn_warning.setCheckable(True)
        self.btn_warning.clicked.connect(lambda: self._filter_logs("WARNING"))
        btn_layout.addWidget(self.btn_warning)

        self.btn_error = QPushButton("ERROR")
        self.btn_error.setCheckable(True)
        self.btn_error.clicked.connect(lambda: self._filter_logs("ERROR"))
        btn_layout.addWidget(self.btn_error)

        self.btn_debug = QPushButton("DEBUG")
        self.btn_debug.setCheckable(True)
        self.btn_debug.clicked.connect(lambda: self._filter_logs("DEBUG"))
        btn_layout.addWidget(self.btn_debug)

        self.btn_reset = QPushButton("Все")
        self.btn_reset.clicked.connect(self._reset_filter)
        btn_layout.addWidget(self.btn_reset)

        btn_layout.addStretch()

        # Галочка переноса строк
        self.wrap_checkbox = QCheckBox("Перенос строк")
        self.wrap_checkbox.setChecked(False)
        self.wrap_checkbox.toggled.connect(self._toggle_word_wrap)
        btn_layout.addWidget(self.wrap_checkbox)

        # Кнопка очистки лога
        self.btn_clear = QPushButton("Очистить лог")
        self.btn_clear.setProperty("secondary", True)
        self.btn_clear.clicked.connect(self._clear_log)
        btn_layout.addWidget(self.btn_clear)

        # Кнопка сворачивания
        self.btn_minimize = QPushButton("Свернуть")
        self.btn_minimize.setProperty("secondary", True)
        self.btn_minimize.clicked.connect(self.showMinimized)
        btn_layout.addWidget(self.btn_minimize)

        layout.addLayout(btn_layout)

        # Отслеживаем прокрутку — при возврате вниз подгружаем накопившиеся обновления
        self.log_text.verticalScrollBar().valueChanged.connect(self._on_scroll)

    def _on_scroll(self, value):
        """При прокрутке к низу — подгружаем отложенные обновления"""
        scrollbar = self.log_text.verticalScrollBar()
        if value >= scrollbar.maximum() - 20 and self._pending_update:
            self._pending_update = False
            self._force_load()

    def _toggle_word_wrap(self, checked):
        """Переключает перенос строк"""
        self._word_wrap = checked
        if checked:
            self.log_text.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        else:
            self.log_text.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self._force_load()

    def _filter_logs(self, level: str):
        """Применяет фильтр по уровню"""
        # Сбрасываем состояние остальных кнопок
        for btn in [self.btn_info, self.btn_success, self.btn_warning, self.btn_error, self.btn_debug]:
            btn.setChecked(False)

        # Переключаем фильтр
        sender = self.sender()
        if self.current_filter == level:
            self.current_filter = None
            sender.setChecked(False)
        else:
            self.current_filter = level
            sender.setChecked(True)

        self._force_load()

    def _reset_filter(self):
        """Сбрасывает все фильтры"""
        self.current_filter = None
        for btn in [self.btn_info, self.btn_success, self.btn_warning, self.btn_error, self.btn_debug]:
            btn.setChecked(False)
        self._force_load()

    def _clear_log(self):
        """Очищает файл лога"""
        try:
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                f.write("")
            self._last_file_size = -1
            log_message("INFO Лог очищен")
            self._force_load()
        except Exception as e:
            log_message(f"ERROR Ошибка при очистке лога: {e}")

    def _check_for_updates(self):
        """Проверяет, изменился ли файл лога. Обновляет только при наличии новых записей."""
        if not os.path.exists(LOG_FILE):
            return
        try:
            file_size = os.path.getsize(LOG_FILE)
            if file_size == self._last_file_size:
                return  # Файл не изменился — ничего не делаем

            # Файл изменился — проверяем, можно ли обновить
            scrollbar = self.log_text.verticalScrollBar()
            at_bottom = scrollbar.value() >= scrollbar.maximum() - 20
            has_selection = self.log_text.textCursor().hasSelection()

            if not at_bottom or has_selection:
                # Пользователь читает или выделяет текст — откладываем
                self._pending_update = True
                return

            self._last_file_size = file_size
            self._pending_update = False
            self._load_and_display()
        except Exception:
            pass

    def _force_load(self):
        """Принудительная перезагрузка логов (фильтр, очистка, и т.д.)"""
        try:
            if os.path.exists(LOG_FILE):
                self._last_file_size = os.path.getsize(LOG_FILE)
        except Exception:
            pass
        self._pending_update = False
        self._load_and_display()

    def _load_and_display(self):
        """Загружает и отображает логи с подсветкой"""
        if not os.path.exists(LOG_FILE):
            return

        try:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()

            # Берём только последние 1000 строк для производительности
            lines = lines[-1000:]

            # Фильтруем, если нужно
            if self.current_filter:
                lines = [line for line in lines if self.current_filter in line]

            # Создаём HTML с подсветкой
            html_lines = [self._line_to_html(line) for line in lines]

            wrap_style = "white-space: pre-wrap; word-wrap: break-word;" if self._word_wrap else ""
            html = f'<pre style="font-family: Consolas, monospace; font-size: 9pt; margin: 0; {wrap_style}">' + ''.join(html_lines) + '</pre>'
            self.log_text.setHtml(html)

            # Прокручиваем вниз
            scrollbar = self.log_text.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

        except Exception:
            pass

    def _line_to_html(self, line: str) -> str:
        """Преобразует строку лога в HTML с цветовой подсветкой"""
        import html
        line_escaped = html.escape(line.rstrip('\n'))
        line_lower = line.lower()

        if "error" in line_lower or "ошибка" in line_lower:
            return f'<span style="color: #d32f2f; background-color: #ffebee;">{line_escaped}</span>\n'
        elif "success" in line_lower or "успешно" in line_lower or "готов" in line_lower:
            return f'<span style="color: #388e3c; background-color: #e8f5e9;">{line_escaped}</span>\n'
        elif "warning" in line_lower or "предупреждение" in line_lower:
            return f'<span style="color: #f57c00; background-color: #fff3e0;">{line_escaped}</span>\n'
        elif "debug" in line_lower:
            return f'<span style="color: #1976d2; background-color: #e3f2fd;">{line_escaped}</span>\n'
        else:  # INFO или другое
            return f'<span style="color: #424242; background-color: #fafafa;">{line_escaped}</span>\n'

    def closeEvent(self, event):
        """Обработка закрытия окна - прячем, а не закрываем"""
        event.ignore()
        self.hide()
        log_message("INFO Окно отладки скрыто")
