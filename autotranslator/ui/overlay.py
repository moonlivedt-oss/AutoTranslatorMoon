"""Постоянная рамка-маркер выбранной области для режима чтения.

В отличие от RegionSelector, этот оверлей не исчезает: он висит поверх всех окон
тонкой сиреневой рамкой и показывает, какую именно область AutoTranslator
перечитывает.

Рамку можно двигать и тянуть мышью:
- за любую часть рамки — перетаскивание;
- за углы — изменение размера.

При этом *внутренняя* область (сам захватываемый прямоугольник) вырезана из окна
маской (setMask), поэтому клики/прокрутка внутри проходят насквозь к окну под
рамкой — источник (книга, PDF, манга) остаётся рабочим. Рамка живёт в поле шириной
M снаружи области, так что её линии не попадают в скриншот OCR.
"""

from __future__ import annotations

import math

from PyQt6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRect,
    QSize,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen, QRegion
from PyQt6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..win.winutil import exclude_from_capture
from . import anim
from .icons import make_icon
from .theme import PALETTE

M = 12  # ширина поля-рамки вокруг области (и зона захвата мышью), px
CORNER = 26  # размер угловой зоны для изменения размера, px
MIN_SIDE = 48  # минимальная сторона захватываемой области, px

# зона -> курсор
_CURSORS = {
    "move": Qt.CursorShape.SizeAllCursor,
    "nw": Qt.CursorShape.SizeFDiagCursor,
    "se": Qt.CursorShape.SizeFDiagCursor,
    "ne": Qt.CursorShape.SizeBDiagCursor,
    "sw": Qt.CursorShape.SizeBDiagCursor,
}


class ReadingOverlay(QWidget):
    # новая область (left, top, width, height) в экранных координатах — после
    # того как пользователь подвигал/растянул рамку мышью
    region_changed = pyqtSignal(int, int, int, int)
    menu_requested = pyqtSignal(QPoint)  # ПКМ по рамке — меню управления чтением

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self._active = True  # True — идёт распознавание, False — пауза
        self._zone: str | None = None
        self._press_global: QPoint | None = None
        self._press_geom: QRect | None = None
        # анимация «сканирования»: бегущий пунктир по рамке + пульсация гало
        self._phase = 0.0
        self._anim = QTimer(self)
        self._anim.setInterval(40)  # ~25 fps
        self._anim.timeout.connect(self._tick)
        self.setToolTip("Тяните рамку — двигать, углы — размер, ПКМ — меню")

    def _tick(self) -> None:
        self._phase = (self._phase + 1.0) % 100000.0
        self.update()

    # ---------- внешнее управление ----------
    def set_region(self, left: int, top: int, width: int, height: int) -> None:
        """Ставит рамку вокруг области (координаты самой области, экранные).

        Программная установка — сигнал region_changed НЕ шлём (чтобы не зациклить
        с обработчиком в окне)."""
        self.setGeometry(left - M, top - M, width + 2 * M, height + 2 * M)
        self._apply_mask()
        self.update()

    def current_region(self) -> tuple[int, int, int, int]:
        """Текущая захватываемая область (внутренний прямоугольник) в экранных координатах."""
        g = self.geometry()
        return g.left() + M, g.top() + M, g.width() - 2 * M, g.height() - 2 * M

    def set_active(self, active: bool) -> None:
        """Меняет цвет рамки: активна (акцент) / на паузе (приглушённо)."""
        self._active = active
        if active:
            self._anim.start()
        else:
            self._anim.stop()
        self.update()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._anim.stop()
        super().hideEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        exclude_from_capture(self)
        if self._active:
            self._anim.start()
        super().showEvent(event)

    # ---------- маска: вырезаем внутренность, оставляем только рамку ----------
    def _apply_mask(self) -> None:
        outer = self.rect()
        inner = outer.adjusted(M, M, -M, -M)
        self.setMask(QRegion(outer).subtracted(QRegion(inner)))

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._apply_mask()
        super().resizeEvent(event)

    # ---------- определение зоны под курсором ----------
    def _zone_at(self, pos: QPoint) -> str:
        r = self.rect()
        left = pos.x() < CORNER
        right = pos.x() > r.width() - CORNER
        top = pos.y() < CORNER
        bottom = pos.y() > r.height() - CORNER
        if top and left:
            return "nw"
        if top and right:
            return "ne"
        if bottom and left:
            return "sw"
        if bottom and right:
            return "se"
        return "move"

    # ---------- мышь: перетаскивание и ресайз ----------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.RightButton:
            self.menu_requested.emit(event.globalPosition().toPoint())
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._zone = self._zone_at(event.position().toPoint())
        self._press_global = event.globalPosition().toPoint()
        self._press_geom = QRect(self.geometry())

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        # без зажатой кнопки — просто показываем подходящий курсор
        if self._press_global is None:
            self.setCursor(
                _CURSORS.get(
                    self._zone_at(event.position().toPoint()),
                    Qt.CursorShape.SizeAllCursor,
                )
            )
            return

        delta = event.globalPosition().toPoint() - self._press_global
        g = QRect(self._press_geom)

        if self._zone == "move":
            g.moveTopLeft(self._press_geom.topLeft() + delta)
        else:
            if "n" in self._zone:
                g.setTop(g.top() + delta.y())
            if "s" in self._zone:
                g.setBottom(g.bottom() + delta.y())
            if "w" in self._zone:
                g.setLeft(g.left() + delta.x())
            if "e" in self._zone:
                g.setRight(g.right() + delta.x())
            self._clamp_min(g)

        self.setGeometry(g)
        self.region_changed.emit(*self.current_region())

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._zone = None
        self._press_global = None
        self._press_geom = None

    def _clamp_min(self, g: QRect) -> None:
        """Не даём области стать меньше MIN_SIDE, двигая нужную грань."""
        min_w = MIN_SIDE + 2 * M
        min_h = MIN_SIDE + 2 * M
        if g.width() < min_w:
            if "w" in (self._zone or ""):
                g.setLeft(g.right() - min_w + 1)
            else:
                g.setRight(g.left() + min_w - 1)
        if g.height() < min_h:
            if "n" in (self._zone or ""):
                g.setTop(g.bottom() - min_h + 1)
            else:
                g.setBottom(g.top() + min_h - 1)

    # ---------- отрисовка рамки ----------
    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        color = QColor(PALETTE["accent"] if self._active else PALETTE["subtext"])
        # inner = ровно захватываемая область; НА ней ничего не рисуем (она вырезана
        # маской и попадает в OCR-скриншот). Всё — в поле снаружи (margin).
        inner = self.rect().adjusted(M, M, -M, -M)
        border = inner.adjusted(-3, -3, 3, 3)  # рамка на 3px снаружи области

        # мягкое «гало» снаружи рамки — «дышит» в активном режиме (пульс сканера)
        pulse = (math.sin(self._phase * 0.12) + 1) / 2 if self._active else 0.0
        glow = QColor(color)
        glow.setAlpha(int(45 + 45 * pulse))
        p.setPen(QPen(glow, 4))
        p.drawRoundedRect(inner.adjusted(-6, -6, 6, 6), 9, 9)

        # сама рамка: в активном режиме — «бегущий пунктир» (marching ants)
        pen = QPen(color, 2)
        if self._active:
            pen.setStyle(Qt.PenStyle.CustomDashLine)
            pen.setDashPattern([5, 4])
            pen.setDashOffset(-self._phase % 100)  # смещение → эффект движения
        p.setPen(pen)
        p.drawRoundedRect(border, 7, 7)

        # уголки-засечки (жирнее — подсказывают, что за них можно тянуть)
        p.setPen(QPen(color, 4))
        c = 16
        for cx, cy, dx, dy in (
            (border.left(), border.top(), 1, 1),
            (border.right(), border.top(), -1, 1),
            (border.left(), border.bottom(), 1, -1),
            (border.right(), border.bottom(), -1, -1),
        ):
            p.drawLine(cx, cy, cx + dx * c, cy)
            p.drawLine(cx, cy, cx, cy + dy * c)
        p.end()


class SubtitleOverlay(QWidget):
    """Плавающая плашка-субтитр с переводом — висит у выбранной области.

    Заменяет большое окно в режиме чтения: на экране остаётся только рамка и
    компактный перевод под ней. Плашку можно перетащить в любое место мышью.
    Кнопки в шапке: показать полное окно и остановить чтение.

    Режим «поверх текста» (``set_cover(True)``): плашка ложится ровно на область
    и закрывает оригинал переводом — как русификатор. Она прозрачна для мыши
    (клики проходят в игру) и исключена из захвата экрана, поэтому OCR под ней
    по-прежнему видит исходный текст. Кегль подбирается под размер области.
    """

    restore_requested = pyqtSignal()
    stop_requested = pyqtSignal()

    MIN_W = 240  # плашка не уже этого, даже если область крошечная
    MAX_W = 760  # и не шире, чтобы строки не растягивались на пол-экрана
    GAP = 10  # зазор между областью и плашкой, px

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._drag_offset: QPoint | None = None
        self._user_moved = False  # после ручного переноса не прыгаем за областью
        self._font_scale = 100
        self._cover = False  # True — плашка лежит поверх самой области
        self._cover_px = 18
        self._region: tuple[int, int, int, int] | None = None

        card = QFrame(self)
        card.setObjectName("subcard")
        self._card = card

        self._dot = QLabel()
        self._dot.setObjectName("subdot")
        self._dot.setFixedSize(8, 8)
        self._dot_effect = QGraphicsOpacityEffect(self._dot)
        self._dot.setGraphicsEffect(self._dot_effect)
        self.hint = QLabel("перевод")
        self.hint.setObjectName("subhint")
        restore_btn = QPushButton()
        restore_btn.setObjectName("subbtn")
        restore_btn.setIconSize(QSize(13, 13))
        restore_btn.setToolTip("Показать полное окно")
        restore_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        restore_btn.clicked.connect(self.restore_requested)
        stop_btn = QPushButton()
        stop_btn.setObjectName("subbtn")
        stop_btn.setIconSize(QSize(13, 13))
        stop_btn.setToolTip("Остановить чтение")
        stop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        stop_btn.clicked.connect(self.stop_requested)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(6)
        head.addWidget(self._dot)
        head.addWidget(self.hint)
        head.addStretch(1)
        head.addWidget(restore_btn)
        head.addWidget(stop_btn)

        self.text = QLabel("…")
        self.text.setObjectName("subtext")
        self.text.setWordWrap(True)
        self.text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 10, 12, 12)
        lay.setSpacing(6)
        lay.addLayout(head)
        lay.addWidget(self.text)
        lay.addStretch(1)
        self._head_widgets = (self._dot, self.hint, restore_btn, stop_btn)
        self._lay = lay

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)
        self._icon_btns = ((restore_btn, "window"), (stop_btn, "close"))

        # пульс «live»-точки, пока идёт распознавание/перевод
        self._pulse_up = True
        self._busy_timer = QTimer(self)
        self._busy_timer.setInterval(60)
        self._busy_timer.timeout.connect(self._pulse_dot)
        # плавное появление плашки
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(160)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.apply_style()

    # ---------- индикатор занятости ----------
    def set_busy(self, busy: bool) -> None:
        """Включает пульсацию точки (идёт OCR/перевод) или гасит её."""
        if busy:
            self.hint.setText("распознаю…")
            if not self._busy_timer.isActive():
                self._pulse_up = True
                self._busy_timer.start()
        else:
            self._busy_timer.stop()
            self._dot_effect.setOpacity(1.0)
            self.hint.setText("перевод")

    def _pulse_dot(self) -> None:
        val = self._dot_effect.opacity()
        val += 0.08 if self._pulse_up else -0.08
        if val >= 1.0:
            val, self._pulse_up = 1.0, False
        elif val <= 0.25:
            val, self._pulse_up = 0.25, True
        self._dot_effect.setOpacity(val)

    def showEvent(self, event) -> None:  # noqa: N802
        exclude_from_capture(self)
        self.setWindowOpacity(0.0)
        self._fade.stop()
        self._fade.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._busy_timer.stop()
        super().hideEvent(event)

    def _rgba(self, hex_color: str, a: float) -> str:
        r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
        return f"rgba({r}, {g}, {b}, {a})"

    def apply_style(self) -> None:
        """Пересобирает оформление под текущую тему (PALETTE) и масштаб шрифта."""
        p = PALETTE
        for btn, icon_name in self._icon_btns:
            btn.setIcon(make_icon(icon_name, p["subtext"]))
        big = self._cover_px if self._cover else round(16 * self._font_scale / 100.0)
        card_bg = self._rgba(p["surface2"], 0.97 if self._cover else 0.95)
        radius = 8 if self._cover else 13
        self.setStyleSheet(f"""
            QFrame#subcard {{
                background: {card_bg};
                border: 1px solid {self._rgba(p["accent"], 0.55)};
                border-radius: {radius}px;
            }}
            QLabel#subtext {{
                color: {p["text"]}; font-size: {big}px; font-weight: 600;
                font-family: 'Segoe UI', sans-serif;
            }}
            QLabel#subhint {{
                color: {p["subtext"]}; font-size: 11px; letter-spacing: 0.3px;
            }}
            QLabel#subdot {{
                background: {p["accent"]}; border-radius: 4px;
            }}
            QPushButton#subbtn {{
                background: transparent; color: {p["subtext"]};
                border: none; border-radius: 6px; padding: 0 6px;
                font-size: 13px; min-width: 20px; min-height: 20px;
            }}
            QPushButton#subbtn:hover {{
                background: {self._rgba(p["accent"], 0.20)}; color: {p["text"]};
            }}
        """)

    def set_font_scale(self, scale: int) -> None:
        self._font_scale = scale
        self.apply_style()
        if self._cover:
            self._fit_cover()
        else:
            self._fit_height()

    # ---------- режим «поверх текста» ----------
    @property
    def cover(self) -> bool:
        return self._cover

    def set_cover(self, on: bool) -> None:
        """Переключает плашку между «под областью» и «поверх текста»."""
        if on == self._cover:
            return
        self._cover = on
        for w in self._head_widgets:
            w.setVisible(not on)
        if on:
            self._lay.setContentsMargins(10, 6, 10, 6)
        else:
            self._lay.setContentsMargins(14, 10, 12, 12)
            self.setMinimumSize(0, 0)
            self.setMaximumSize(16777215, 16777215)
            self._card.setVisible(True)
        visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, on)
        if visible:
            self.show()  # смена флагов пересоздаёт окно — показать заново
        self.apply_style()
        if self._region:
            self.place_by(*self._region)

    def _fit_cover(self) -> None:
        """Подбирает кегль так, чтобы перевод целиком влез в область."""
        from .lens import fit_text

        text = self.text.text()
        m = self._lay.contentsMargins()
        w = self.width() - m.left() - m.right() - 4
        h = self.height() - m.top() - m.bottom() - 2
        start = max(12, min(44, round(24 * self._font_scale / 100.0)))
        px, _ = fit_text(text, w, h, start, QFont.Weight.DemiBold)
        if px != self._cover_px:
            self._cover_px = px
            self.apply_style()

    # ---------- содержимое и размещение ----------
    def set_text(self, text: str) -> None:
        """Обновляет текст перевода, высота подгоняется под содержимое."""
        if self._cover:
            # нет текста (окно диалога скрыто) — не закрываем картинку игры
            self._card.setVisible(bool(text.strip()))
            self.text.setText(text)
            self._fit_cover()
            return
        self._card.setVisible(True)
        new = text or "…"
        if new != self.text.text() and self.isVisible():
            anim.fade_in(self.text, 200, 0.15)  # новая реплика проявляется
        self.text.setText(new)
        self._fit_height()

    def _fit_height(self) -> None:
        """Высота строго по тексту. adjustSize у переносимого QLabel считает высоту
        для «примерной» ширины — плашка выходила с пустым местом снизу."""
        lay = self.layout()
        if lay is not None and lay.hasHeightForWidth():
            self.setFixedHeight(lay.totalHeightForWidth(self.width()))
        else:
            self.adjustSize()

    def place_by(self, left: int, top: int, width: int, height: int) -> None:
        """Ставит плашку под областью (или над ней, если снизу нет места).

        Сбрасывает флаг ручного переноса — при смене/выборе области субтитр
        снова «прилипает» к рамке.
        """
        self._user_moved = False
        self._region = (left, top, width, height)
        if self._cover:
            self.setFixedSize(width, height)
            self.move(left, top)
            self._fit_cover()
            return
        w = max(self.MIN_W, min(self.MAX_W, width))
        self.setFixedWidth(w)
        self._fit_height()
        self._reposition(left, top, width, height)

    def _reposition(self, left: int, top: int, width: int, height: int) -> None:
        screen = (
            QGuiApplication.screenAt(QPoint(left + width // 2, top + height // 2))
            or QGuiApplication.primaryScreen()
        )
        area = screen.availableGeometry()
        h = self.height()
        x = left + (width - self.width()) // 2
        x = max(area.left() + 4, min(x, area.right() - self.width() - 4))
        y = top + height + self.GAP
        if y + h > area.bottom():  # снизу не помещается — вешаем сверху
            y = top - self.GAP - h
        y = max(area.top() + 4, min(y, area.bottom() - h - 4))
        self.move(x, y)

    # ---------- перетаскивание всей плашки ----------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and not self._cover:
            self._drag_offset = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_offset is not None:
            self._user_moved = True
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_offset = None
