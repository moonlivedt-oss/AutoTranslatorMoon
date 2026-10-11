"""Окна живого перевода: прозрачный для ввода слой с переводами и панель управления."""

from __future__ import annotations

import math
import time

from PyQt6.QtCore import QPoint, QRect, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetrics, QImage, QPainter
from PyQt6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QWidget,
)

from ..ui import anim
from ..ui.icons import make_icon
from ..ui.lens import (
    FADE_S,
    STAGGER_MAX_S,
    STAGGER_S,
    TextItem,
    layout_item,
    paint_item,
    settle_overlaps,
)
from ..ui.theme import PALETTE
from ..win.winutil import exclude_from_capture

HIDE_FADE_S = 0.12
_MOVE_TOL = 6.0  # блок сдвинулся меньше — перевод не перекладываем


class LiveOverlay(QWidget):
    """Слой с переводами блоков поверх игры. Клики и клавиши проходят сквозь него."""

    def __init__(self, rect: QRect) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setGeometry(rect)
        self._items: dict[int, TextItem] = {}  # id слота -> перевод на экране
        self._hidden: dict[int, float] = {}  # id слота -> когда начал гаснуть
        self._peek = False  # показать оригинал (все переводы погашены)
        self._peek_at = 0.0
        self._anim = QTimer(self)
        self._anim.setInterval(16)
        self._anim.timeout.connect(self._tick)

    def showEvent(self, event) -> None:  # noqa: N802
        exclude_from_capture(self)  # OCR под слоем видит оригинал
        super().showEvent(event)

    @property
    def has_items(self) -> bool:
        return bool(self._items)

    def item_ids(self) -> list[int]:
        return list(self._items)

    def item(self, slot_id: int) -> TextItem | None:
        return self._items.get(slot_id)

    # ---------- содержимое ----------
    def sync(
        self, entries: list[tuple[int, object, str]], image: QImage | None
    ) -> None:
        """Привести слой к списку (id слота, блок, перевод).

        Перевод, который не изменился и стоит на том же месте, не трогаем — он не
        мигает. Новые проявляются плавно сверху вниз.
        """
        now = time.monotonic()
        dpr = image.devicePixelRatio() if image is not None else 1.0
        dpr = dpr or 1.0
        keep: dict[int, TextItem] = {}
        fresh: list[TextItem] = []
        for sid, b, dst in entries:
            dst = (dst or "").strip()
            rect = QRectF(b.left / dpr, b.top / dpr, b.width / dpr, b.height / dpr)
            old = self._items.get(sid)
            if (
                old is not None
                and old.dst == dst
                and abs(old.rect.left() - rect.left()) < _MOVE_TOL
                and abs(old.rect.top() - rect.top()) < _MOVE_TOL
            ):
                keep[sid] = old
                continue
            if image is None:
                continue
            it = TextItem(
                b.text, rect, b.line_h / dpr, b.bg, getattr(b, "align", "left")
            )
            it.dst = dst
            if not it.visible:
                continue  # перевод совпал с оригиналом (имя, число) — не закрываем
            layout_item(it, self.width(), self.height(), image, dpr)
            keep[sid] = it
            fresh.append(it)
            self._hidden.pop(sid, None)
        if fresh and image is not None:
            settle_overlaps(
                list(keep.values()), self.width(), self.height(), image, dpr
            )
        fresh.sort(key=lambda it: (it.rect.top(), it.rect.left()))
        for n, it in enumerate(fresh):
            it.shown_at = now + min(STAGGER_MAX_S, n * STAGGER_S)
        self._hidden = {k: v for k, v in self._hidden.items() if k in keep}
        self._items = keep
        self._animate()

    def clear(self) -> None:
        self._items = {}
        self._hidden = {}
        self.update()

    def hide_slots(self, ids) -> None:
        """Погасить переводы этих блоков (под ними меняется текст)."""
        now = time.monotonic()
        for sid in ids:
            if sid in self._items and sid not in self._hidden:
                self._hidden[sid] = now
        self._animate()

    def unhide(self, ids=None) -> None:
        """Вернуть погашенные переводы (текст под ними так и не сменился)."""
        now = time.monotonic()
        for sid in list(self._hidden) if ids is None else ids:
            if self._hidden.pop(sid, None) is not None and sid in self._items:
                self._items[sid].shown_at = now  # проявить заново, а не «вспыхнуть»
        self._animate()

    def hidden_ids(self) -> set[int]:
        return set(self._hidden)

    def set_peek(self, on: bool) -> None:
        """Показать оригинал: все переводы гаснут, пока on."""
        if on == self._peek:
            return
        self._peek = on
        now = time.monotonic()
        self._peek_at = now
        if not on:
            for it in self._items.values():
                it.shown_at = now
        self._animate()

    # ---------- анимация и отрисовка ----------
    def _animate(self) -> None:
        if not self._anim.isActive():
            self._anim.start()
        self.update()

    def _tick(self) -> None:
        now = time.monotonic()
        busy = (
            (self._peek and now < self._peek_at + HIDE_FADE_S)
            or any(now < t + HIDE_FADE_S for t in self._hidden.values())
            or any(
                it.shown_at and now < it.shown_at + FADE_S
                for it in self._items.values()
            )
        )
        if not busy:
            self._anim.stop()
        self.update()

    @staticmethod
    def _fade_out(since: float, now: float) -> float:
        return max(0.0, 1.0 - (now - since) / HIDE_FADE_S)

    def paintEvent(self, event) -> None:  # noqa: N802
        if not self._items:
            return
        now = time.monotonic()
        base = self._fade_out(self._peek_at, now) if self._peek else 1.0
        if base <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        for sid, it in self._items.items():
            fade = base
            if sid in self._hidden:
                fade *= self._fade_out(self._hidden[sid], now)
            if fade > 0:
                paint_item(p, it, now, fade)
        p.end()


class LiveBar(QFrame):
    """Панелька управления. Не забирает фокус у игры; её можно перетащить."""

    pause_toggled = pyqtSignal(bool)
    original_toggled = pyqtSignal(bool)
    refresh_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    moved = pyqtSignal(QPoint)  # новое положение левого верхнего угла (после переноса)

    IDLE_OPACITY = 0.6  # пока мышь не над панелью — полупрозрачная, меньше мешает

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._drag: QPoint | None = None
        self._dragged = False
        p = PALETTE

        card = QFrame(self)
        card.setObjectName("livecard")
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)
        lay = QHBoxLayout(card)
        lay.setContentsMargins(12, 3, 3, 3)
        lay.setSpacing(2)
        self._dot = QLabel(card)
        self._dot.setFixedSize(9, 9)
        self._dot.setObjectName("livedot")
        self._dot_fx = QGraphicsOpacityEffect(self._dot)
        self._dot.setGraphicsEffect(self._dot_fx)
        self._pulse_phase = 0.0
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(33)
        self._pulse_timer.timeout.connect(self._pulse)
        lay.addWidget(self._dot)
        lay.addSpacing(5)
        self._status = QLabel(card)
        self._status.setObjectName("livestatus")
        self._status.setFixedWidth(170)
        self._hint = (
            "Листайте игру как обычно: клик / Пробел / Enter — перевод обновится.\n"
            "Зажать Shift — посмотреть оригинал. Панель можно перетащить мышью."
        )
        lay.addWidget(self._status)

        def tool(icon: str, tip: str, *, checkable: bool = False) -> QToolButton:
            b = QToolButton(card)
            b.setIcon(make_icon(icon, p["text"]))
            b.setIconSize(QSize(15, 15))
            b.setToolTip(tip)
            b.setCheckable(checkable)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFixedSize(28, 26)
            lay.addWidget(b)
            return b

        self.orig_btn = tool(
            "eye", "Показать оригинал (или зажать Shift)", checkable=True
        )
        self.orig_btn.toggled.connect(self.original_toggled)
        refresh = tool("refresh", "Перечитать и перевести экран заново")
        refresh.clicked.connect(self.refresh_requested)
        self.pause_btn = tool("pause", "Пауза", checkable=True)
        self.pause_btn.toggled.connect(self.pause_toggled)
        stop = tool("close", "Выключить живой перевод")
        stop.setObjectName("livestop")
        stop.clicked.connect(self.stop_requested)

        surface = QColor(p["surface2"])
        self.setStyleSheet(
            f"""
            QFrame#livecard {{
                background: rgba({surface.red()}, {surface.green()}, {surface.blue()}, 235);
                border: 1px solid {p["border"]}; border-radius: 16px;
            }}
            QLabel#livestatus {{
                color: {p["text"]}; font-family: 'Segoe UI'; font-size: 12px;
                font-weight: 600; background: transparent;
            }}
            QLabel#livedot {{ border-radius: 4px; background: {p["success"]}; }}
            QLabel#livedot[state="work"] {{ background: {p["accent"]}; }}
            QLabel#livedot[state="pause"] {{ background: {p["subtext"]}; }}
            QLabel#livedot[state="error"] {{ background: {p["error"]}; }}
            QToolButton {{
                background: transparent; border: 1px solid transparent; border-radius: 13px;
            }}
            QToolButton:hover {{ background: rgba(255, 255, 255, 20); border-color: {p["accent"]}; }}
            QToolButton:checked {{ background: {p["accent"]}; border-color: {p["accent"]}; }}
            QToolButton#livestop:hover {{ background: {p["error"]}; border-color: {p["error"]}; }}
            QToolTip {{
                background: {p["surface2"]}; color: {p["text"]};
                border: 1px solid {p["accent"]}; border-radius: 6px; padding: 5px 7px;
            }}
            """
        )
        self.set_status("Живой перевод", "ok")

    def showEvent(self, event) -> None:  # noqa: N802
        exclude_from_capture(self)
        super().showEvent(event)
        anim.window_drop_in(self, self.IDLE_OPACITY, dy=-14, ms=260)  # съезжает сверху

    def enterEvent(self, event) -> None:  # noqa: N802
        anim.window_opacity(self, self.windowOpacity(), 1.0, 120)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        anim.window_opacity(self, self.windowOpacity(), self.IDLE_OPACITY, 260)
        super().leaveEvent(event)

    def _pulse(self) -> None:
        """Точка «дышит», пока идёт работа (распознавание, перевод)."""
        self._pulse_phase = (self._pulse_phase + 0.09) % (2 * math.pi)
        self._dot_fx.setOpacity(0.55 + 0.45 * math.cos(self._pulse_phase))

    def set_status(self, text: str, state: str = "ok") -> None:
        fm = QFontMetrics(self._status.font())
        self._status.setText(
            fm.elidedText(text, Qt.TextElideMode.ElideRight, self._status.width())
        )
        self._status.setToolTip(
            text + "\n\n" + self._hint if state == "error" else self._hint
        )
        if self._dot.property("state") != state:
            self._dot.setProperty("state", state)
            self._dot.style().unpolish(self._dot)
            self._dot.style().polish(self._dot)
        if state == "work":
            if not self._pulse_timer.isActive():
                self._pulse_phase = 0.0
                self._pulse_timer.start()
        else:
            self._pulse_timer.stop()
            self._dot_fx.setOpacity(1.0)

    def place_on(self, rect: QRect, offset: QPoint | None = None) -> None:
        """Сверху по центру области или туда, куда её перетащили в прошлый раз."""
        self.adjustSize()
        if offset is not None:
            pos = rect.topLeft() + offset
        else:
            pos = QPoint(rect.center().x() - self.width() // 2, rect.top() + 8)
        x = max(rect.left(), min(pos.x(), rect.right() - self.width()))
        y = max(rect.top(), min(pos.y(), rect.bottom() - self.height()))
        self.move(x, y)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = event.globalPosition().toPoint() - self.pos()
            self._dragged = False

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag is not None:
            self.move(event.globalPosition().toPoint() - self._drag)
            self._dragged = True

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._drag is not None and self._dragged:
            self.moved.emit(self.pos())
        self._drag = None
