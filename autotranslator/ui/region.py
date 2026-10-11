"""Полупрозрачный оверлей на весь виртуальный рабочий стол для выбора прямоугольника.

Пользователь зажимает ЛКМ и тянет рамку; по отпусканию — сигнал selected(rect в
экранных координатах). Esc / ПКМ — отмена.
"""

from __future__ import annotations

from PyQt6.QtCore import QPoint, QRect, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from ..win.winutil import exclude_from_capture, force_foreground
from .theme import PALETTE

_HINT = "Выделите область с текстом  |  Esc / ПКМ — отмена"


class RegionSelector(QWidget):
    selected = pyqtSignal(int, int, int, int)  # left, top, width, height
    cancelled = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.CrossCursor)

        # Объединённая геометрия всех экранов (виртуальный десктоп).
        virt = QRect()
        for scr in QGuiApplication.screens():
            virt = virt.united(scr.geometry())
        self._origin_offset = virt.topLeft()
        self.setGeometry(virt)

        self._start: QPoint | None = None
        self._end: QPoint | None = None

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 90))  # затемнение
        accent = QColor(PALETTE["accent"])
        p.setFont(QFont("Segoe UI", 10))
        if self._start and self._end:
            sel = QRect(self._start, self._end).normalized()
            # «Вырезаем» окно выделения — рисуем его прозрачным поверх затемнения
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            p.fillRect(sel, Qt.GlobalColor.transparent)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            p.setPen(QPen(accent, 2))
            p.drawRect(sel)
            self._draw_label(
                p,
                f"{sel.width()} × {sel.height()}",
                sel.left(),
                sel.top() - 26 if sel.top() > 30 else sel.bottom() + 6,
            )
        else:
            # подсказка у курсора на старте — на том экране, где сейчас мышь
            scr = (
                QGuiApplication.screenAt(self.cursor().pos())
                or QGuiApplication.primaryScreen()
            )
            g = scr.geometry().translated(-self._origin_offset)
            self._draw_label(p, _HINT, g.center().x(), g.top() + 40, centered=True)
        p.end()

    def _draw_label(
        self, p: QPainter, text: str, x: int, y: int, *, centered: bool = False
    ) -> None:
        fm = p.fontMetrics()
        w, h = fm.horizontalAdvance(text) + 16, fm.height() + 8
        if centered:
            x -= w // 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(PALETTE["surface"]))
        p.drawRoundedRect(x, y, w, h, 6, 6)
        p.setPen(QColor(PALETTE["text"]))
        p.drawText(QRect(x, y, w, h), Qt.AlignmentFlag.AlignCenter, text)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.RightButton:
            self.cancelled.emit()
            self.close()
            return
        self._start = event.pos()
        self._end = event.pos()
        self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._start:
            self._end = event.pos()
            self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if not self._start:
            return
        sel = QRect(self._start, self._end).normalized()
        self.close()
        if sel.width() < 5 or sel.height() < 5:
            self.cancelled.emit()
            return
        # Переводим в абсолютные экранные координаты
        off = self._origin_offset
        self.selected.emit(
            sel.left() + off.x(), sel.top() + off.y(), sel.width(), sel.height()
        )

    def showEvent(self, event) -> None:  # noqa: N802
        # затемнение не должно попасть в скриншот, снятый сразу после выделения
        exclude_from_capture(self)
        super().showEvent(event)
        # после глобальной горячей клавиши — забрать фокус, чтобы работал Esc
        QTimer.singleShot(0, lambda: force_foreground(self))

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            self.close()
