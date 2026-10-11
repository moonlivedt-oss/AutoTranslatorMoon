"""Мелкие виджеты главного окна: кнопки с иконками, шапка (перетаскивание окна),
переключатель и полоса «идёт работа»."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import QEvent, QObject, QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..paths import asset
from . import theme
from .anim import tween
from .icons import make_icon

if TYPE_CHECKING:
    from ..app import MainWindow


class Field(QFrame):
    """Текстовое поле с «подвалом» для своих кнопок (как в Google Translate).

    Рамка общая на поле и подвал; при фокусе в тексте подсвечивается вся
    карточка — так видно, куда сейчас идёт ввод.
    """

    def __init__(self, edit: QTextEdit, name: str = "field") -> None:
        super().__init__()
        self.setObjectName(name)
        self.setProperty("focused", False)
        self.edit = edit
        edit.setFrameShape(QFrame.Shape.NoFrame)
        edit.installEventFilter(self)
        self.footer = QHBoxLayout()
        self.footer.setContentsMargins(10, 0, 6, 6)
        self.footer.setSpacing(4)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(1, 1, 1, 1)
        lay.setSpacing(0)
        lay.addWidget(edit, 1)
        lay.addLayout(self.footer)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if obj is self.edit and event.type() in (
            QEvent.Type.FocusIn,
            QEvent.Type.FocusOut,
        ):
            self.setProperty("focused", event.type() == QEvent.Type.FocusIn)
            self.style().unpolish(self)
            self.style().polish(self)
        return super().eventFilter(obj, event)


def section_label(text: str) -> QLabel:
    """Мелкий заголовок группы кнопок («С экрана», «Настройки»)."""
    lbl = QLabel(text.upper())
    lbl.setObjectName("section")
    return lbl


def tool_button(
    icon_name: str, tooltip: str, *, checkable=False, size=30
) -> QToolButton:
    btn = QToolButton()
    btn.setIcon(make_icon(icon_name, theme.ICON))
    btn.setIconSize(QSize(16, 16))
    btn.setToolTip(tooltip)
    btn.setCheckable(checkable)
    btn.setFixedSize(size, size)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    return btn


def text_button(icon_name: str, text: str, tooltip: str = "") -> QPushButton:
    btn = QPushButton("  " + text)
    btn.setIcon(make_icon(icon_name, theme.ICON))
    btn.setIconSize(QSize(16, 16))
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    if tooltip:
        btn.setToolTip(tooltip)
    return btn


class TitleBar(QWidget):
    """Шапка окна: заголовок + перетаскивание + история/тема/pin/свернуть/закрыть."""

    def __init__(self, window: MainWindow) -> None:
        super().__init__()
        self._win = window
        self._drag_offset = None
        self.setFixedHeight(38)
        self.setObjectName("titlebar")

        dot = QLabel()
        dot.setObjectName("logodot")
        logo = QPixmap(asset("icon.png"))
        if logo.isNull():  # нет файла ассета — остаётся цветная точка из темы
            dot.setFixedSize(9, 9)
        else:
            dpr = self.devicePixelRatioF()
            logo = logo.scaled(
                round(18 * dpr),
                round(18 * dpr),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            logo.setDevicePixelRatio(dpr)
            dot.setPixmap(logo)
            dot.setFixedSize(18, 18)
            dot.setProperty("image", True)
        title = QLabel("AutoTranslator")
        title.setObjectName("titletext")

        self.history_btn = window._reg_tool(
            "history", "История переводов (Ctrl+H — поиск по всей истории)", size=28
        )
        self.history_btn.clicked.connect(window.show_history_menu)
        # «настройки» — переключатель страницы; подключает его само окно
        self.settings_btn = window._reg_tool(
            "gear", "Настройки (Ctrl+,)", checkable=True, size=28
        )
        self.theme_btn = window._reg_tool(
            "theme",
            "ЛКМ — тёмная / светлая / системная\nПКМ — флейвор Catppuccin",
            size=28,
        )
        self.theme_btn.clicked.connect(window.toggle_theme)
        self.theme_btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.theme_btn.customContextMenuRequested.connect(
            lambda _: window.show_flavor_menu()
        )
        self.pin_btn = window._reg_tool(
            "pin", "Поверх всех окон", checkable=True, size=28
        )
        self.pin_btn.toggled.connect(window.set_always_on_top)
        min_btn = window._reg_tool("min", "Свернуть в трей", size=28)
        min_btn.clicked.connect(window.hide_to_tray)
        close_btn = window._reg_tool("close", "Закрыть", size=28)
        close_btn.setObjectName("closebtn")
        close_btn.clicked.connect(window.close)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 8, 0)
        lay.setSpacing(4)
        lay.addWidget(dot)
        lay.addSpacing(2)
        lay.addWidget(title)
        lay.addStretch(1)
        lay.addWidget(self.history_btn)
        lay.addWidget(self.theme_btn)
        lay.addWidget(self.pin_btn)
        lay.addWidget(self.settings_btn)
        sep = QFrame()
        sep.setObjectName("titlesep")
        sep.setFixedSize(1, 16)
        lay.addSpacing(2)
        lay.addWidget(sep)
        lay.addSpacing(2)
        lay.addWidget(min_btn)
        lay.addWidget(close_btn)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = (
                event.globalPosition().toPoint() - self._win.frameGeometry().topLeft()
            )

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_offset is not None:
            self._win.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_offset = None


class Switch(QCheckBox):
    """Переключатель «как в настройках телефона»: подпись слева, тумблер справа.

    Тот же QCheckBox по API (toggled, setChecked, text) — заменяет галочки без
    правок в обработчиках. Ручка переезжает плавно, дорожка перетекает в акцент.
    Кликается вся строка, а не только тумблер.
    """

    TRACK_W, TRACK_H = 34, 18

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(text, parent)
        self._t = 0.0  # 0 — выкл, 1 — вкл (промежуточное — во время анимации)
        self._hover = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.toggled.connect(self._animate)

    def _animate(self, on: bool) -> None:
        if not self.isVisible():
            self._t = 1.0 if on else 0.0
            self.update()
            return
        tween(self, self._t, 1.0 if on else 0.0, 170, self._set_t)

    def _set_t(self, value) -> None:
        self._t = float(value)
        self.update()

    def setChecked(self, on: bool) -> None:  # noqa: N802
        super().setChecked(on)
        if not self.isVisible():
            self._t = 1.0 if on else 0.0

    def showEvent(self, event) -> None:  # noqa: N802
        self._t = 1.0 if self.isChecked() else 0.0
        super().showEvent(event)

    def sizeHint(self) -> QSize:  # noqa: N802
        fm = self.fontMetrics()
        w = fm.horizontalAdvance(self.text()) + 14 + self.TRACK_W + 2
        return QSize(w, max(26, fm.height() + 6))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def hitButton(self, pos) -> bool:  # noqa: N802
        return self.rect().contains(pos)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = theme.PALETTE
        t = self._t
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        enabled = self.isEnabled()
        # подпись
        painter.setPen(QColor(p["text"] if enabled else p["subtext"]))
        painter.setFont(self.font())
        text_rect = self.rect().adjusted(0, 0, -(self.TRACK_W + 12), 0)
        painter.drawText(
            text_rect,
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            self.fontMetrics().elidedText(
                self.text(), Qt.TextElideMode.ElideRight, text_rect.width()
            ),
        )
        # дорожка: цвет перетекает от «выкл» к акценту
        track = QRectF(
            self.width() - self.TRACK_W - 1,
            (self.height() - self.TRACK_H) / 2,
            self.TRACK_W,
            self.TRACK_H,
        )
        off, on = QColor(p["track"]), QColor(p["accent"])
        fill = QColor(
            round(off.red() + (on.red() - off.red()) * t),
            round(off.green() + (on.green() - off.green()) * t),
            round(off.blue() + (on.blue() - off.blue()) * t),
        )
        if not enabled:
            fill.setAlpha(110)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(track, self.TRACK_H / 2, self.TRACK_H / 2)
        if self._hover and enabled:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(p["accent"]), 1.2))
            painter.drawRoundedRect(
                track.adjusted(-1.5, -1.5, 1.5, 1.5),
                self.TRACK_H / 2 + 1.5,
                self.TRACK_H / 2 + 1.5,
            )
        # ручка: едет и чуть «вытягивается» на середине пути
        d = self.TRACK_H - 6
        stretch = 4 * (1 - abs(2 * t - 1))
        x = track.left() + 3 + (track.width() - d - 6 - stretch) * t
        knob = QRectF(x, track.top() + 3, d + stretch, d)
        painter.setPen(Qt.PenStyle.NoPen)
        light = QColor(p["surface"]).lightness() > 128  # светлая тема — ручка белая
        knob_off = p["surface"] if light else p["text"]
        painter.setBrush(QColor(p["accent_text"] if t > 0.5 else knob_off))
        painter.drawRoundedRect(knob, d / 2, d / 2)
        painter.end()


class BusyLine(QWidget):
    """Тонкая бегущая полоса «идёт работа» (перевод, распознавание).

    Место под неё занято всегда — включение не сдвигает вёрстку; появляется и
    гаснет плавно.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(2)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._phase = 0.0
        self._alpha = 0.0  # 0..1 — видимость полосы
        self._on = False
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    def set_busy(self, on: bool) -> None:
        if on == self._on:
            return
        self._on = on
        if not self._timer.isActive():
            self._timer.start()

    def _tick(self) -> None:
        self._phase = (self._phase + 0.012) % 1.0
        target = 1.0 if self._on else 0.0
        self._alpha += (target - self._alpha) * 0.15
        if not self._on and self._alpha < 0.02:
            self._alpha = 0.0
            self._timer.stop()
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        if self._alpha <= 0:
            return
        p = QPainter(self)
        w = self.width()
        seg = w * 0.35
        x = -seg + (w + seg) * self._phase
        accent = QColor(theme.PALETTE["accent"])
        grad = QLinearGradient(x, 0, x + seg, 0)
        clear = QColor(accent)
        clear.setAlpha(0)
        solid = QColor(accent)
        solid.setAlpha(round(255 * self._alpha))
        grad.setColorAt(0.0, clear)
        grad.setColorAt(0.5, solid)
        grad.setColorAt(1.0, clear)
        p.fillRect(QRectF(x, 0, seg, self.height()), grad)
        p.end()
