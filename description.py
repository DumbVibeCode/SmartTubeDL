"""
Окно просмотра описания видео
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTextEdit, QPushButton, QApplication
)
from PyQt6.QtGui import QFont


def show_description(*args, **kwargs):
    """Заглушка для совместимости со старым tkinter кодом"""
    from logger import log_message
    log_message("WARNING: show_description вызвана из старого кода (не реализована в PyQt6)")
    pass


def extract_video_id(url):
    """Заглушка для совместимости со старым кодом"""
    import re
    patterns = [
        r'(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]{11})',
        r'youtube\.com/embed/([a-zA-Z0-9_-]{11})',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


class DescriptionWindow(QWidget):
    """Окно для просмотра описания видео"""

    def __init__(self, title: str, description: str, url: str = ""):
        super().__init__()

        self.setWindowTitle("Описание видео")
        self.setMinimumSize(700, 500)
        self.resize(800, 600)

        # Центрируем окно
        screen = QApplication.primaryScreen().geometry()
        x = (screen.width() - self.width()) // 2
        y = (screen.height() - self.height()) // 2
        self.move(x, y)

        # Создаём интерфейс
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)

        # Заголовок
        title_label = QLabel(title)
        title_font = QFont()
        title_font.setPointSize(11)
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_label.setWordWrap(True)
        layout.addWidget(title_label)

        # Текст описания
        desc_text = QTextEdit()
        desc_text.setReadOnly(True)
        desc_text.setPlainText(description)
        layout.addWidget(desc_text, 1)

        # Кнопки
        btn_layout = QHBoxLayout()

        if url:
            copy_btn = QPushButton("Копировать URL")
            copy_btn.clicked.connect(lambda: self._copy_url(url))
            btn_layout.addWidget(copy_btn)

        btn_layout.addStretch()

        close_btn = QPushButton("Закрыть")
        close_btn.setProperty("secondary", True)
        close_btn.clicked.connect(self.close)
        btn_layout.addWidget(close_btn)

        layout.addLayout(btn_layout)

    def _copy_url(self, url: str):
        """Копирует URL в буфер обмена"""
        from clipboard_utils import update_last_copy_time
        update_last_copy_time()

        QApplication.clipboard().setText(url)
        from logger import log_message
        log_message(f"INFO URL скопирован: {url}")
