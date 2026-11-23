"""
Стили для приложения (Qt Style Sheets)
Современный минималистичный дизайн
"""

import os
import tempfile

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

# ── Компактная светлая тема (Fluent / Windows 11 минимализм) ─────────────────
_COLORS_LIGHT = {
    "bg_primary":       "#f3f4f6",
    "bg_secondary":     "#ffffff",
    "bg_input":         "#ffffff",
    "bg_hover":         "#f8fafc",
    "row_hover":        "#f5f7fa",
    "text_primary":     "#1a1a2e",
    "text_secondary":   "#64748b",
    "text_placeholder": "#94a3b8",
    "accent":           "#2563eb",
    "accent_hover":     "#1d4ed8",
    "accent_pressed":   "#1e40af",
    "accent_light":     "#e0edff",
    "success":          "#16a34a",
    "warning":          "#d97706",
    "error":            "#dc2626",
    "info":             "#2563eb",
    "border":           "#e2e8f0",
    "border_strong":    "#cbd5e1",
    "border_focus":     "#2563eb",
}

# ── Компактная тёмная тема ───────────────────────────────────────────────────
_COLORS_DARK = {
    "bg_primary":       "#1e1e1e",
    "bg_secondary":     "#252526",
    "bg_input":         "#2d2d30",
    "bg_hover":         "#333337",
    "row_hover":        "rgba(255,255,255,0.04)",
    "text_primary":     "#cccccc",
    "text_secondary":   "#858585",
    "text_placeholder": "#555555",
    "accent":           "#0ea5e9",
    "accent_hover":     "#38bdf8",
    "accent_pressed":   "#7dd3fc",
    "accent_light":     "rgba(14,165,233,0.18)",
    "success":          "#4ec9b0",
    "warning":          "#ce9178",
    "error":            "#f48771",
    "info":             "#9cdcfe",
    "border":           "rgba(255,255,255,0.10)",
    "border_strong":    "rgba(255,255,255,0.20)",
    "border_focus":     "#0ea5e9",
}


def _ensure_svg_icons(c: dict) -> dict:
    """Создаёт векторные SVG-иконки для QSS (шевроны комбобокса и галочка чекбокса)."""
    icon_dir = os.path.join(tempfile.gettempdir(), "smarttubedl_ui_icons")
    os.makedirs(icon_dir, exist_ok=True)

    sec = c["text_secondary"]
    acc = c["accent"]
    tag = f"{sec.strip('#')}_{acc.strip('#')}"

    specs = {
        "chevron_down": (
            f"chevron_down_{tag}.svg",
            f'<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12">'
            f'<path d="M2.5 4.25L6 7.75L9.5 4.25" fill="none" stroke="{sec}" '
            f'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>'
        ),
        "chevron_down_hover": (
            f"chevron_down_hover_{tag}.svg",
            f'<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12">'
            f'<path d="M2.5 4.25L6 7.75L9.5 4.25" fill="none" stroke="{acc}" '
            f'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>'
        ),
        "chevron_up": (
            f"chevron_up_{tag}.svg",
            f'<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12">'
            f'<path d="M2.5 7.75L6 4.25L9.5 7.75" fill="none" stroke="{acc}" '
            f'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>'
        ),
        "checkmark": (
            "checkmark_white.svg",
            '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12">'
            '<path d="M2.3 6.2L4.9 8.8L9.7 3.4" fill="none" stroke="#ffffff" '
            'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>'
        ),
    }

    paths = {}
    for key, (fname, svg) in specs.items():
        fpath = os.path.join(icon_dir, fname)
        try:
            if not os.path.exists(fpath):
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(svg)
            paths[key] = fpath.replace("\\", "/")
        except OSError:
            paths[key] = ""
    return paths


def _build_stylesheet(c: dict) -> str:
    """Генерирует QSS из цветового словаря (современный Fluent-минимализм)."""
    icons = _ensure_svg_icons(c)
    chev_down = f'image: url("{icons["chevron_down"]}");' if icons["chevron_down"] else "image: none;"
    chev_hover = f'image: url("{icons["chevron_down_hover"]}");' if icons["chevron_down_hover"] else "image: none;"
    chev_up = f'image: url("{icons["chevron_up"]}");' if icons["chevron_up"] else "image: none;"
    chk_svg = f'image: url("{icons["checkmark"]}");' if icons["checkmark"] else ""

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
    border-radius: 8px;
    margin-top: 10px;
    padding: 10px;
    padding-top: 18px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 6px;
    color: {c["text_secondary"]};
    font-size: 11px;
    font-weight: 600;
}}

/* === ПОЛЯ ВВОДА === */
QLineEdit, QTextEdit, QPlainTextEdit {{
    background-color: {c["bg_input"]};
    border: 1px solid {c["border_strong"]};
    border-radius: 6px;
    padding: 4px 10px;
    color: {c["text_primary"]};
    selection-background-color: {c["accent"]};
    selection-color: white;
}}
QLineEdit:hover, QTextEdit:hover, QPlainTextEdit:hover {{
    border: 1px solid {c["text_secondary"]};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border: 1px solid {c["border_focus"]};
}}

/* === КНОПКИ === */
QPushButton {{
    background-color: {c["accent"]};
    color: white;
    border: none;
    border-radius: 6px;
    padding: 5px 14px;
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
    border: 1px solid {c["border_strong"]};
}}
QPushButton[secondary="true"]:hover {{
    background-color: {c["bg_hover"]};
    border-color: {c["accent"]};
    color: {c["accent"]};
}}
QPushButton[secondary="true"]:pressed {{
    background-color: {c["accent_light"]};
    border-color: {c["accent"]};
}}

/* === КОМБОБОКС === */
QComboBox {{
    background-color: {c["bg_input"]};
    border: 1px solid {c["border_strong"]};
    border-radius: 6px;
    padding: 4px 26px 4px 10px;
    color: {c["text_primary"]};
}}
QComboBox:hover {{
    background-color: {c["bg_hover"]};
    border-color: {c["text_secondary"]};
}}
QComboBox:focus, QComboBox:on {{
    border: 1px solid {c["border_focus"]};
    background-color: {c["bg_input"]};
}}
QComboBox:disabled {{
    background-color: {c["bg_primary"]};
    color: {c["text_placeholder"]};
    border-color: {c["border"]};
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 24px;
    border: none;
    background: transparent;
}}
QComboBox::down-arrow {{
    {chev_down}
    width: 12px;
    height: 12px;
}}
QComboBox::down-arrow:hover {{
    {chev_hover}
}}
QComboBox::down-arrow:on {{
    {chev_up}
}}
QComboBox QAbstractItemView {{
    background-color: {c["bg_secondary"]};
    border: 1px solid {c["border_strong"]};
    border-radius: 6px;
    padding: 4px;
    selection-background-color: {c["accent_light"]};
    selection-color: {c["text_primary"]};
    outline: none;
}}
QComboBox QAbstractItemView::item {{
    padding: 5px 10px;
    min-height: 20px;
    border-radius: 4px;
    color: {c["text_primary"]};
}}
QComboBox QAbstractItemView::item:hover {{
    background-color: {c["row_hover"]};
}}
QComboBox QAbstractItemView::item:selected {{
    background-color: {c["accent_light"]};
    color: {c["accent"]};
    font-weight: 500;
}}

/* === ЧЕКБОКСЫ === */
QCheckBox {{
    spacing: 7px;
    color: {c["text_primary"]};
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {c["border_strong"]};
    border-radius: 4px;
    background-color: {c["bg_input"]};
}}
QCheckBox::indicator:hover {{
    border-color: {c["accent"]};
    background-color: {c["bg_hover"]};
}}
QCheckBox::indicator:checked {{
    background-color: {c["accent"]};
    border-color: {c["accent"]};
    {chk_svg}
}}
QCheckBox::indicator:checked:hover {{
    background-color: {c["accent_hover"]};
    border-color: {c["accent_hover"]};
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
    padding: 4px 8px;
    border: none;
    border-bottom: 1px solid {c["border"]};
    outline: none;
}}
QTableWidget::item:hover, QTableView::item:hover {{
    background-color: {c["row_hover"]};
}}
QTableWidget::item:selected, QTableView::item:selected {{
    background-color: {c["accent_light"]};
    color: {c["text_primary"]};
}}
QHeaderView::section {{
    background-color: {c["bg_primary"]};
    color: {c["text_secondary"]};
    padding: 5px 8px;
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
    width: 8px;
    margin: 2px 1px;
}}
QScrollBar::handle:vertical {{
    background-color: {c["border_strong"]};
    min-height: 24px;
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
    height: 8px;
    margin: 1px 2px;
}}
QScrollBar::handle:horizontal {{
    background-color: {c["border_strong"]};
    min-width: 24px;
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
    border: 1px solid {c["border_strong"]};
    border-radius: 6px;
    padding: 4px;
}}
QMenu::item {{
    padding: 5px 22px 5px 10px;
    border-radius: 4px;
}}
QMenu::item:selected {{
    background-color: {c["accent_light"]};
    color: {c["accent"]};
}}
QMenu::separator {{
    height: 1px;
    background-color: {c["border"]};
    margin: 4px 6px;
}}

/* === ТУЛТИПЫ === */
QToolTip {{
    background-color: {c["bg_secondary"]};
    color: {c["text_primary"]};
    border: 1px solid {c["border_strong"]};
    border-radius: 6px;
    padding: 5px 9px;
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
    padding: 5px 12px;
    border-radius: 6px;
    background-color: {c["bg_input"]};
    border: 1px solid {c["border_strong"]};
    color: {c["text_primary"]};
}}
#searchInput:hover {{
    border: 1px solid {c["text_secondary"]};
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
    border-radius: 6px;
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
    border-radius: 6px;
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
