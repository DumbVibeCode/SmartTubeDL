"""
Стили для приложения (Qt Style Sheets)
Современный минималистичный дизайн
"""

# Цветовая палитра (классический Windows)
COLORS = {
    # Основные цвета
    "bg_primary": "#f0f0f0",      # Классический серый фон Windows
    "bg_secondary": "#ffffff",    # Белый для панелей
    "bg_input": "#ffffff",        # Белый фон полей ввода

    # Текст
    "text_primary": "#000000",    # Чёрный текст
    "text_secondary": "#696969",  # Серый текст
    "text_placeholder": "#a9a9a9", # Плейсхолдер

    # Акценты
    "accent": "#0078d7",          # Синий Windows 10
    "accent_hover": "#005a9e",    # При наведении
    "accent_pressed": "#004275",  # При нажатии

    # Статусы
    "success": "#10893e",         # Зелёный
    "warning": "#ff8c00",         # Оранжевый
    "error": "#e81123",           # Красный
    "info": "#0078d7",            # Синий

    # Границы
    "border": "#ababab",
    "border_focus": "#0078d7",
}

# Классический Windows стиль (компактный)
STYLESHEET = f"""
/* === ГЛОБАЛЬНЫЕ СТИЛИ === */
QWidget {{
    background-color: {COLORS["bg_primary"]};
    color: {COLORS["text_primary"]};
    font-family: "Segoe UI", "Tahoma", "Arial", sans-serif;
    font-size: 11px;
}}

/* === ГЛАВНОЕ ОКНО === */
QMainWindow {{
    background-color: {COLORS["bg_primary"]};
}}

/* === ГРУППЫ (GroupBox) === */
QGroupBox {{
    background-color: {COLORS["bg_secondary"]};
    border: 1px solid {COLORS["border"]};
    margin-top: 8px;
    padding: 6px;
    padding-top: 16px;
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 8px;
    padding: 0 4px;
    color: {COLORS["text_primary"]};
}}

/* === ПОЛЯ ВВОДА === */
QLineEdit, QTextEdit, QPlainTextEdit {{
    background-color: {COLORS["bg_input"]};
    border: 1px solid {COLORS["border"]};
    padding: 3px 5px;
    color: {COLORS["text_primary"]};
    selection-background-color: {COLORS["accent"]};
    selection-color: white;
}}

QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border: 1px solid {COLORS["border_focus"]};
}}

QLineEdit::placeholder {{
    color: {COLORS["text_placeholder"]};
}}

/* === КНОПКИ === */
QPushButton {{
    background-color: #e1e1e1;
    color: {COLORS["text_primary"]};
    border: 1px solid {COLORS["border"]};
    padding: 4px 12px;
    min-height: 20px;
}}

QPushButton:hover {{
    background-color: #e5f1fb;
    border: 1px solid #0078d7;
}}

QPushButton:pressed {{
    background-color: #cce4f7;
    border: 1px solid #005499;
}}

QPushButton:disabled {{
    background-color: {COLORS["bg_primary"]};
    color: {COLORS["text_placeholder"]};
    border: 1px solid #d0d0d0;
}}

/* Вторичная кнопка (серая) */
QPushButton[secondary="true"] {{
    background-color: {COLORS["bg_input"]};
    color: {COLORS["text_primary"]};
    border: 1px solid {COLORS["border"]};
}}

QPushButton[secondary="true"]:hover {{
    background-color: #e5f1fb;
}}

/* === КОМБОБОКС === */
QComboBox {{
    background-color: {COLORS["bg_input"]};
    border: 1px solid {COLORS["border"]};
    padding: 3px 5px;
    min-width: 100px;
}}

QComboBox:focus {{
    border: 1px solid {COLORS["border_focus"]};
}}

QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 18px;
    border-left: 1px solid {COLORS["border"]};
}}

QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {COLORS["text_primary"]};
    margin-right: 5px;
}}

QComboBox QAbstractItemView {{
    background-color: {COLORS["bg_secondary"]};
    border: 1px solid {COLORS["border"]};
    selection-background-color: {COLORS["accent"]};
    selection-color: white;
    outline: none;
}}

/* === ЧЕКБОКСЫ === */
QCheckBox {{
    spacing: 6px;
}}

QCheckBox::indicator {{
    width: 13px;
    height: 13px;
    border: 1px solid {COLORS["border"]};
    background-color: {COLORS["bg_input"]};
}}

QCheckBox::indicator:checked {{
    background-color: {COLORS["accent"]};
    border: 1px solid {COLORS["accent"]};
}}

QCheckBox::indicator:hover {{
    border: 1px solid {COLORS["border_focus"]};
}}

/* === ТАБЛИЦА === */
QTableWidget, QTableView, QTreeView {{
    background-color: {COLORS["bg_secondary"]};
    border: 1px solid {COLORS["border"]};
    gridline-color: {COLORS["border"]};
    selection-background-color: {COLORS["accent"]};
    selection-color: white;
    alternate-background-color: #f9f9f9;
    outline: none;
}}

QTableWidget::item, QTableView::item, QTreeView::item {{
    padding: 2px 4px;
    border: none;
    outline: none;
}}

QTableWidget::item:selected, QTableView::item:selected, QTreeView::item:selected {{
    background-color: {COLORS["accent"]};
    color: white;
    outline: none;
}}

QTableWidget::item:focus, QTableView::item:focus, QTreeView::item:focus {{
    outline: none;
    border: none;
}}

QHeaderView::section {{
    background-color: {COLORS["bg_primary"]};
    color: {COLORS["text_primary"]};
    padding: 3px 4px;
    border: none;
    border-bottom: 1px solid {COLORS["border"]};
    border-right: 1px solid {COLORS["border"]};
}}

QHeaderView::section:hover {{
    background-color: #e5e5e5;
}}

/* === ПРОГРЕСС БАР === */
QProgressBar {{
    background-color: white;
    border: 1px solid {COLORS["border"]};
    height: 18px;
    text-align: center;
}}

QProgressBar::chunk {{
    background-color: {COLORS["accent"]};
}}

/* === СКРОЛЛБАР === */
QScrollBar:vertical {{
    background-color: {COLORS["bg_primary"]};
    width: 17px;
    margin: 0;
}}

QScrollBar::handle:vertical {{
    background-color: #cdcdcd;
    min-height: 20px;
    margin: 0;
}}

QScrollBar::handle:vertical:hover {{
    background-color: #a6a6a6;
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}

QScrollBar:horizontal {{
    background-color: {COLORS["bg_primary"]};
    height: 17px;
    margin: 0;
}}

QScrollBar::handle:horizontal {{
    background-color: #cdcdcd;
    min-width: 20px;
    margin: 0;
}}

QScrollBar::handle:horizontal:hover {{
    background-color: #a6a6a6;
}}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}

/* === МЕТКИ === */
QLabel {{
    color: {COLORS["text_primary"]};
    background: transparent;
}}

QLabel[secondary="true"] {{
    color: {COLORS["text_secondary"]};
}}

/* === МЕНЮ === */
QMenu {{
    background-color: {COLORS["bg_secondary"]};
    border: 1px solid {COLORS["border"]};
    padding: 2px;
}}

QMenu::item {{
    padding: 4px 25px 4px 8px;
}}

QMenu::item:selected {{
    background-color: {COLORS["accent"]};
    color: white;
}}

QMenu::separator {{
    height: 1px;
    background-color: {COLORS["border"]};
    margin: 2px 0;
}}

/* === ТУЛТИПЫ === */
QToolTip {{
    background-color: #ffffe1;
    color: black;
    border: 1px solid black;
    padding: 2px;
}}

/* === СТАТУС-БАР === */
QStatusBar {{
    background-color: {COLORS["bg_primary"]};
    color: {COLORS["text_secondary"]};
    border-top: 1px solid {COLORS["border"]};
}}
"""

# Минималистичная тема (Apple-стиль)
COLORS_MINIMAL = {
    # Основные цвета
    "bg_primary": "#f5f5f7",       # Очень светло-серый Apple
    "bg_secondary": "#ffffff",     # Чисто белый для карточек
    "bg_input": "#ffffff",         # Белый фон полей

    # Текст
    "text_primary": "#1d1d1f",     # Почти чёрный
    "text_secondary": "#86868b",   # Серый Apple
    "text_placeholder": "#aeaeb2", # Светлый плейсхолдер

    # Акценты
    "accent": "#007aff",           # Синий Apple
    "accent_hover": "#0066d6",     # Темнее при наведении
    "accent_pressed": "#004999",   # Ещё темнее при нажатии
    "accent_light": "#e8f4ff",     # Светлый акцент для hover

    # Статусы
    "success": "#34c759",          # Зелёный Apple
    "warning": "#ff9500",          # Оранжевый Apple
    "error": "#ff3b30",            # Красный Apple
    "info": "#007aff",             # Синий

    # Границы и тени
    "border": "rgba(0, 0, 0, 0.08)",  # Почти невидимая граница
    "border_focus": "#007aff",
    "shadow": "rgba(0, 0, 0, 0.04)",
}

STYLESHEET_MINIMAL = f"""
/* === ГЛОБАЛЬНЫЕ СТИЛИ === */
QWidget {{
    background-color: {COLORS_MINIMAL["bg_primary"]};
    color: {COLORS_MINIMAL["text_primary"]};
    font-family: "SF Pro Display", "SF Pro", "Helvetica Neue", "Segoe UI", sans-serif;
    font-size: 13px;
}}

/* === ГЛАВНОЕ ОКНО === */
QMainWindow {{
    background-color: {COLORS_MINIMAL["bg_primary"]};
}}

/* === ГРУППЫ (GroupBox) - карточки === */
QGroupBox {{
    background-color: {COLORS_MINIMAL["bg_secondary"]};
    border: none;
    border-radius: 12px;
    margin-top: 12px;
    padding: 16px;
    padding-top: 24px;
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 16px;
    padding: 0 8px;
    color: {COLORS_MINIMAL["text_secondary"]};
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}}

/* === ПОЛЯ ВВОДА === */
QLineEdit, QTextEdit, QPlainTextEdit {{
    background-color: {COLORS_MINIMAL["bg_secondary"]};
    border: 1px solid {COLORS_MINIMAL["border"]};
    border-radius: 8px;
    padding: 8px 12px;
    color: {COLORS_MINIMAL["text_primary"]};
    selection-background-color: {COLORS_MINIMAL["accent"]};
    selection-color: white;
}}

QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border: 2px solid {COLORS_MINIMAL["accent"]};
    padding: 7px 11px;
}}

QLineEdit:hover, QTextEdit:hover {{
    border: 1px solid rgba(0, 0, 0, 0.15);
}}

QLineEdit::placeholder {{
    color: {COLORS_MINIMAL["text_placeholder"]};
}}

/* === КНОПКИ === */
QPushButton {{
    background-color: {COLORS_MINIMAL["accent"]};
    color: white;
    border: none;
    border-radius: 8px;
    padding: 8px 20px;
    min-height: 24px;
    font-weight: 500;
}}

QPushButton:hover {{
    background-color: {COLORS_MINIMAL["accent_hover"]};
}}

QPushButton:pressed {{
    background-color: {COLORS_MINIMAL["accent_pressed"]};
}}

QPushButton:disabled {{
    background-color: #e5e5e5;
    color: #a0a0a0;
}}

/* Вторичная кнопка */
QPushButton[secondary="true"] {{
    background-color: rgba(0, 122, 255, 0.1);
    color: {COLORS_MINIMAL["accent"]};
    border: none;
}}

QPushButton[secondary="true"]:hover {{
    background-color: rgba(0, 122, 255, 0.15);
}}

QPushButton[secondary="true"]:pressed {{
    background-color: rgba(0, 122, 255, 0.2);
}}

/* === КОМБОБОКС === */
QComboBox {{
    background-color: {COLORS_MINIMAL["bg_secondary"]};
    border: 1px solid {COLORS_MINIMAL["border"]};
    border-radius: 8px;
    padding: 8px 12px;
    min-width: 120px;
}}

QComboBox:hover {{
    border: 1px solid rgba(0, 0, 0, 0.15);
}}

QComboBox:focus {{
    border: 2px solid {COLORS_MINIMAL["accent"]};
    padding: 7px 11px;
}}

QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 24px;
    border: none;
}}

QComboBox::down-arrow {{
    image: none;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 6px solid {COLORS_MINIMAL["text_secondary"]};
    margin-right: 8px;
}}

QComboBox QAbstractItemView {{
    background-color: {COLORS_MINIMAL["bg_secondary"]};
    border: 1px solid {COLORS_MINIMAL["border"]};
    border-radius: 8px;
    padding: 4px;
    selection-background-color: {COLORS_MINIMAL["accent_light"]};
    selection-color: {COLORS_MINIMAL["accent"]};
    outline: none;
}}

QComboBox QAbstractItemView::item {{
    padding: 8px 12px;
    border-radius: 6px;
}}

/* === ЧЕКБОКСЫ === */
QCheckBox {{
    spacing: 8px;
}}

QCheckBox::indicator {{
    width: 18px;
    height: 18px;
    border: 2px solid {COLORS_MINIMAL["border"]};
    border-radius: 4px;
    background-color: {COLORS_MINIMAL["bg_secondary"]};
}}

QCheckBox::indicator:hover {{
    border-color: {COLORS_MINIMAL["text_secondary"]};
}}

QCheckBox::indicator:checked {{
    background-color: {COLORS_MINIMAL["accent"]};
    border: 2px solid {COLORS_MINIMAL["accent"]};
}}

/* === ТАБЛИЦА === */
QTableWidget, QTableView, QTreeView {{
    background-color: {COLORS_MINIMAL["bg_secondary"]};
    border: none;
    border-radius: 12px;
    gridline-color: transparent;
    selection-background-color: {COLORS_MINIMAL["accent_light"]};
    selection-color: {COLORS_MINIMAL["text_primary"]};
    outline: none;
}}

QTableWidget::item, QTableView::item, QTreeView::item {{
    padding: 12px 16px;
    border: none;
    border-bottom: 1px solid {COLORS_MINIMAL["border"]};
    outline: none;
}}

QTableWidget::item:selected, QTableView::item:selected {{
    background-color: {COLORS_MINIMAL["accent_light"]};
    color: {COLORS_MINIMAL["text_primary"]};
}}

QTableWidget::item:hover {{
    background-color: rgba(0, 0, 0, 0.02);
}}

QHeaderView::section {{
    background-color: transparent;
    color: {COLORS_MINIMAL["text_secondary"]};
    padding: 12px 16px;
    border: none;
    border-bottom: 1px solid {COLORS_MINIMAL["border"]};
    font-weight: 600;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}}

/* === ПРОГРЕСС БАР === */
QProgressBar {{
    background-color: #e5e5e5;
    border: none;
    border-radius: 4px;
    height: 8px;
    text-align: center;
}}

QProgressBar::chunk {{
    background-color: {COLORS_MINIMAL["accent"]};
    border-radius: 4px;
}}

/* === СКРОЛЛБАР === */
QScrollBar:vertical {{
    background-color: transparent;
    width: 8px;
    margin: 4px 2px;
}}

QScrollBar::handle:vertical {{
    background-color: rgba(0, 0, 0, 0.2);
    min-height: 30px;
    border-radius: 4px;
}}

QScrollBar::handle:vertical:hover {{
    background-color: rgba(0, 0, 0, 0.35);
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}

QScrollBar:horizontal {{
    background-color: transparent;
    height: 8px;
    margin: 2px 4px;
}}

QScrollBar::handle:horizontal {{
    background-color: rgba(0, 0, 0, 0.2);
    min-width: 30px;
    border-radius: 4px;
}}

QScrollBar::handle:horizontal:hover {{
    background-color: rgba(0, 0, 0, 0.35);
}}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}

/* === МЕТКИ === */
QLabel {{
    color: {COLORS_MINIMAL["text_primary"]};
    background: transparent;
}}

QLabel[secondary="true"] {{
    color: {COLORS_MINIMAL["text_secondary"]};
    font-size: 12px;
}}

/* === МЕНЮ === */
QMenu {{
    background-color: {COLORS_MINIMAL["bg_secondary"]};
    border: 1px solid {COLORS_MINIMAL["border"]};
    border-radius: 12px;
    padding: 8px;
}}

QMenu::item {{
    padding: 8px 16px;
    border-radius: 6px;
}}

QMenu::item:selected {{
    background-color: {COLORS_MINIMAL["accent_light"]};
    color: {COLORS_MINIMAL["accent"]};
}}

QMenu::separator {{
    height: 1px;
    background-color: {COLORS_MINIMAL["border"]};
    margin: 8px 0;
}}

/* === ТУЛТИПЫ === */
QToolTip {{
    background-color: {COLORS_MINIMAL["text_primary"]};
    color: white;
    border: none;
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 12px;
}}

/* === РАЗДЕЛИТЕЛЬ === */
QSplitter::handle {{
    background-color: transparent;
}}

QSplitter::handle:horizontal {{
    width: 8px;
}}

QSplitter::handle:vertical {{
    height: 8px;
}}

/* === ОКНО ПОИСКА — специфичные стили === */

/* Строка поиска */
#searchBar {{
    background-color: {COLORS_MINIMAL["bg_secondary"]};
    border-bottom: 1px solid {COLORS_MINIMAL["border"]};
}}

#searchInput {{
    font-size: 15px;
    padding: 10px 16px;
    border-radius: 10px;
    background-color: {COLORS_MINIMAL["bg_primary"]};
    border: none;
}}

#searchInput:focus {{
    background-color: {COLORS_MINIMAL["bg_secondary"]};
    border: 2px solid {COLORS_MINIMAL["accent"]};
    padding: 8px 14px;
}}

#searchBtn {{
    background-color: {COLORS_MINIMAL["accent"]};
    color: white;
    font-size: 13px;
    font-weight: 600;
    padding: 10px 24px;
    border-radius: 10px;
}}

#searchBtn:hover {{
    background-color: {COLORS_MINIMAL["accent_hover"]};
}}

/* Панель настроек */
#settingsPanel {{
    background-color: {COLORS_MINIMAL["bg_secondary"]};
    border-bottom: 1px solid {COLORS_MINIMAL["border"]};
}}

/* Тонкий прогресс-бар */
#thinProgress {{
    background-color: {COLORS_MINIMAL["bg_primary"]};
    border: none;
    border-radius: 0;
    max-height: 3px;
}}

#thinProgress::chunk {{
    background-color: {COLORS_MINIMAL["accent"]};
    border-radius: 0;
}}

/* Таблица результатов — чистая, без рамок */
#resultsTable {{
    background-color: {COLORS_MINIMAL["bg_secondary"]};
    border: none;
    border-radius: 0;
    outline: none;
}}

/* Нижняя панель */
#bottomBar {{
    background-color: {COLORS_MINIMAL["bg_secondary"]};
    border-top: 1px solid {COLORS_MINIMAL["border"]};
}}

/* Лог */
#logPanel {{
    background-color: {COLORS_MINIMAL["bg_primary"]};
    border: none;
    border-radius: 6px;
    font-family: "Consolas", "SF Mono", monospace;
    font-size: 11px;
    color: {COLORS_MINIMAL["text_secondary"]};
    padding: 8px;
}}

/* Прогресс загрузки */
#downloadProgress {{
    background-color: #e5e5e5;
    border: none;
    border-radius: 2px;
    max-height: 4px;
}}

#downloadProgress::chunk {{
    background-color: {COLORS_MINIMAL["success"]};
    border-radius: 2px;
}}
"""
