"""
Окно для просмотра YouTube видео
"""

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QMessageBox
from PyQt6.QtCore import QUrl
from logger import log_message
import webbrowser


class VideoPlayerWindow(QWidget):
    """Окно для просмотра YouTube видео"""

    def __init__(self, title: str, url: str):
        super().__init__()

        self.setWindowTitle(f"Просмотр: {title[:50]}...")
        self.setMinimumSize(800, 600)
        self.resize(1024, 768)

        # Пытаемся использовать встроенный веб-движок
        try:
            from PyQt6.QtWebEngineWidgets import QWebEngineView
            log_message("INFO PyQt6-WebEngine успешно импортирован")
            self._setup_webengine_player(url)
        except ImportError as e:
            # Если PyQt6-WebEngine не установлен, открываем в браузере
            log_message(f"WARNING: PyQt6-WebEngine не установлен: {e}")
            log_message("WARNING: Открываем видео в браузере")
            QMessageBox.information(
                self,
                "Встроенный плеер недоступен",
                f"Видео будет открыто в браузере.\n\nОшибка импорта: {e}\n\nДля встроенного просмотра установите:\npip install PyQt6-WebEngine"
            )
            webbrowser.open(url)
            self.close()
            return
        except Exception as e:
            log_message(f"ERROR: Неожиданная ошибка при создании плеера: {e}")
            QMessageBox.critical(
                self,
                "Ошибка",
                f"Не удалось создать плеер: {e}"
            )
            webbrowser.open(url)
            self.close()
            return

    def _setup_webengine_player(self, url: str):
        """Создаёт встроенный веб-плеер"""
        from PyQt6.QtWebEngineWidgets import QWebEngineView

        log_message("INFO Начало создания веб-плеера")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Создаём веб-view
        try:
            self.web_view = QWebEngineView()
            log_message("INFO QWebEngineView создан успешно")
        except Exception as e:
            log_message(f"ERROR Ошибка создания QWebEngineView: {e}")
            raise

        # Используем обычную ссылку YouTube (не embed)
        # Это позволяет обойти ограничения на воспроизведение в iframe
        log_message(f"INFO Загрузка URL в плеер: {url}")
        self.web_view.setUrl(QUrl(url))
        log_message(f"INFO Встроенный плеер открыт для: {url}")

        layout.addWidget(self.web_view)
        log_message("INFO Веб-плеер добавлен в layout")

    def _extract_video_id(self, url: str) -> str:
        """Извлекает ID видео из YouTube URL"""
        import re
        patterns = [
            r'(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]{11})',
            r'youtube\.com/embed/([a-zA-Z0-9_-]{11})',
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return ""
