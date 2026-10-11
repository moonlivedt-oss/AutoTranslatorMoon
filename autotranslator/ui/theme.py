"""Оформление AutoTranslator — палитра Catppuccin Mocha (та же, что в
VSCodeLauncher у пользователя) и генератор QSS под виджеты этого окна.

Окно безрамочное и рисует единственную «карточку» с тенью, поэтому фон самого
QWidget прозрачный — все цвета живут на #card и его детях.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

# Catppuccin Mocha (тёмная). Ключи совпадают с launcher/theme.py, чтобы палитра
# ощущалась одинаково в обоих приложениях.
MOCHA = {
    "bg": "#181825",
    "surface": "#1e1e2e",
    "surface2": "#11111b",
    "border": "#313244",
    "text": "#cdd6f4",
    "subtext": "#a6adc8",
    "accent": "#cba6f7",
    "accent_text": "#11111b",
    "accent_hover": "#d9bbff",
    "success": "#a6e3a1",
    "warn": "#f9e2af",
    "error": "#f38ba8",
    "track": "#45475a",
    "input_bg": "#232336",
    "input_text": "#cdd6f4",
}

# Catppuccin Macchiato (тёмная, чуть теплее Mocha).
MACCHIATO = {
    "bg": "#1e2030",
    "surface": "#24273a",
    "surface2": "#181926",
    "border": "#363a4f",
    "text": "#cad3f5",
    "subtext": "#a5adcb",
    "accent": "#c6a0f6",
    "accent_text": "#181926",
    "accent_hover": "#d3b8f8",
    "success": "#a6da95",
    "warn": "#eed49f",
    "error": "#ed8796",
    "track": "#494d64",
    "input_bg": "#262a3f",
    "input_text": "#cad3f5",
}

# Catppuccin Frappé (тёмная, самая мягкая/дымчатая).
FRAPPE = {
    "bg": "#292c3c",
    "surface": "#303446",
    "surface2": "#232634",
    "border": "#414559",
    "text": "#c6d0f5",
    "subtext": "#a5adce",
    "accent": "#ca9ee6",
    "accent_text": "#232634",
    "accent_hover": "#d7b6ee",
    "success": "#a6d189",
    "warn": "#e5c890",
    "error": "#e78284",
    "track": "#51576d",
    "input_bg": "#2f3243",
    "input_text": "#c6d0f5",
}

# Catppuccin Latte (светлая) — те же роли ключей, светлый фон.
LATTE = {
    "bg": "#eff1f5",
    "surface": "#ffffff",
    "surface2": "#e6e9ef",
    "border": "#ccd0da",
    "text": "#4c4f69",
    "subtext": "#6c6f85",
    "accent": "#8839ef",
    "accent_text": "#ffffff",
    "accent_hover": "#7a2fe0",
    "success": "#40a02b",
    "warn": "#df8e1d",
    "error": "#d20f39",
    "track": "#bcc0cc",
    "input_bg": "#eff1f5",
    "input_text": "#4c4f69",
}

# Флейворы Catppuccin по имени + удобные алиасы «тёмная/светлая».
FLAVORS = {"mocha": MOCHA, "macchiato": MACCHIATO, "frappe": FRAPPE, "latte": LATTE}
DARK_FLAVORS = ("mocha", "macchiato", "frappe")  # порядок в меню выбора
FLAVOR_NAMES = {
    "mocha": "Mocha",
    "macchiato": "Macchiato",
    "frappe": "Frappé",
    "latte": "Latte",
}
THEMES = {"dark": MOCHA, "light": LATTE, **FLAVORS}

# PALETTE — «текущая» палитра. Меняется на месте (mutate) в set_theme, чтобы
# уже импортировавшие её модули (overlay/subtitle) увидели новые цвета.
PALETTE = dict(MOCHA)

# Цвет монохромных иконок — в тон приглушённого текста (обновляется в set_theme).
ICON = PALETTE["subtext"]
ICON_ACCENT = PALETTE["accent"]


def set_theme(name: str) -> dict:
    """Переключает текущую палитру на месте и возвращает её."""
    global ICON, ICON_ACCENT
    src = THEMES.get(name, MOCHA)
    PALETTE.clear()
    PALETTE.update(src)
    ICON = PALETTE["subtext"]
    ICON_ACCENT = PALETTE["accent"]
    return PALETTE


def _mix(c1: str, c2: str, t: float) -> str:
    a = [int(c1[i : i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i : i + 2], 16) for i in (1, 3, 5)]
    r, g, bl = (
        max(0, min(255, round(x + (y - x) * t))) for x, y in zip(a, b, strict=True)
    )
    return f"#{r:02x}{g:02x}{bl:02x}"


def _rgba(color: str, a: float) -> str:
    r, g, b = (int(color[i : i + 2], 16) for i in (1, 3, 5))
    return f"rgba({r}, {g}, {b}, {a})"


def _glyph(name: str, color: str, size: int = 12, width: float = 1.6) -> str:
    """PNG-глиф для QSS (стрелка списка, галочка): CSS-треугольники Qt не рисует.

    Файлы кладём во временную папку по имени+цвету — при смене темы просто
    появится новый файл. Без QGuiApplication (тесты) — пустая строка.
    """
    from PyQt6.QtCore import QSize
    from PyQt6.QtGui import QGuiApplication

    if QGuiApplication.instance() is None:
        return ""
    folder = Path(tempfile.gettempdir()) / "autotranslator-ui"
    path = folder / f"{name}-{color.lstrip('#')}-{size}.png"
    if not path.exists():
        from .icons import make_icon

        folder.mkdir(parents=True, exist_ok=True)
        make_icon(name, color, size).pixmap(QSize(size, size), 2.0).save(str(path))
    return path.as_posix()


def build_qss(p: dict | None = None, font_scale: int = 100) -> str:
    p = p if p is not None else PALETTE
    arrow = _glyph("chevron_down", p["subtext"])
    arrow_up = _glyph("chevron_up", p["subtext"], 10)
    arrow_dn = _glyph("chevron_down", p["subtext"], 10)
    tick = _glyph("check", p["accent_text"], 13)
    hov = _mix(p["surface"], p["text"], 0.06)
    s = font_scale / 100.0
    base = round(13 * s)
    small = round(11 * s)
    edit = round(14 * s)
    return f"""
* {{
    font-family: 'Segoe UI', 'Segoe UI Variable', sans-serif;
    font-size: {base}px; color: {p["text"]};
}}
QTextEdit {{ font-size: {edit}px; }}
QLabel#muted, QLabel#chip, QLabel#chipLive {{ font-size: {small}px; }}

/* карточка — единственный видимый фон (окно прозрачное) */
QFrame#card {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {_mix(p["surface"], p["text"], 0.02)}, stop:1 {p["surface"]});
    border: 1px solid {p["border"]}; border-radius: 16px;
}}
QWidget#titlebar {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {_mix(p["surface"], p["accent"], 0.14)}, stop:1 {p["surface"]});
    border-top-left-radius: 16px; border-top-right-radius: 16px;
    border-bottom: 1px solid {p["border"]};
}}
QLabel#titletext {{
    font-size: {base}px; font-weight: 700; letter-spacing: 0.3px; color: {p["text"]};
}}
QLabel#logodot {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 {p["accent_hover"]}, stop:1 {p["accent"]});
    border-radius: 4px;
}}
QLabel#logodot[image="true"] {{ background: transparent; }}
QLabel#muted {{ color: {p["subtext"]}; font-size: {small}px; }}

/* статус-плашка снизу: цвет и фон зависят от свойства state */
QLabel#status {{
    color: {p["subtext"]}; font-size: {small}px; font-weight: 600;
    border: 1px solid transparent; border-radius: 9px; padding: 1px 8px;
}}
QLabel#status[state="working"] {{
    color: {p["accent"]}; background: {_rgba(p["accent"], 0.14)};
    border-color: {_rgba(p["accent"], 0.30)};
}}
QLabel#status[state="success"] {{
    color: {p["success"]}; background: {_rgba(p["success"], 0.14)};
    border-color: {_rgba(p["success"], 0.30)};
}}
QLabel#status[state="error"] {{
    color: {p["error"]}; background: {_rgba(p["error"], 0.14)};
    border-color: {_rgba(p["error"], 0.30)};
}}
QLabel#status[state="offline"] {{
    color: {p["warn"]}; background: {_rgba(p["warn"], 0.14)};
    border-color: {_rgba(p["warn"], 0.30)};
}}

/* точка-индикатор режима движка (онлайн/офлайн) */
QLabel#enginedot {{ border-radius: 5px; background: {p["subtext"]}; }}
QLabel#enginedot[mode="online"] {{ background: {p["success"]}; }}
QLabel#enginedot[mode="offline"] {{ background: {p["accent"]}; }}
QLabel#chip {{
    color: {p["accent"]}; background: {_rgba(p["accent"], 0.14)};
    border: 1px solid {_rgba(p["accent"], 0.35)};
    border-radius: 9px; padding: 1px 9px; font-size: {small}px; font-weight: 600;
}}
QLabel#chipLive {{
    color: {p["success"]}; background: {_rgba(p["success"], 0.14)};
    border: 1px solid {_rgba(p["success"], 0.35)};
    border-radius: 9px; padding: 1px 9px; font-size: {small}px; font-weight: 600;
}}

QTextEdit {{
    background: {p["surface2"]}; border: 1px solid {p["border"]};
    border-radius: 10px; padding: 8px;
    selection-background-color: {p["accent"]}; selection-color: {p["accent_text"]};
}}
QTextEdit:focus {{ border: 1px solid {p["accent"]}; background: {_mix(p["surface2"], p["surface"], 0.5)}; }}
QTextEdit#output {{ background: {_mix(p["surface2"], p["bg"], 0.5)}; }}

QComboBox {{
    background: {p["input_bg"]}; border: 1px solid {p["border"]};
    border-radius: 9px; padding: 6px 10px; min-height: 18px;
}}
QComboBox:hover, QComboBox:focus {{ border-color: {p["accent"]}; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{ image: url({arrow}); width: 12px; height: 12px; }}
QComboBox::down-arrow:on {{ top: 1px; }}
QComboBox QAbstractItemView {{
    background: {p["input_bg"]}; border: 1px solid {p["accent"]};
    border-radius: 9px; padding: 4px; outline: none;
    selection-background-color: {_rgba(p["accent"], 0.30)};
}}

QPushButton {{
    background: {p["input_bg"]}; border: 1px solid {p["border"]};
    border-radius: 9px; padding: 8px 12px; font-weight: 600;
}}
QPushButton:hover {{ background: {hov}; border-color: {p["accent"]}; }}
QPushButton:pressed {{ background: {_mix(p["surface"], p["surface2"], 0.5)}; }}
QPushButton#accent {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {p["accent_hover"]}, stop:1 {p["accent"]});
    color: {p["accent_text"]}; border: 1px solid {p["accent"]};
}}
QPushButton#accent:hover {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {_mix(p["accent_hover"], "#ffffff", 0.18)}, stop:1 {p["accent_hover"]});
}}
QPushButton#accent:pressed {{ background: {p["accent"]}; }}
QPushButton#toggle:checked {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {p["accent_hover"]}, stop:1 {p["accent"]});
    color: {p["accent_text"]}; border: 1px solid {p["accent"]};
}}
QPushButton#toggle:checked:hover {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {_mix(p["accent_hover"], "#ffffff", 0.18)}, stop:1 {p["accent_hover"]});
}}

QToolButton {{
    background: transparent; border: 1px solid transparent; border-radius: 9px;
}}
QToolButton:hover {{ background: {_rgba(p["accent"], 0.14)}; border-color: {_rgba(p["accent"], 0.35)}; }}
QToolButton:pressed {{ background: {_rgba(p["accent"], 0.22)}; }}
QToolButton:checked {{
    background: {_rgba(p["accent"], 0.22)}; border-color: {p["accent"]};
}}
QToolButton#closebtn:hover {{ background: {_rgba(p["error"], 0.85)}; border-color: {p["error"]}; }}

QCheckBox {{ spacing: 7px; color: {p["subtext"]}; background: transparent; }}
QCheckBox::indicator {{
    width: 17px; height: 17px; border-radius: 6px;
    border: 1px solid {p["border"]}; background: {p["input_bg"]};
}}
QCheckBox::indicator:hover {{ border-color: {p["accent"]}; }}
QCheckBox::indicator:checked {{
    background: {p["accent"]}; border-color: {p["accent"]}; image: url({tick});
}}
QCheckBox:hover {{ color: {p["text"]}; }}

QSpinBox {{
    background: {p["input_bg"]}; border: 1px solid {p["border"]};
    border-radius: 9px; padding: 4px 8px; min-height: 18px;
}}
QSpinBox:hover, QSpinBox:focus {{ border-color: {p["accent"]}; }}
QSpinBox::up-button, QSpinBox::down-button {{
    border: none; background: transparent; width: 18px;
}}
QSpinBox::up-arrow {{ image: url({arrow_up}); width: 10px; height: 10px; }}
QSpinBox::down-arrow {{ image: url({arrow_dn}); width: 10px; height: 10px; }}

/* поле текста с «подвалом» кнопок: рамка общая, подсветка при фокусе */
QFrame#field, QFrame#fieldOut {{
    background: {p["surface2"]}; border: 1px solid {p["border"]}; border-radius: 12px;
}}
QFrame#fieldOut {{ background: {_mix(p["surface2"], p["bg"], 0.5)}; }}
QFrame#field[focused="true"], QFrame#fieldOut[focused="true"] {{
    border: 1px solid {p["accent"]};
}}
QFrame#field QTextEdit, QFrame#fieldOut QTextEdit {{
    background: transparent; border: none; padding: 10px 10px 4px 10px;
}}
QFrame#field QToolButton, QFrame#fieldOut QToolButton {{ border-radius: 8px; }}

/* значок движка в подвале перевода: клик переключает онлайн/офлайн */
QToolButton#enginechip {{
    color: {p["subtext"]}; font-size: {small}px; font-weight: 600;
    background: {_rgba(p["text"], 0.06)}; border: 1px solid {p["border"]};
    border-radius: 9px; padding: 2px 9px;
}}
QToolButton#enginechip[mode="online"] {{ color: {p["success"]}; }}
QToolButton#enginechip[mode="offline"] {{ color: {p["accent"]}; }}
QToolButton#enginechip:hover {{ border-color: {p["accent"]}; }}

/* панели: настройки и строка игры */
QFrame#panel {{
    background: {_rgba(p["text"], 0.035)}; border: 1px solid {p["border"]};
    border-radius: 12px;
}}
QFrame#panel QLabel#muted {{ font-size: {small}px; }}
QLabel#section {{
    color: {p["subtext"]}; font-size: {max(9, small - 1)}px; font-weight: 700;
    letter-spacing: 1px; padding-left: 2px;
}}

/* панель «С экрана»: плитки «иконка над подписью» в общей рамке */
QFrame#toolbar {{
    background: {_rgba(p["text"], 0.035)}; border: 1px solid {p["border"]};
    border-radius: 12px;
}}
QToolButton#tile {{
    color: {p["subtext"]}; font-size: {small}px; font-weight: 600;
    padding: 5px 2px 4px 2px; border-radius: 9px; min-height: 36px;
}}
QToolButton#tile:hover {{ color: {p["text"]}; }}
QToolButton#tile:checked {{
    color: {p["accent_text"]}; border-color: {p["accent"]};
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {p["accent_hover"]}, stop:1 {p["accent"]});
}}
QToolButton#tileSmall {{ border-radius: 9px; }}


/* страница настроек */
QLabel#pagetitle {{ font-size: {round(base * 1.15)}px; font-weight: 700; }}
QScrollArea#settingsScroll, QWidget#settingsBody {{ background: transparent; }}
QFrame#panel QCheckBox {{ color: {p["text"]}; }}
QFrame#panel QLabel {{ color: {p["text"]}; }}
QFrame#panel QLabel#muted {{ color: {p["subtext"]}; }}
QFrame#titlesep {{ background: {p["border"]}; }}
QToolButton#stepper {{
    font-weight: 700; background: {p["input_bg"]}; border: 1px solid {p["border"]};
}}
QToolButton#stepper:hover {{ border-color: {p["accent"]}; }}

/* сегментный переключатель (тема) */
QPushButton#seg {{
    font-weight: 600; padding: 6px 8px; border-radius: 0; color: {p["subtext"]};
}}
QPushButton#seg[pos="first"] {{
    border-top-left-radius: 9px; border-bottom-left-radius: 9px;
}}
QPushButton#seg[pos="mid"] {{ border-left: none; border-right: none; }}
QPushButton#seg[pos="last"] {{
    border-top-right-radius: 9px; border-bottom-right-radius: 9px;
}}
QPushButton#seg:checked {{
    color: {p["accent"]}; background: {_rgba(p["accent"], 0.16)};
    border-color: {p["accent"]};
}}
QComboBox:disabled, QSpinBox:disabled {{ color: {p["subtext"]}; border-color: {p["border"]}; }}

QSlider::groove:horizontal {{ height: 4px; background: {p["track"]}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {p["accent"]}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    width: 14px; height: 14px; background: {p["text"]};
    border: 2px solid {p["accent"]}; border-radius: 7px; margin: -6px 0;
}}
QSlider::handle:horizontal:hover {{ background: {p["accent_hover"]}; }}

QScrollBar:vertical {{ background: transparent; width: 9px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {p["track"]}; border-radius: 4px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: {p["accent"]}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QSizeGrip {{ width: 14px; height: 14px; background: transparent; }}
QToolTip {{
    background: {p["surface2"]}; color: {p["text"]};
    border: 1px solid {p["accent"]}; border-radius: 6px; padding: 5px 7px;
}}
"""
