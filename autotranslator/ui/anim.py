"""Анимации интерфейса: проявление, сдвиг страниц, появление окон.

Всё короткое (120–260 мс) и с плавным замедлением в конце (OutCubic): движение
должно подсказывать, что изменилось, а не заставлять ждать. Эффекты прозрачности
снимаются по окончании — постоянный QGraphicsEffect замедляет отрисовку.
"""

from __future__ import annotations

from PyQt6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
    QVariantAnimation,
)
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QWidget

EASE = QEasingCurve.Type.OutCubic

# ссылки на идущие анимации: без них Python соберёт объект раньше времени
_RUNNING: dict[int, QAbstractAnimation] = {}


def _keep(key: QWidget, anim: QAbstractAnimation) -> None:
    old = _RUNNING.pop(id(key), None)
    if old is not None:
        old.stop()
    _RUNNING[id(key)] = anim
    anim.finished.connect(lambda: _RUNNING.pop(id(key), None))
    anim.start()


def _opacity_effect(widget: QWidget) -> QGraphicsOpacityEffect | None:
    """Эффект прозрачности для анимации. None — у виджета уже свой эффект (тень)."""
    current = widget.graphicsEffect()
    if isinstance(current, QGraphicsOpacityEffect):
        return current
    if current is not None:
        return None
    fx = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(fx)
    return fx


def fade_in(widget: QWidget, ms: int = 200, start: float = 0.0) -> None:
    """Проявить виджет (внутри окна) от start до полной непрозрачности."""
    fx = _opacity_effect(widget)
    if fx is None:
        return
    anim = QPropertyAnimation(fx, b"opacity")
    anim.setDuration(ms)
    anim.setStartValue(start)
    anim.setEndValue(1.0)
    anim.setEasingCurve(EASE)

    def done() -> None:
        if widget.graphicsEffect() is fx:
            widget.setGraphicsEffect(None)  # без эффекта рисуется быстрее

    anim.finished.connect(done)
    _keep(widget, anim)


def slide_in(widget: QWidget, dx: int, ms: int = 240) -> None:
    """Страница въезжает сбоку (dx > 0 — справа) и проявляется."""
    end = QPoint(widget.pos())
    group = QParallelAnimationGroup()
    move = QPropertyAnimation(widget, b"pos")
    move.setDuration(ms)
    move.setStartValue(end + QPoint(dx, 0))
    move.setEndValue(end)
    move.setEasingCurve(EASE)
    group.addAnimation(move)
    fx = _opacity_effect(widget)
    if fx is not None:
        fade = QPropertyAnimation(fx, b"opacity")
        fade.setDuration(ms)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(EASE)
        group.addAnimation(fade)

    def done() -> None:
        widget.move(end)
        if fx is not None and widget.graphicsEffect() is fx:
            widget.setGraphicsEffect(None)

    group.finished.connect(done)
    _keep(widget, group)


def window_opacity(window: QWidget, start: float, end: float, ms: int, on_done=None):
    """Прозрачность целого окна (своего верхнего уровня) от start до end."""
    anim = QPropertyAnimation(window, b"windowOpacity")
    anim.setDuration(ms)
    anim.setStartValue(start)
    anim.setEndValue(end)
    anim.setEasingCurve(EASE)
    if on_done is not None:
        anim.finished.connect(on_done)
    _keep(window, anim)
    return anim


def window_drop_in(window: QWidget, opacity: float = 1.0, dy: int = -10, ms: int = 220):
    """Окно появляется: проявляется и чуть съезжает на место (сверху по умолчанию)."""
    end = QPoint(window.pos())
    window.move(end + QPoint(0, dy))
    window.setWindowOpacity(0.0)
    group = QParallelAnimationGroup()
    move = QPropertyAnimation(window, b"pos")
    move.setDuration(ms)
    move.setStartValue(end + QPoint(0, dy))
    move.setEndValue(end)
    move.setEasingCurve(EASE)
    fade = QPropertyAnimation(window, b"windowOpacity")
    fade.setDuration(ms)
    fade.setStartValue(0.0)
    fade.setEndValue(opacity)
    fade.setEasingCurve(EASE)
    group.addAnimation(move)
    group.addAnimation(fade)
    _keep(window, group)


def tween(owner: QWidget, start: float, end: float, ms: int, on_value) -> None:
    """Числовая анимация для своих отрисовок (ручки переключателей и т.п.)."""
    anim = QVariantAnimation(owner)
    anim.setDuration(ms)
    anim.setStartValue(float(start))
    anim.setEndValue(float(end))
    anim.setEasingCurve(EASE)
    anim.valueChanged.connect(on_value)
    _keep(owner, anim)
