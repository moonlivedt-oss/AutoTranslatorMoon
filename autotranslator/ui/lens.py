"""Экранный перевод «как Google Lens»: перевод рисуется прямо поверх текста.

Окно-оверлей закрывает экран (или выделенный кусок) застывшим кадром, на
котором каждый найденный блок текста закрыт «матовой» подложкой — размытым
куском того же кадра, тонированным цветом фона, — и подписан переводом. Так
перевод выглядит частью картинки, а не заплаткой, и весь экран читается разом.

Управление:
- панель сверху: оригинал ⇄ перевод, копировать всё, язык перевода, монитор,
  открыть в окне, закрыть;
- Esc / ПКМ / клик мимо текста / та же горячая клавиша — закрыть;
- зажать Пробел — подсмотреть оригинал целиком;
- навести на блок — над ним появится карточка с оригиналом;
- клик по блоку — скопировать его перевод; Ctrl+C — скопировать всё.
"""

from __future__ import annotations

import math
import time

from PyQt6.QtCore import (
    QEasingCurve,
    QParallelAnimationGroup,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMenu,
    QToolButton,
    QWidget,
)

from ..core.langs import LANGUAGES
from ..core.langs import name as lang_name
from ..core.textutil import norm
from ..win.winutil import exclude_from_capture, force_foreground
from .icons import make_icon
from .theme import PALETTE

FONT_FAMILY = "Segoe UI"
MIN_PX = 11
PAD_X, PAD_Y = 5, 3
FADE_S = 0.18  # проявление перевода одного блока
STAGGER_S = 0.022  # задержка между блоками (сверху вниз), не больше STAGGER_MAX_S
STAGGER_MAX_S = 0.35
TINT_ALPHA = 205  # насколько подложка тонируется цветом фона (из 255)
HOVER_MS = 280  # через сколько наведения показать карточку с оригиналом
OPEN_S = 0.16  # оверлей проявляется при открытии
CLOSE_MS = 130  # и гаснет при закрытии
DIM_S = 0.25  # затемнение кадра во время распознавания нарастает/уходит столько
ORIG_S = 0.14  # переход «перевод ⇄ оригинал»
HOVER_FADE_S = 0.12  # рамка под мышью проявляется
PEEK_S = 0.16  # карточка с оригиналом проявляется и всплывает
FLASH_S = 0.55  # «скопировано»: кольцо расходится и гаснет
# буквы с «хвостом» вниз: у строки без них рамка OCR — только высота заглавных
_DESCENDERS = set("gjpqyQ,;руфдцщДЦЩ()[]{}")

_ALIGN = {
    "left": Qt.AlignmentFlag.AlignLeft,
    "center": Qt.AlignmentFlag.AlignHCenter,
    "right": Qt.AlignmentFlag.AlignRight,
}


def _ease(t: float) -> float:
    """Плавное замедление к концу (OutCubic), t в 0..1."""
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def _lum(c: QColor) -> float:
    return 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()


def fit_text(
    text: str,
    width: int,
    height: int,
    start_px: int,
    weight: QFont.Weight = QFont.Weight.Medium,
) -> tuple[int, int]:
    """Наибольший кегль, при котором текст с переносами влезает в width×height.

    Возвращает (кегль, нужная высота). Если не влезает даже минимальным —
    минимальный кегль и высота, которую придётся занять (блок растянется вниз).
    """
    flags = int(Qt.TextFlag.TextWordWrap)
    need = height
    for px in range(max(start_px, MIN_PX), MIN_PX - 1, -1):
        f = QFont(FONT_FAMILY)
        f.setPixelSize(px)
        f.setWeight(weight)
        br = QFontMetrics(f).boundingRect(
            QRect(0, 0, max(1, width), 100000), flags, text
        )
        need = br.height()
        if need <= height and br.width() <= width:
            return px, need
    return MIN_PX, need


def _frosted(image: QImage, rect: QRect, line_h: float, tint: QColor) -> QImage:
    """«Матовое стекло»: кусок кадра, размытый до неузнаваемости букв, + тонировка.

    Размытие — дешёвое: уменьшить так, чтобы строка текста стала ~2 px, и
    растянуть обратно со сглаживанием (в два шага, чтобы не было «ступенек»).
    """
    crop = image.copy(rect)
    w, h = crop.width(), crop.height()
    if w < 2 or h < 2:
        return crop
    k = max(6.0, line_h * 1.2)  # строка текста ужимается меньше чем в пиксель
    smooth = Qt.TransformationMode.SmoothTransformation
    ignore = Qt.AspectRatioMode.IgnoreAspectRatio
    small = crop.scaled(max(1, round(w / k)), max(1, round(h / k)), ignore, smooth)
    mid = small.scaled(max(1, w // 3), max(1, h // 3), ignore, smooth)
    out = mid.scaled(w, h, ignore, smooth).convertToFormat(
        QImage.Format.Format_ARGB32_Premultiplied
    )
    p = QPainter(out)
    t = QColor(tint)
    t.setAlpha(TINT_ALPHA)
    p.fillRect(out.rect(), t)
    p.end()
    return out


def _avg_color(image: QImage) -> QColor:
    if image.isNull() or image.width() < 1:
        return QColor(30, 30, 46)
    one = image.scaled(
        1,
        1,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    return QColor(one.pixel(0, 0))


class TextItem:
    """Блок текста на экране и его перевод: где лежит, каким кеглем, на какой подложке."""

    __slots__ = (
        "src",
        "dst",
        "rect",
        "box",
        "px",
        "bg",
        "fg",
        "shade",
        "single",
        "align",
        "line_h",
        "em",
        "backdrop",
        "shown_at",
    )

    def __init__(
        self, src: str, rect: QRectF, line_h: float, bg, align: str = "left"
    ) -> None:
        self.src = src
        self.dst = ""
        self.rect = rect  # где был оригинал (логические координаты окна)
        self.box = QRectF(rect)  # плашка перевода (может вырасти вниз/вправо)
        self.line_h = line_h
        # рамка OCR — по глифам: с хвостами вниз это ~0.95 кегля, без них ~0.72
        tall = any(ch in _DESCENDERS for ch in src)
        self.em = line_h / (0.95 if tall else 0.72)
        self.px = max(MIN_PX, min(48, round(self.em)))
        self.single = rect.height() < 1.6 * line_h  # однострочная подпись/кнопка
        self.align = align if align in _ALIGN else "left"
        self.bg = QColor(*bg)
        self.fg = QColor("#f4f4f6")
        self.shade = QColor(0, 0, 0, 140)  # тень под буквами — читаемость на любом фоне
        self.backdrop: QImage | None = None
        self.shown_at = 0.0  # когда начать проявлять перевод (0 — ещё нет перевода)
        self._pick_colors(self.bg)

    def _pick_colors(self, under: QColor) -> None:
        if _lum(under) > 150:
            self.fg = QColor("#141414")
            self.shade = QColor(255, 255, 255, 110)
        else:
            self.fg = QColor("#f4f4f6")
            self.shade = QColor(0, 0, 0, 150)

    @property
    def same(self) -> bool:
        """Перевод совпал с оригиналом (имя, ссылка, термин) — не закрываем его."""
        return bool(self.dst) and norm(self.dst) == norm(self.src)

    @property
    def visible(self) -> bool:
        return bool(self.dst) and not self.same


def layout_item(
    item: TextItem,
    width: int,
    height: int,
    image: QImage,
    dpr: float,
    *,
    span: tuple[float, float] | None = None,
) -> None:
    """Раскладывает перевод блока: кегль, размер плашки, матовая подложка.

    span — (левая, правая) граница, за которую короткой подписи расти нельзя:
    там соседняя кнопка. По умолчанию — края оверлея.
    """
    # рамка OCR — по самим буквам, а строке шрифта нужно ~на треть больше
    # высоты: без запаса кегль подбирался мельче оригинала
    extra = max(item.line_h * 0.15, (item.em * 1.3 - item.line_h) / 2)
    r = item.rect.adjusted(-PAD_X, -PAD_Y - extra, PAD_X, PAD_Y + extra)
    inner_w = int(r.width()) - 2 * PAD_X
    inner_h = int(r.height()) - 2 * PAD_Y
    # одна строка ровно посередине экрана — почти наверняка выровнена по центру
    # (реплика в окне диалога): по одной строке иначе выравнивание не узнать
    if (
        item.single
        and item.align == "left"
        and abs(item.rect.center().x() - width / 2) < 0.02 * width
    ):
        item.align = "center"
    if item.single and "\n" not in item.dst:
        # короткая подпись («Save» -> «Сохранить»): лучше расшириться,
        # чем мельчить шрифт до нечитаемого
        f = QFont(FONT_FAMILY)
        f.setPixelSize(item.px)
        f.setWeight(QFont.Weight.Medium)
        natural = QFontMetrics(f).horizontalAdvance(item.dst) + 2
        if inner_w < natural <= max(inner_w * 2.5, inner_w + 260):
            grow = natural - inner_w
            # короткая подпись (1–2 слова) — почти всегда кнопка или пункт меню
            # с текстом по центру: растём в обе стороны, а не за край кнопки
            if item.align == "left" and len(item.src.split()) <= 2:
                item.align = "center"
            if item.align == "center":
                r.adjust(-grow / 2, 0, grow / 2, 0)
            elif item.align == "right":
                r.adjust(-grow, 0, 0, 0)
            else:
                r.setWidth(r.width() + grow)
            # не вылезать за края оверлея и в соседей: сначала сдвигаемся в
            # сторону, где есть место, потом — обрезаем (шрифт станет мельче)
            lo, hi = span if span is not None else (0.0, width - 2.0)
            if r.right() > hi:
                r.moveRight(hi)
            if r.left() < lo:
                r.moveLeft(lo)
            r.setRight(min(r.right(), hi))
            if r.width() < item.rect.width() + 2 * PAD_X:  # не уже своей рамки
                r = item.rect.adjusted(-PAD_X, r.top() - item.rect.top(), PAD_X, 0)
                r.setHeight(inner_h + 2 * PAD_Y)
            inner_w = int(r.width()) - 2 * PAD_X
    px, need = fit_text(item.dst, inner_w, inner_h, item.px)
    item.px = px
    if need > inner_h:  # не влезло даже мелко — растягиваем плашку вниз
        r.setHeight(min(need + 2 * PAD_Y, height - r.top()))
    item.box = r
    make_backdrop(item, image, dpr)


def _grew(item: TextItem) -> bool:
    return item.box.width() > item.rect.width() + 2 * PAD_X + 1


def _same_row(a: TextItem, b: TextItem) -> bool:
    return not (b.rect.bottom() < a.rect.top() or b.rect.top() > a.rect.bottom())


def _span(a: TextItem, items: list[TextItem], width: int) -> tuple[float, float]:
    """Сколько места у подписи по горизонтали: до середины промежутка с соседями
    по строке (так соседи делят зазор поровну и не наезжают друг на друга)."""
    lo, hi = 0.0, width - 2.0
    for b in items:
        if b is a or not _same_row(a, b):
            continue
        if b.rect.center().x() < a.rect.center().x():
            lo = max(lo, (b.rect.right() + a.rect.left()) / 2 + 2)
        else:
            hi = min(hi, (a.rect.right() + b.rect.left()) / 2 - 2)
    return lo, hi


def settle_overlaps(
    items: list[TextItem], width: int, height: int, image: QImage, dpr: float
) -> None:
    """Приводит в порядок короткие подписи после раскладки.

    - расширенная под длинный перевод подпись («Save» -> «Сохранить») не должна
      наезжать на соседнюю («Сохраниті Загрузить») — растёт только в свободное
      место, остальное добирается мельче шрифтом;
    - кнопки одного ряда одного размера получают одинаковый кегль: иначе
      «Сохранить» крупнее «Загрузить» только потому, что ему досталось больше места.
    """
    shown = [it for it in items if it.visible]
    for a in shown:
        if _grew(a) and any(b is not a and a.box.intersects(b.box) for b in shown):
            layout_item(a, width, height, image, dpr, span=_span(a, shown, width))
    # шрифт подбирается заново и может стать ещё мельче — выравниваем, пока не сойдётся
    for _ in range(3):
        changed = False
        singles = [it for it in shown if it.single and "\n" not in it.dst]
        for a in singles:
            row = [
                b
                for b in singles
                if _same_row(a, b) and abs(b.line_h - a.line_h) <= 0.2 * a.line_h
            ]
            if len(row) < 2:
                continue
            smallest = min(b.px for b in row)
            for b in row:
                if b.px > smallest:
                    b.px = smallest
                    changed = True
                    layout_item(
                        b, width, height, image, dpr, span=_span(b, shown, width)
                    )
        if not changed:
            break


def make_backdrop(item: TextItem, image: QImage, dpr: float) -> None:
    d = dpr
    b = item.box
    phys = QRect(
        round(b.left() * d),
        round(b.top() * d),
        round(b.width() * d),
        round(b.height() * d),
    ).intersected(image.rect())
    if phys.width() < 2 or phys.height() < 2:
        item.backdrop = None
        return
    img = _frosted(image, phys, item.line_h * d, item.bg)
    img.setDevicePixelRatio(d)
    item.backdrop = img
    item._pick_colors(_avg_color(img))  # цвет букв — по реальной подложке


def paint_item(p: QPainter, it: TextItem, now: float, fade: float = 1.0) -> bool:
    """Рисует перевод блока (подложка, тень, текст). False — ещё не пора."""
    if not it.shown_at or now < it.shown_at:
        return False
    t = min(1.0, (now - it.shown_at) / FADE_S)
    ease = 1 - (1 - t) ** 3  # плавное замедление в конце
    p.save()
    p.setOpacity(ease * fade)
    path = QPainterPath()
    path.addRoundedRect(it.box, 5, 5)
    if it.backdrop is not None:
        p.save()
        p.setClipPath(path)
        p.drawImage(it.box.topLeft(), it.backdrop)
        p.restore()
    else:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(it.bg)
        p.drawPath(path)
    f = QFont(FONT_FAMILY)
    f.setPixelSize(it.px)
    f.setWeight(QFont.Weight.Medium)
    p.setFont(f)
    # однострочная подпись — по центру плашки по высоте: кнопки одного ряда
    # стоят ровно, даже если у одной есть буквы с «хвостом» вниз, а у другой нет
    vertical = (
        Qt.AlignmentFlag.AlignVCenter
        if it.single and "\n" not in it.dst
        else Qt.AlignmentFlag.AlignTop
    )
    flags = int(Qt.TextFlag.TextWordWrap) | int(_ALIGN[it.align]) | int(vertical)
    inner = it.box.adjusted(PAD_X, PAD_Y, -PAD_X, -PAD_Y)
    # лёгкий подъём при проявлении: текст «всплывает» на 3 px
    lift = (1 - ease) * 3
    p.setPen(it.shade)
    p.drawText(inner.translated(0, 1 + lift), flags, it.dst)
    p.setPen(it.fg)
    p.drawText(inner.translated(0, lift), flags, it.dst)
    p.restore()
    return True


class _Bar(QFrame):
    """Панель управления оверлеем. Клик по ней не должен закрывать оверлей."""

    hovered = pyqtSignal(bool)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        event.accept()

    def enterEvent(self, event) -> None:  # noqa: N802
        self.hovered.emit(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.hovered.emit(False)
        super().leaveEvent(event)


class LensOverlay(QWidget):
    closed = pyqtSignal()
    copied = pyqtSignal(str)
    retranslate = pyqtSignal(str)  # код языка — перевести те же блоки заново
    to_window = pyqtSignal()  # открыть результат в главном окне
    screen_requested = pyqtSignal(int)  # номер монитора, -1 — все экраны
    live_requested = pyqtSignal()  # перейти в живой перевод этой же области

    def __init__(
        self,
        rect: QRect,
        image: QImage,
        *,
        full: bool,
        target: str = "ru",
        screens: list[str] | None = None,
    ) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setGeometry(rect)
        self._image = image
        self._full = full
        self._target = target
        self._items: list[TextItem] = []
        self._dpr = image.devicePixelRatio() or 1.0
        self._state = "scan"  # scan -> blocks -> done | empty | error
        self._msg = "Распознаю текст"
        self._space = False  # зажат Пробел — показать оригинал
        self._hover = -1
        self._peek = -1  # блок, над которым показана карточка с оригиналом
        self._flash = -1
        self._flash_until = 0.0
        self._flash_at = 0.0
        self._opened = time.monotonic()
        self._scan_end = 0.0  # когда закончилось распознавание (затемнение уходит)
        self._orig_t = 0.0  # 0 — видно перевод, 1 — оригинал (плавно между ними)
        self._hover_at = 0.0
        self._peek_at = 0.0
        self._closing = False
        self._pill_until = 0.0
        self._menu_open = False
        self._bar_fits = True
        self._phase = 0.0
        self._anim = QTimer(self)
        self._anim.setInterval(16)
        self._anim.timeout.connect(self._tick)
        self._anim.start()
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(HOVER_MS)
        self._hover_timer.timeout.connect(self._show_peek)
        self._build_bar(screens or [])

    # ---------- панель управления ----------
    def _build_bar(self, screens: list[str]) -> None:
        p = PALETTE
        bar = _Bar(self)
        bar.setObjectName("lensbar")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 4, 4, 4)
        lay.setSpacing(2)
        self._status = QLabel(bar)
        self._status.setObjectName("lensstatus")
        self._status.setToolTip(
            "Пробел (зажать) — оригинал | клик по блоку — копировать его\n"
            "Ctrl+C — копировать всё | Esc / ПКМ — закрыть"
        )
        lay.addWidget(self._status)
        lay.addSpacing(6)

        def tool(icon: str, text: str, tip: str) -> QToolButton:
            b = QToolButton(bar)
            b.setIcon(make_icon(icon, p["text"]))
            b.setIconSize(QSize(16, 16))
            b.setText(text)
            b.setToolTip(tip)
            b.setToolButtonStyle(
                Qt.ToolButtonStyle.ToolButtonTextBesideIcon
                if text
                else Qt.ToolButtonStyle.ToolButtonIconOnly
            )
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            lay.addWidget(b)
            return b

        self._orig_btn = tool(
            "eye", "Оригинал", "Показать оригинал (или зажать Пробел)"
        )
        self._orig_btn.setCheckable(True)
        self._orig_btn.toggled.connect(lambda _on: self._kick())
        self._copy_btn = tool("copy", "", "Копировать весь перевод (Ctrl+C)")
        self._copy_btn.clicked.connect(self.copy_all)

        self._lang_btn = tool("swap", lang_name(self._target), "Язык перевода")
        menu = QMenu(self._lang_btn)
        for code in LANGUAGES:
            act = menu.addAction(lang_name(code))
            act.triggered.connect(lambda _=False, c=code: self._pick_lang(c))
        self._lang_btn.setMenu(menu)
        self._lang_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._watch_menu(menu)

        if len(screens) > 1:
            scr_btn = tool("screen", "", "Перевести другой монитор")
            smenu = QMenu(scr_btn)
            for i, label in enumerate(screens):
                if label:
                    act = smenu.addAction(label)
                    act.triggered.connect(lambda _=False, n=i: self._ask_screen(n))
            smenu.addSeparator()
            smenu.addAction("Все экраны").triggered.connect(
                lambda: self._ask_screen(-1)
            )
            scr_btn.setMenu(smenu)
            scr_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            self._watch_menu(smenu)

        live_btn = tool(
            "live",
            "Живой",
            "Живой перевод: перевод останется поверх игры и будет\n"
            "обновляться сам, когда вы листаете реплики",
        )
        live_btn.clicked.connect(self._ask_live)
        win_btn = tool("window", "", "Открыть оригинал и перевод в окне")
        win_btn.clicked.connect(self._ask_window)
        close_btn = tool("close", "", "Закрыть (Esc)")
        close_btn.setObjectName("lensclose")
        close_btn.clicked.connect(self.close)

        surface = QColor(p["surface2"])
        bar.setStyleSheet(
            f"""
            QFrame#lensbar {{
                background: rgba({surface.red()}, {surface.green()}, {surface.blue()}, 238);
                border: 1px solid {p["border"]}; border-radius: 19px;
            }}
            QLabel#lensstatus {{
                color: {p["text"]}; font-family: 'Segoe UI'; font-size: 13px;
                font-weight: 600; background: transparent;
            }}
            QToolButton {{
                color: {p["text"]}; font-family: 'Segoe UI'; font-size: 12px;
                font-weight: 600; background: transparent; border: 1px solid transparent;
                border-radius: 14px; padding: 4px 8px; min-height: 20px;
            }}
            QToolButton::menu-indicator {{ image: none; width: 0; }}
            QToolButton:hover {{
                background: rgba(255, 255, 255, 18); border-color: {p["accent"]};
            }}
            QToolButton:checked {{
                color: {p["accent_text"]}; background: {p["accent"]};
                border-color: {p["accent"]};
            }}
            QToolButton#lensclose:hover {{ background: {p["error"]}; border-color: {p["error"]}; }}
            QToolTip {{
                background: {p["surface2"]}; color: {p["text"]};
                border: 1px solid {p["accent"]}; border-radius: 6px; padding: 5px 7px;
            }}
            """
        )
        self._bar = bar
        self._bar_fx = QGraphicsOpacityEffect(bar)
        self._bar_fx.setOpacity(1.0)
        bar.setGraphicsEffect(self._bar_fx)
        bar.hovered.connect(lambda on: self._bar_fx.setOpacity(1.0 if on else 0.8))
        self._set_msg(self._msg)

    def _watch_menu(self, menu: QMenu) -> None:
        # пока открыто меню, оверлей формально теряет активность — не закрываться
        menu.aboutToShow.connect(lambda: setattr(self, "_menu_open", True))
        menu.aboutToHide.connect(
            lambda: QTimer.singleShot(300, lambda: setattr(self, "_menu_open", False))
        )

    def _place_bar(self) -> None:
        bar = self._bar
        bar.adjustSize()
        hint = bar.sizeHint()
        fits = self.width() >= hint.width() + 16 and self.height() >= hint.height() + 60
        self._bar_fits = fits
        bar.setVisible(fits)
        if fits:
            y = 12 if self._full else 6
            bar.move((self.width() - hint.width()) // 2, y)
            bar.raise_()

    def _set_msg(self, text: str) -> None:
        self._msg = text
        dots = (
            "." * (int(self._phase / 20) % 4)
            if self._state in ("scan", "blocks")
            else ""
        )
        self._status.setText(text + dots)
        if self._bar_fits:
            self._place_bar()

    def _pick_lang(self, code: str) -> None:
        if not self._items or self._state == "scan":
            return
        self._target = code
        self._lang_btn.setText(lang_name(code))
        for it in self._items:
            it.dst = ""
            it.shown_at = 0.0
            it.backdrop = None
        self._peek = -1
        self._state = "blocks"
        self._set_msg(f"Перевожу на {lang_name(code)}")
        self._anim.start()
        self.retranslate.emit(code)

    def _ask_screen(self, n: int) -> None:
        self.screen_requested.emit(n)

    def _ask_live(self) -> None:
        self.live_requested.emit()
        self.close()

    def _ask_window(self) -> None:
        self.to_window.emit()
        self.close()

    # ---------- жизненный цикл ----------
    def showEvent(self, event) -> None:  # noqa: N802
        exclude_from_capture(self)  # чтобы оверлей не попал в чужие/наши скриншоты
        super().showEvent(event)
        self._place_bar()
        QTimer.singleShot(0, lambda: force_foreground(self))
        self.setFocus()
        self._intro_bar()

    def _intro_bar(self) -> None:
        """Панель съезжает сверху и проявляется."""
        if not self._bar_fits:
            return
        end = QPoint(self._bar.pos())
        group = QParallelAnimationGroup(self)
        move = QPropertyAnimation(self._bar, b"pos", group)
        move.setDuration(280)
        move.setStartValue(end - QPoint(0, 22))
        move.setEndValue(end)
        move.setEasingCurve(QEasingCurve.Type.OutBack)
        fade = QPropertyAnimation(self._bar_fx, b"opacity", group)
        fade.setDuration(220)
        fade.setStartValue(0.0)
        fade.setEndValue(0.8)
        group.addAnimation(move)
        group.addAnimation(fade)
        group.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    @property
    def closing(self) -> bool:
        """Оверлей уже гаснет — для окна он всё равно что закрыт."""
        return self._closing

    def close(self) -> bool:  # noqa: D401 — то же, что QWidget.close, но с угасанием
        if self._closing:
            return False
        self._closing = True
        self._hover_timer.stop()
        fade = QPropertyAnimation(self, b"windowOpacity", self)
        fade.setDuration(CLOSE_MS)
        fade.setStartValue(self.windowOpacity())
        fade.setEndValue(0.0)
        fade.setEasingCurve(QEasingCurve.Type.InCubic)
        fade.finished.connect(lambda: QWidget.close(self))
        fade.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        return True

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._place_bar()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._anim.stop()
        self.closed.emit()
        super().closeEvent(event)

    def changeEvent(self, event) -> None:  # noqa: N802
        # ушли в другое окно (Alt+Tab, клик в игру на другом мониторе) — закрываемся
        if (
            event.type() == event.Type.ActivationChange
            and not self.isActiveWindow()
            and not self._menu_open
            and time.monotonic() - self._opened > 0.6
        ):
            QTimer.singleShot(0, self._close_if_inactive)
        super().changeEvent(event)

    def _close_if_inactive(self) -> None:
        if not self._menu_open and not self.isActiveWindow():
            active = QApplication.activePopupWidget()
            if active is None:
                self.close()

    def _tick(self) -> None:
        self._phase += 1.0
        now = time.monotonic()
        if self._flash >= 0 and now > self._flash_until:
            self._flash = -1
        # «оригинал ⇄ перевод» — плавно к нужной стороне
        target = 1.0 if self._show_orig() else 0.0
        step = 0.016 / ORIG_S
        if self._orig_t < target:
            self._orig_t = min(target, self._orig_t + step)
        elif self._orig_t > target:
            self._orig_t = max(target, self._orig_t - step)
        if self._state in ("scan", "blocks"):
            if int(self._phase) % 20 == 0:
                self._set_msg(self._msg)
        elif not self._busy(now, target):
            self._anim.stop()
        self.update()

    def _busy(self, now: float, orig_target: float) -> bool:
        """Идёт ли ещё какая-нибудь анимация (иначе таймер можно остановить)."""
        return (
            self._flash >= 0
            or self._fading(now)
            or self._orig_t != orig_target
            or now < self._opened + OPEN_S
            or now < self._scan_end + DIM_S
            or (self._hover >= 0 and now < self._hover_at + HOVER_FADE_S)
            or (self._peek >= 0 and now < self._peek_at + PEEK_S)
        )

    def _kick(self) -> None:
        """Запустить кадры анимации (если таймер уже стоял)."""
        if not self._anim.isActive():
            self._anim.start()
        self.update()

    def _fading(self, now: float) -> bool:
        return any(it.shown_at and now < it.shown_at + FADE_S for it in self._items)

    # ---------- данные от воркера ----------
    def set_blocks(self, blocks) -> None:
        """blocks — ocr.Block в физических пикселях картинки."""
        d = self._dpr
        self._items = [
            TextItem(
                b.text,
                QRectF(b.left / d, b.top / d, b.width / d, b.height / d),
                b.line_h / d,
                b.bg,
                getattr(b, "align", "left"),
            )
            for b in blocks
        ]
        self._scan_end = time.monotonic()
        self._kick()
        if not self._items:
            self._state = "empty"
            self._set_msg("Текст не найден")
        else:
            self._state = "blocks"
            self._set_msg(f"Перевожу (блоков: {len(self._items)})")
        self.update()

    def set_partial(self, texts: list[str]) -> None:
        """Часть блоков уже переведена — показываем их, не дожидаясь остальных."""
        self._apply(texts)
        self._anim.start()

    def set_translations(self, texts: list[str]) -> None:
        self._apply(texts)
        self._state = "done"
        shown = sum(1 for it in self._items if it.visible)
        kept = len(self._items) - shown
        msg = f"Переведено блоков: {shown}"
        if kept:
            msg += f" | без изменений: {kept}"
        self._set_msg(msg)
        self._anim.start()  # доиграть проявление
        self._show_pill(3.0)

    def _apply(self, texts: list[str]) -> None:
        now = time.monotonic()
        fresh = []
        for item, dst in zip(self._items, texts, strict=False):
            dst = (dst or "").strip()
            if dst and not item.dst:
                item.dst = dst
                self._layout(item)
                fresh.append(item)
        if fresh:
            settle_overlaps(
                self._items, self.width(), self.height(), self._image, self._dpr
            )
        # проявляем сверху вниз — глаз ведёт, как при чтении
        fresh.sort(key=lambda it: (it.rect.top(), it.rect.left()))
        for n, it in enumerate(fresh):
            it.shown_at = now + min(STAGGER_MAX_S, n * STAGGER_S)
        self.update()

    def _show_pill(self, seconds: float) -> None:
        self._pill_until = time.monotonic() + seconds
        QTimer.singleShot(int(seconds * 1000) + 50, self.update)
        self.update()

    def set_error(self, msg: str) -> None:
        self._state = "error"
        self._set_msg(msg)
        self._status.setStyleSheet(f"color: {PALETTE['error']};")
        if self._state == "error" and self._scan_end == 0.0:
            self._scan_end = time.monotonic()
        self._shake_bar()
        self._kick()

    def _shake_bar(self) -> None:
        """Ошибка — панель коротко «мотает головой»."""
        if not self._bar_fits:
            return
        home = QPoint(self._bar.pos())
        shake = QPropertyAnimation(self._bar, b"pos", self)
        shake.setDuration(360)
        for t, dx in ((0.0, 0), (0.15, -8), (0.35, 7), (0.55, -5), (0.75, 3), (1.0, 0)):
            shake.setKeyValueAt(t, home + QPoint(dx, 0))
        shake.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def all_text(self) -> tuple[str, str]:
        src = "\n\n".join(i.src for i in self._items)
        dst = "\n\n".join(i.dst for i in self._items if i.dst)
        return src, dst

    def copy_all(self) -> None:
        _, dst = self.all_text()
        if not dst:
            return
        QApplication.clipboard().setText(dst)
        self.copied.emit(dst)
        self._set_msg("Весь перевод скопирован")
        self._show_pill(1.5)

    def _layout(self, item: TextItem) -> None:
        layout_item(item, self.width(), self.height(), self._image, self._dpr)

    # ---------- отрисовка ----------
    def _show_orig(self) -> bool:
        return self._space or self._orig_btn.isChecked()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        full = QRectF(self.rect())
        p.drawImage(full, self._image)
        accent = QColor(PALETTE["accent"])
        now = time.monotonic()
        # открытие: всё нарисованное поверх кадра проявляется (сам кадр = экран)
        p.setOpacity(_ease((now - self._opened) / OPEN_S))

        if self._state == "scan":
            self._paint_scan(p, full, accent, now)
        else:
            # затемнение распознавания не обрывается, а уходит
            dim = 1.0 - _ease((now - self._scan_end) / DIM_S)
            if dim > 0:
                p.fillRect(full, QColor(0, 0, 0, round(70 * dim)))
        shown = 1.0 - _ease(self._orig_t)  # переводы гаснут при «оригинале»
        if self._state != "scan" and shown > 0:
            pulse = (math.sin(self._phase * 0.12) + 1) / 2
            base = p.opacity()
            for i, it in enumerate(self._items):
                if it.visible:
                    p.setOpacity(base * shown)
                    self._paint_item(p, it, i, now, accent, base * shown)
                elif not it.dst and self._state == "blocks":
                    p.setOpacity(base * shown)
                    fill = QColor(accent)
                    fill.setAlpha(int(22 + 30 * pulse))
                    p.setPen(QPen(accent, 1.2))
                    p.setBrush(fill)
                    p.drawRoundedRect(it.rect.adjusted(-3, -2, 3, 2), 5, 5)
            p.setOpacity(base)

        if self._peek >= 0 and shown > 0:
            self._paint_peek(p, self._items[self._peek], accent, now, shown)
        if not self._full:  # одиночный кусок — подчёркиваем его границу
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(accent, 2))
            p.drawRect(full.adjusted(1, 1, -1, -1))
        if not self._bar_fits:
            self._draw_pill(p)
        p.end()

    def _paint_scan(
        self, p: QPainter, full: QRectF, accent: QColor, now: float
    ) -> None:
        # затемнение нарастает, а не падает сразу
        p.fillRect(
            full, QColor(0, 0, 0, round(70 * _ease((now - self._opened) / DIM_S)))
        )
        h = max(1.0, full.height())
        y = (self._phase * 7.0) % (h + 160) - 80
        grad = QLinearGradient(0, y - 80, 0, y + 6)
        c0 = QColor(accent)
        c0.setAlpha(0)
        c1 = QColor(accent)
        c1.setAlpha(90)
        grad.setColorAt(0.0, c0)
        grad.setColorAt(1.0, c1)
        p.fillRect(QRectF(0, y - 80, full.width(), 86), grad)
        p.setPen(QPen(accent, 2))
        p.drawLine(QPointF(0, y), QPointF(full.width(), y))

    def _paint_item(
        self,
        p: QPainter,
        it: TextItem,
        i: int,
        now: float,
        accent: QColor,
        fade: float = 1.0,
    ) -> None:
        if not paint_item(p, it, now, fade):
            return
        if i == self._flash:
            # «скопировано»: зелёная вспышка и кольцо, расходящееся от блока
            t = _ease((now - self._flash_at) / FLASH_S)
            ok = QColor(PALETTE["success"])
            tint = QColor(ok)
            tint.setAlpha(round(70 * (1 - t)))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(tint)
            p.drawRoundedRect(it.box, 5, 5)
            ring = QColor(ok)
            ring.setAlpha(round(255 * (1 - t)))
            grow = 10 * t
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(ring, 2.5 - 1.2 * t))
            p.drawRoundedRect(
                it.box.adjusted(-grow, -grow, grow, grow), 5 + grow, 5 + grow
            )
        elif i == self._hover:
            edge = QColor(accent)
            edge.setAlpha(round(255 * _ease((now - self._hover_at) / HOVER_FADE_S)))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(edge, 1.5))
            p.drawRoundedRect(it.box, 5, 5)

    def _paint_peek(
        self, p: QPainter, it: TextItem, accent: QColor, now: float, shown: float
    ) -> None:
        """Карточка с оригиналом над блоком (или под ним, если сверху нет места)."""
        t = _ease((now - self._peek_at) / PEEK_S)
        p.save()
        p.setOpacity(p.opacity() * t * shown)
        p.translate(0, 6 * (1 - t))  # всплывает на место
        f = QFont(FONT_FAMILY)
        f.setPixelSize(13)
        p.setFont(f)
        fm = QFontMetrics(f)
        head = QFont(FONT_FAMILY)
        head.setPixelSize(11)
        head.setWeight(QFont.Weight.DemiBold)
        max_w = int(min(480, self.width() - 16))
        text_w = max(120, min(max_w - 24, int(it.box.width()) + 80))
        br = fm.boundingRect(
            QRect(0, 0, text_w, 100000), int(Qt.TextFlag.TextWordWrap), it.src
        )
        w = min(max_w, br.width() + 24)
        h = br.height() + 34
        x = max(8.0, min(it.box.left(), self.width() - w - 8.0))
        y = it.box.top() - h - 6
        if y < 8:
            y = min(it.box.bottom() + 6, self.height() - h - 8)
        card = QRectF(x, y, w, h)
        surface = QColor(PALETTE["surface2"])
        surface.setAlpha(245)
        p.setPen(QPen(accent, 1))
        p.setBrush(surface)
        p.drawRoundedRect(card, 9, 9)
        p.setFont(head)
        p.setPen(QColor(PALETTE["subtext"]))
        p.drawText(
            card.adjusted(12, 7, -12, 0), int(Qt.AlignmentFlag.AlignTop), "ОРИГИНАЛ"
        )
        p.setFont(f)
        p.setPen(QColor(PALETTE["text"]))
        p.drawText(
            card.adjusted(12, 24, -12, -8),
            int(Qt.TextFlag.TextWordWrap) | int(Qt.AlignmentFlag.AlignTop),
            it.src,
        )
        p.restore()

    def _draw_pill(self, p: QPainter) -> None:
        """Плашка-подсказка — только когда панели не хватило места (маленький кусок)."""
        if self._show_orig():
            text = "Оригинал"
        elif self._state in ("scan", "blocks"):
            text = f"{self._msg}  |  Esc — закрыть"
        elif self._state == "done":
            if time.monotonic() > self._pill_until:
                return
            text = f"{self._msg}  |  Пробел — оригинал  |  Esc — закрыть"
        else:
            text = f"{self._msg}  |  Esc — закрыть"
        f = QFont(FONT_FAMILY)
        f.setPixelSize(12)
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        fm = QFontMetrics(f)
        w = min(self.width() - 8, fm.horizontalAdvance(text) + 24)
        h = fm.height() + 10
        if w < 80 or self.height() < h + 8:
            return
        x = (self.width() - w) / 2
        surface = QColor(PALETTE["surface2"])
        surface.setAlpha(232)
        border = QColor(
            PALETTE["error"] if self._state == "error" else PALETTE["accent"]
        )
        p.setPen(QPen(border, 1))
        p.setBrush(surface)
        p.drawRoundedRect(QRectF(x, 4, w, h), h / 2, h / 2)
        p.setPen(QColor(PALETTE["text"]))
        p.drawText(
            QRectF(x + 12, 4, w - 24, h),
            int(Qt.AlignmentFlag.AlignCenter),
            fm.elidedText(text, Qt.TextElideMode.ElideRight, int(w - 24)),
        )

    # ---------- ввод ----------
    def _item_at(self, pos: QPoint) -> int:
        for i, it in enumerate(self._items):
            if it.visible and it.box.contains(pos.toPointF()):
                return i
        return -1

    def _show_peek(self) -> None:
        if self._hover >= 0 and not self._show_orig():
            self._peek = self._hover
            self._peek_at = time.monotonic()
            self._kick()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.close()
        elif key == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space = True
            self._kick()
        elif (
            key == Qt.Key.Key_C
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self.copy_all()
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space = False
            self._kick()
        else:
            super().keyReleaseEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        idx = self._item_at(event.position().toPoint())
        if idx != self._hover:
            self._hover = idx
            self._hover_at = time.monotonic()
            self._peek = -1
            self._kick()
            self.setCursor(
                Qt.CursorShape.PointingHandCursor
                if idx >= 0
                else Qt.CursorShape.ArrowCursor
            )
            if idx >= 0:
                self._hover_timer.start()
            else:
                self._hover_timer.stop()
            self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = self._peek = -1
        self._hover_timer.stop()
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.RightButton:
            self.close()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        idx = self._item_at(event.position().toPoint())
        if idx < 0:
            self.close()
            return
        text = self._items[idx].dst
        QApplication.clipboard().setText(text)
        self.copied.emit(text)
        self._flash = idx
        self._flash_at = time.monotonic()
        self._flash_until = self._flash_at + FLASH_S
        self._set_msg("Скопировано")
        self._show_pill(1.5)
        self._kick()
