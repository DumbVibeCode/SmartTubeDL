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

# ── Компактная светлая тема ─────────────────────────────────────────────────
_COLORS_LIGHT = {
    "bg_primary":       "#f0f2f5",
    "bg_secondary":     "#ffffff",
    "bg_input":         "#ffffff",
    "text_primary":     "#1a1a2e",
    "text_secondary":   "#6b7280",
    "text_placeholder": "#9ca3af",
    "accent":           "#2563eb",
    "accent_hover":     "#1d4ed8",
    "accent_pressed":   "#1e40af",
    "accent_light":     "#dbeafe",
    "success":          "#16a34a",
    "warning":          "#d97706",
    "error":            "#dc2626",
    "info":             "#2563eb",
    "border":           "rgba(0,0,0,0.10)",
    "border_focus":     "#2563eb",
}

# ── Компактная тёмная тема ───────────────────────────────────────────────────
_COLORS_DARK = {
    "bg_primary":       "#1e1e1e",
    "bg_secondary":     "#252526",
    "bg_input":         "#2d2d30",
    "text_primary":     "#cccccc",
    "text_secondary":   "#858585",
    "text_placeholder": "#555555",
    "accent":           "#0ea5e9",
    "accent_hover":     "#38bdf8",
    "accent_pressed":   "#7dd3fc",
    "accent_light":     "rgba(14,165,233,0.15)",
    "success":          "#4ec9b0",
    "warning":          "#ce9178",
    "error":            "#f48771",
    "info":             "#9cdcfe",
    "border":           "rgba(255,255,255,0.08)",
    "border_focus":     "#0ea5e9",
}


def _build_stylesheet(c: dict) -> str:
    """Генерирует QSS из цветового словаря (компактный стиль)."""
    return f"""
/* === ГЛОБАЛЬНЫЕ СТИЛИ === */
QWidget {{
    background-color: {c["bg_primary"]};
    color: {c["text_primary"]};
    font-family: "Segoe UI", "Helvetica Neue", sans-serif;
    font-size: 12px;
}}
QMainWindow {{
    background-color: {c["bg_primary"]};
}}

/* === ГРУППЫ === */
QGroupBox {{
    background-color: {c["bg_secondary"]};
    border: 1px solid {c["border"]};
    border-radius: 4px;
    margin-top: 8px;
    padding: 8px;
    padding-top: 16px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 8px;
    padding: 0 4px;
    color: {c["text_secondary"]};
    font-size: 11px;
    font-weight: 600;
}}

/* === ПОЛЯ ВВОДА === */
QLineEdit, QTextEdit, QPlainTextEdit {{
    background-color: {c["bg_input"]};
    border: 1px solid {c["border"]};
    border-radius: 4px;
    padding: 4px 8px;
    color: {c["text_primary"]};
    selection-background-color: {c["accent"]};
    selection-color: white;
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border: 1px solid {c["border_focus"]};
}}
QLineEdit:hover, QTextEdit:hover {{
    border: 1px solid {c["border_focus"]};
}}

/* === КНОПКИ === */
QPushButton {{
    background-color: {c["accent"]};
    color: white;
    border: none;
    border-radius: 4px;
    padding: 4px 12px;
    min-height: 18px;
    font-weight: 500;
}}
QPushButton:hover {{
    background-color: {c["accent_hover"]};
}}
QPushButton:pressed {{
    background-color: {c["accent_pressed"]};
}}
QPushButton:disabled {{
    background-color: {c["border"]};
    color: {c["text_placeholder"]};
}}
QPushButton[secondary="true"] {{
    background-color: {c["bg_input"]};
    color: {c["text_primary"]};
    border: 1px solid {c["border"]};
}}
QPushButton[secondary="true"]:hover {{
    border-color: {c["accent"]};
    color: {c["accent"]};
}}
QPushButton[secondary="true"]:pressed {{
    background-color: {c["accent_light"]};
}}

/* === КОМБОБОКС === */
QComboBox {{
    background-color: {c["bg_input"]};
    border: 1px solid {c["border"]};
    border-radius: 4px;
    padding: 4px 8px;
    min-width: 80px;
    color: {c["text_primary"]};
}}
QComboBox:hover {{
    border-color: {c["border_focus"]};
}}
QComboBox:focus {{
    border: 1px solid {c["border_focus"]};
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 18px;
    border: none;
}}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {c["text_secondary"]};
    margin-right: 5px;
}}
QComboBox QAbstractItemView {{
    background-color: {c["bg_secondary"]};
    border: 1px solid {c["border"]};
    selection-background-color: {c["accent_light"]};
    selection-color: {c["text_primary"]};
    outline: none;
}}
QComboBox QAbstractItemView::item {{
    padding: 4px 8px;
}}

/* === ЧЕКБОКСЫ === */
QCheckBox {{
    spacing: 6px;
    color: {c["text_primary"]};
}}
QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid {c["border"]};
    border-radius: 3px;
    background-color: {c["bg_input"]};
}}
QCheckBox::indicator:hover {{
    border-color: {c["accent"]};
}}
QCheckBox::indicator:checked {{
    background-color: {c["accent"]};
    border-color: {c["accent"]};
}}

/* === ТАБЛИЦА === */
QTableWidget, QTableView, QTreeView {{
    background-color: {c["bg_secondary"]};
    border: none;
    gridline-color: transparent;
    selection-background-color: {c["accent_light"]};
    selection-color: {c["text_primary"]};
    outline: none;
}}
QTableWidget::item, QTableView::item, QTreeView::item {{
    padding: 3px 8px;
    border: none;
    border-bottom: 1px solid {c["border"]};
    outline: none;
}}
QTableWidget::item:selected, QTableView::item:selected {{
    background-color: {c["accent_light"]};
    color: {c["text_primary"]};
}}
QTableWidget::item:hover {{
    background-color: {c["accent_light"]};
}}
QHeaderView::section {{
    background-color: {c["bg_primary"]};
    color: {c["text_secondary"]};
    padding: 4px 8px;
    border: none;
    border-bottom: 1px solid {c["border"]};
    font-weight: 600;
    font-size: 11px;
}}

/* === ПРОГРЕСС БАР === */
QProgressBar {{
    background-color: {c["border"]};
    border: none;
    border-radius: 3px;
    text-align: center;
}}
QProgressBar::chunk {{
    background-color: {c["accent"]};
    border-radius: 3px;
}}

/* === СКРОЛЛБАР === */
QScrollBar:vertical {{
    background-color: transparent;
    width: 7px;
    margin: 2px 1px;
}}
QScrollBar::handle:vertical {{
    background-color: {c["border"]};
    min-height: 20px;
    border-radius: 3px;
}}
QScrollBar::handle:vertical:hover {{
    background-color: {c["text_secondary"]};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar:horizontal {{
    background-color: transparent;
    height: 7px;
    margin: 1px 2px;
}}
QScrollBar::handle:horizontal {{
    background-color: {c["border"]};
    min-width: 20px;
    border-radius: 3px;
}}
QScrollBar::handle:horizontal:hover {{
    background-color: {c["text_secondary"]};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}

/* === МЕТКИ === */
QLabel {{
    color: {c["text_primary"]};
    background: transparent;
}}
QLabel[secondary="true"] {{
    color: {c["text_secondary"]};
    font-size: 11px;
}}

/* === МЕНЮ === */
QMenu {{
    background-color: {c["bg_secondary"]};
    border: 1px solid {c["border"]};
    border-radius: 4px;
    padding: 3px;
}}
QMenu::item {{
    padding: 4px 20px 4px 8px;
    border-radius: 3px;
}}
QMenu::item:selected {{
    background-color: {c["accent_light"]};
    color: {c["accent"]};
}}
QMenu::separator {{
    height: 1px;
    background-color: {c["border"]};
    margin: 3px 0;
}}

/* === ТУЛТИПЫ === */
QToolTip {{
    background-color: {c["bg_primary"]};
    color: {c["text_primary"]};
    border: 1px solid {c["border"]};
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 11px;
}}

/* === РАЗДЕЛИТЕЛЬ === */
QSplitter::handle {{
    background-color: transparent;
}}
QSplitter::handle:horizontal {{ width: 6px; }}
QSplitter::handle:vertical   {{ height: 6px; }}

/* === ОКНО ПОИСКА === */
#searchBar {{
    background-color: {c["bg_secondary"]};
    border-bottom: 1px solid {c["border"]};
}}
#searchInput {{
    font-size: 12px;
    padding: 5px 10px;
    border-radius: 4px;
    background-color: {c["bg_input"]};
    border: 1px solid {c["border"]};
    color: {c["text_primary"]};
}}
#searchInput:focus {{
    border: 1px solid {c["border_focus"]};
}}
#searchBtn {{
    background-color: {c["accent"]};
    color: white;
    font-size: 12px;
    font-weight: 600;
    padding: 5px 16px;
    border-radius: 4px;
}}
#searchBtn:hover {{
    background-color: {c["accent_hover"]};
}}
#settingsPanel {{
    background-color: {c["bg_secondary"]};
    border-bottom: 1px solid {c["border"]};
}}
#thinProgress {{
    background-color: {c["bg_primary"]};
    border: none;
    border-radius: 0;
    max-height: 3px;
}}
#thinProgress::chunk {{
    background-color: {c["accent"]};
    border-radius: 0;
}}
#resultsTable {{
    background-color: {c["bg_secondary"]};
    border: none;
    border-radius: 0;
    outline: none;
}}
#bottomBar {{
    background-color: {c["bg_secondary"]};
    border-top: 1px solid {c["border"]};
}}
#logPanel {{
    background-color: {c["bg_primary"]};
    border: none;
    border-radius: 4px;
    font-family: "Consolas", monospace;
    font-size: 11px;
    color: {c["text_secondary"]};
    padding: 6px;
}}
#downloadProgress {{
    background-color: {c["border"]};
    border: none;
    border-radius: 2px;
    max-height: 4px;
}}
#downloadProgress::chunk {{
    background-color: {c["success"]};
    border-radius: 2px;
}}
"""


# Начальная тема (светлая по умолчанию)
COLORS_MINIMAL = dict(_COLORS_LIGHT)
STYLESHEET_MINIMAL = _build_stylesheet(_COLORS_LIGHT)


def set_dark_mode(dark: bool) -> None:
    """Переключает активную тему. Обновляет COLORS_MINIMAL и STYLESHEET_MINIMAL."""
    global STYLESHEET_MINIMAL
    src = _COLORS_DARK if dark else _COLORS_LIGHT
    COLORS_MINIMAL.clear()
    COLORS_MINIMAL.update(src)
    STYLESHEET_MINIMAL = _build_stylesheet(src)
