"""Плитка панели «С экрана» (иконка над подписью) со своей анимацией.

- наведение: подсветка проявляется, иконка чуть приподнимается и светлеет;
- нажатие: от точки клика расходится волна, иконка «вжимается» и пружинит;
- включение (Живой, Чтение): заливка акцентом растекается от центра.

Плитка рисуется целиком сама, поэтому состояния перетекают друг в друга, а не
переключаются, как в QSS. Цвета берутся из текущей палитры при каждой
отрисовке — смена темы подхватывается сама.
"""

from __future__ import annotations

import math
import time

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import (
    QColor,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import QToolButton

from . import theme


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
        round(a.alpha() + (b.alpha() - a.alpha()) * t),
    )


def ease_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def ease_back(t: float) -> float:
    """С лёгким «перелётом» в конце — пружинистый возврат."""
    t = max(0.0, min(1.0, t))
    c = 1.70158
    return 1 + (c + 1) * (t - 1) ** 3 + c * (t - 1) ** 2


class Tile(QToolButton):
    HOVER_S = 0.15
    CHECK_S = 0.30
    PRESS_DOWN_S = 0.08
    PRESS_UP_S = 0.28
    RIPPLE_S = 0.5
    RADIUS = 9.0

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # прогресс состояний 0..1 — линейный; плавность добавляется при отрисовке
        self._hover = 0.0
        self._check = 0.0
        self._press = 0.0
        self._pressed = False
        self._ripples: list[tuple[QPointF, float]] = []  # (точка клика, когда)
        self._last = time.monotonic()
        self._icon_cache: tuple | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._step)
        self.toggled.connect(lambda _on: self._run())

    # ---------- состояние ----------
    def _run(self) -> None:
        if not self._timer.isActive():
            self._last = time.monotonic()
            self._timer.start()
        self.update()

    @staticmethod
    def _toward(value: float, target: float, dt: float, seconds: float) -> float:
        step = dt / seconds
        if value < target:
            return min(target, value + step)
        return max(target, value - step)

    def _step(self) -> None:
        now = time.monotonic()
        dt, self._last = now - self._last, now
        hover_target = 1.0 if self.underMouse() and self.isEnabled() else 0.0
        check_target = 1.0 if self.isChecked() else 0.0
        press_target = 1.0 if self._pressed else 0.0
        self._hover = self._toward(self._hover, hover_target, dt, self.HOVER_S)
        self._check = self._toward(self._check, check_target, dt, self.CHECK_S)
        speed = self.PRESS_DOWN_S if self._pressed else self.PRESS_UP_S
        self._press = self._toward(self._press, press_target, dt, speed)
        self._ripples = [r for r in self._ripples if now - r[1] < self.RIPPLE_S]
        if (
            self._hover == hover_target
            and self._check == check_target
            and self._press == press_target
            and not self._ripples
        ):
            self._timer.stop()
        self.update()

    def showEvent(self, event) -> None:  # noqa: N802
        self._check = 1.0 if self.isChecked() else 0.0  # при показе — без анимации
        super().showEvent(event)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._run()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._run()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = True
            self._ripples.append((event.position(), time.monotonic()))
            self._run()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._pressed = False
        self._run()
        super().mouseReleaseEvent(event)

    # ---------- отрисовка ----------
    def _tinted_icon(self, color: QColor, size: int) -> QPixmap:
        """Иконка, перекрашенная в цвет состояния (кэш — на один цвет)."""
        dpr = self.devicePixelRatioF()
        key = (self.icon().cacheKey(), color.rgba(), size, dpr)
        if self._icon_cache is not None and self._icon_cache[0] == key:
            return self._icon_cache[1]
        img = (
            self.icon()
            .pixmap(QSize(size, size), dpr)
            .toImage()
            .convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        )
        painter = QPainter(img)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(img.rect(), color)
        painter.end()
        out = QPixmap.fromImage(img)
        out.setDevicePixelRatio(dpr)
        self._icon_cache = (key, out)
        return out

    def paintEvent(self, event) -> None:  # noqa: N802
        p = theme.PALETTE
        now = time.monotonic()
        hover = ease_out(self._hover)
        check = ease_out(self._check)
        accent = QColor(p["accent"])
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        if not self.isEnabled():
            painter.setOpacity(0.45)
        box = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        shape = QPainterPath()
        shape.addRoundedRect(box, self.RADIUS, self.RADIUS)
        reach = math.hypot(box.width(), box.height())

        # подсветка наведения (под заливкой включённой плитки не нужна)
        k = hover * (1 - check)
        if k > 0:
            fill = QColor(accent)
            fill.setAlphaF(0.13 * k)
            edge = QColor(accent)
            edge.setAlphaF(0.40 * k)
            painter.setPen(QPen(edge, 1))
            painter.setBrush(fill)
            painter.drawPath(shape)

        painter.save()
        painter.setClipPath(shape)
        if check > 0:  # «включено»: круг акцента растекается от центра
            grad = QLinearGradient(0, box.top(), 0, box.bottom())
            grad.setColorAt(0.0, QColor(p["accent_hover"]))
            grad.setColorAt(1.0, accent)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(grad)
            r = (reach / 2 + 2) * check
            painter.drawEllipse(box.center(), r, r)
        ink = QColor(p["accent_text"] if check > 0.5 else p["text"])
        for pos, start in self._ripples:  # волны от нажатий
            t = (now - start) / self.RIPPLE_S
            wave = QColor(ink)
            wave.setAlphaF(max(0.0, 0.22 * (1 - t)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(wave)
            r = reach * ease_out(t)
            painter.drawEllipse(pos, r, r)
        painter.restore()

        # иконка и подпись: светлеют при наведении, на заливке — цвета текста акцента
        color = _mix(QColor(p["subtext"]), QColor(p["text"]), hover)
        color = _mix(color, QColor(p["accent_text"]), check)
        size = self.iconSize().width()
        fm = self.fontMetrics()
        gap = 3
        top = (self.height() - (size + gap + fm.height())) / 2 - 1.5 * hover
        press = ease_out(self._press) if self._pressed else ease_back(self._press)
        scale = 1.0 - 0.12 * press
        painter.save()
        painter.translate(self.width() / 2, top + size / 2)
        painter.scale(scale, scale)
        painter.drawPixmap(
            QPointF(-size / 2, -size / 2), self._tinted_icon(color, size)
        )
        painter.restore()
        painter.setPen(color)
        painter.setFont(self.font())
        painter.drawText(
            QRectF(0, top + size + gap, self.width(), fm.height() + 2),
            int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop),
            fm.elidedText(self.text(), Qt.TextElideMode.ElideRight, self.width() - 4),
        )
        painter.end()
