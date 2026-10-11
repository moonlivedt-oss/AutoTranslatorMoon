"""Векторные иконки, нарисованные через QPainter — без внешних файлов и эмодзи.

Все иконки монохромные, линиями; цвет задаётся при отрисовке, чтобы совпадать
с темой. make_icon(name, color, size) -> QIcon.
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap


def _arrow_head(p: QPainter, tip: QPointF, dx: float, k: float = 3.2) -> None:
    """Рисует наконечник стрелки в точке tip, направленный по знаку dx."""
    s = -1.0 if dx < 0 else 1.0
    p.drawLine(tip, QPointF(tip.x() - s * k, tip.y() - k))
    p.drawLine(tip, QPointF(tip.x() - s * k, tip.y() + k))


def _draw(name: str, p: QPainter, s: int) -> None:
    m = 3.0
    a, b = m, s - m  # рабочие границы
    mid = s / 2.0

    if name == "close":
        p.drawLine(QPointF(a, a), QPointF(b, b))
        p.drawLine(QPointF(b, a), QPointF(a, b))

    elif name == "window":  # окно с шапкой — «показать полное окно»
        p.drawRoundedRect(QRectF(a, a + 1, b - a, b - a - 2), 2, 2)
        p.drawLine(QPointF(a, a + 4.5), QPointF(b, a + 4.5))

    elif name == "min":
        y = s * 0.62
        p.drawLine(QPointF(a, y), QPointF(b, y))

    elif name == "pin":  # «поверх всех» — две наложенные пластины
        back = QRectF(a + 2, a, s * 0.5, s * 0.5)
        front = QRectF(a, a + 2.5, s * 0.5, s * 0.5)
        p.drawRoundedRect(back, 2, 2)
        p.fillRect(front, QColor(0, 0, 0, 0))  # «прорезь» под front
        p.drawRoundedRect(front, 2, 2)

    elif name == "swap":  # ⇄
        y1, y2 = s * 0.4, s * 0.62
        p.drawLine(QPointF(a + 1, y1), QPointF(b - 1, y1))
        _arrow_head(p, QPointF(b - 1, y1), +1)
        p.drawLine(QPointF(b - 1, y2), QPointF(a + 1, y2))
        _arrow_head(p, QPointF(a + 1, y2), -1)

    elif name == "scan":  # рамка с уголками (маркер захвата)
        c = s * 0.28
        # четыре Г-образных уголка
        p.drawLine(QPointF(a, a + c), QPointF(a, a))
        p.drawLine(QPointF(a, a), QPointF(a + c, a))
        p.drawLine(QPointF(b - c, a), QPointF(b, a))
        p.drawLine(QPointF(b, a), QPointF(b, a + c))
        p.drawLine(QPointF(b, b - c), QPointF(b, b))
        p.drawLine(QPointF(b, b), QPointF(b - c, b))
        p.drawLine(QPointF(a + c, b), QPointF(a, b))
        p.drawLine(QPointF(a, b), QPointF(a, b - c))

    elif name == "screen":  # монитор на подставке — «весь экран»
        p.drawRoundedRect(QRectF(a, a + 0.5, b - a, (b - a) * 0.66), 2, 2)
        foot = a + 0.5 + (b - a) * 0.66
        p.drawLine(QPointF(mid, foot), QPointF(mid, b - 0.5))
        p.drawLine(QPointF(mid - s * 0.2, b - 0.5), QPointF(mid + s * 0.2, b - 0.5))

    elif name == "crop":  # два уголка внахлёст — «кусок экрана»
        k = s * 0.22
        p.drawLine(QPointF(a + k, a), QPointF(a + k, b - k))
        p.drawLine(QPointF(a + k, b - k), QPointF(b, b - k))
        p.drawLine(QPointF(a, a + k), QPointF(b - k, a + k))
        p.drawLine(QPointF(b - k, a + k), QPointF(b - k, b))

    elif name == "refresh":  # круговая стрелка
        rect = QRectF(a, a, b - a, b - a)
        path = QPainterPath()
        path.arcMoveTo(rect, 65)
        path.arcTo(rect, 65, 280)
        p.drawPath(path)
        # наконечник у начала дуги (верх-право)
        import math

        ang = math.radians(65)
        r = (b - a) / 2
        cx, cy = mid, mid
        tx = cx + r * math.cos(ang)
        ty = cy - r * math.sin(ang)
        p.drawLine(QPointF(tx, ty), QPointF(tx - 3.2, ty - 2.2))
        p.drawLine(QPointF(tx, ty), QPointF(tx + 2.2, ty - 3.2))

    elif name == "copy":  # две наложенные страницы
        p.drawRoundedRect(QRectF(a + 2.5, a, s * 0.5, s * 0.55), 2, 2)
        p.drawRoundedRect(QRectF(a, a + 2.5, s * 0.5, s * 0.55), 2, 2)

    elif name == "paste":  # планшет с зажимом
        p.drawRoundedRect(QRectF(a, a + 1.5, b - a, b - a - 1.5), 2, 2)
        clip = QRectF(mid - s * 0.16, a, s * 0.32, s * 0.16)
        p.drawRoundedRect(clip, 1.5, 1.5)

    elif name == "clear":  # ластик/крестик в мягком квадрате
        p.drawLine(QPointF(a + 1, a + 1), QPointF(b - 1, b - 1))
        p.drawLine(QPointF(b - 1, a + 1), QPointF(a + 1, b - 1))

    elif name == "eye":  # режим чтения — глаз со зрачком
        w = b - a
        eye = QRectF(a, mid - w * 0.30, w, w * 0.60)
        path = QPainterPath()
        path.moveTo(eye.left(), mid)
        path.quadTo(mid, eye.top(), eye.right(), mid)
        path.quadTo(mid, eye.bottom(), eye.left(), mid)
        p.drawPath(path)
        p.drawEllipse(QPointF(mid, mid), w * 0.13, w * 0.13)

    elif name == "live":  # «в эфире»: точка и расходящиеся дуги — живой перевод
        w = b - a
        p.drawEllipse(QPointF(mid, mid), w * 0.11, w * 0.11)
        for r in (w * 0.30, w * 0.48):
            box = QRectF(mid - r, mid - r, 2 * r, 2 * r)
            p.drawArc(box, -40 * 16, 80 * 16)  # справа
            p.drawArc(box, 140 * 16, 80 * 16)  # слева

    elif name == "pause":  # две вертикальные черты
        w = b - a
        p.drawLine(
            QPointF(mid - w * 0.17, a + w * 0.12), QPointF(mid - w * 0.17, b - w * 0.12)
        )
        p.drawLine(
            QPointF(mid + w * 0.17, a + w * 0.12), QPointF(mid + w * 0.17, b - w * 0.12)
        )

    elif name == "stop":  # стоп — скруглённый квадрат
        p.drawRoundedRect(QRectF(a + 1.5, a + 1.5, b - a - 3, b - a - 3), 2, 2)

    elif name == "speak":  # динамик + звуковые дуги
        w = b - a
        bx = a + w * 0.06
        p.drawLine(QPointF(bx, mid - w * 0.12), QPointF(bx, mid + w * 0.12))
        cone = QPainterPath()
        cone.moveTo(bx, mid - w * 0.12)
        cone.lineTo(a + w * 0.34, mid - w * 0.12)
        cone.lineTo(a + w * 0.52, mid - w * 0.26)
        cone.lineTo(a + w * 0.52, mid + w * 0.26)
        cone.lineTo(a + w * 0.34, mid + w * 0.12)
        cone.lineTo(bx, mid + w * 0.12)
        p.drawPath(cone)
        arc = QRectF(a + w * 0.44, mid - w * 0.30, w * 0.42, w * 0.60)
        p.drawArc(arc, -55 * 16, 110 * 16)

    elif name == "history":  # часы со стрелками
        rect = QRectF(a + 0.5, a + 0.5, b - a - 1, b - a - 1)
        p.drawEllipse(rect)
        p.drawLine(QPointF(mid, mid), QPointF(mid, a + (b - a) * 0.28))
        p.drawLine(QPointF(mid, mid), QPointF(a + (b - a) * 0.66, mid))

    elif name == "theme":  # полумесяц (переключение темы)
        big = QPainterPath()
        big.addEllipse(QRectF(a, a, b - a, b - a))
        small = QPainterPath()
        small.addEllipse(
            QRectF(
                a + (b - a) * 0.32, a - (b - a) * 0.02, (b - a) * 0.82, (b - a) * 0.82
            )
        )
        p.drawPath(big.subtracted(small))

    elif name == "game":  # геймпад: корпус, крестовина, две кнопки
        w = b - a
        p.drawRoundedRect(QRectF(a, mid - w * 0.28, w, w * 0.56), w * 0.24, w * 0.24)
        cx = a + w * 0.28
        p.drawLine(QPointF(cx - w * 0.10, mid), QPointF(cx + w * 0.10, mid))
        p.drawLine(QPointF(cx, mid - w * 0.10), QPointF(cx, mid + w * 0.10))
        p.drawEllipse(QPointF(a + w * 0.68, mid - w * 0.05), 0.9, 0.9)
        p.drawEllipse(QPointF(a + w * 0.80, mid + w * 0.07), 0.9, 0.9)

    elif name == "edit":  # карандаш наискосок
        w = b - a
        p.drawLine(
            QPointF(a + w * 0.18, b - w * 0.18), QPointF(b - w * 0.12, a + w * 0.08)
        )
        p.drawLine(QPointF(a + w * 0.30, b - w * 0.06), QPointF(b, a + w * 0.20))
        p.drawLine(
            QPointF(a + w * 0.18, b - w * 0.18), QPointF(a + w * 0.30, b - w * 0.06)
        )
        p.drawLine(QPointF(a + w * 0.18, b - w * 0.18), QPointF(a, b))

    elif name == "check":  # галочка — «готово / скопировано»
        w = b - a
        path = QPainterPath()
        path.moveTo(a + w * 0.08, a + w * 0.55)
        path.lineTo(a + w * 0.38, a + w * 0.82)
        path.lineTo(b - w * 0.04, a + w * 0.2)
        p.drawPath(path)

    elif name in ("chevron_down", "chevron_up"):  # раскрыть / свернуть
        w = b - a
        top, bot = a + w * 0.33, a + w * 0.67
        if name == "chevron_up":
            top, bot = bot, top
        path = QPainterPath()
        path.moveTo(a + w * 0.15, top)
        path.lineTo(mid, bot)
        path.lineTo(b - w * 0.15, top)
        p.drawPath(path)

    elif name == "back":  # стрелка влево — «назад» со страницы настроек
        w = b - a
        p.drawLine(QPointF(a + w * 0.12, mid), QPointF(b - w * 0.08, mid))
        path = QPainterPath()
        path.moveTo(a + w * 0.45, a + w * 0.18)
        path.lineTo(a + w * 0.12, mid)
        path.lineTo(a + w * 0.45, b - w * 0.18)
        p.drawPath(path)

    elif name == "gear":  # шестерёнка — настройки
        import math

        r_out, r_in = (b - a) / 2, (b - a) * 0.34
        path = QPainterPath()
        teeth = 8
        for i in range(teeth * 2):
            ang = math.pi * i / teeth
            r = r_out if i % 2 == 0 else r_in * 1.12
            for da in (-0.17, 0.17):
                x = mid + r * math.cos(ang + da)
                y = mid + r * math.sin(ang + da)
                if i == 0 and da < 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
        path.closeSubpath()
        p.drawPath(path)
        p.drawEllipse(QPointF(mid, mid), r_in * 0.48, r_in * 0.48)

    elif name == "help":  # «?» в круге — справка
        w = b - a
        p.drawEllipse(QRectF(a, a, w, w))
        q = QPainterPath()
        q.moveTo(mid - w * 0.17, a + w * 0.36)
        q.quadTo(mid - w * 0.17, a + w * 0.18, mid, a + w * 0.18)
        q.quadTo(mid + w * 0.19, a + w * 0.18, mid + w * 0.19, a + w * 0.36)
        q.quadTo(mid + w * 0.19, a + w * 0.48, mid, a + w * 0.55)
        q.lineTo(mid, a + w * 0.64)
        p.drawPath(q)
        p.drawPoint(QPointF(mid, a + w * 0.79))

    elif name == "book":  # раскрытая книга — глоссарий
        w = b - a
        top, bot = a + w * 0.12, b - w * 0.08
        left = QPainterPath()
        left.moveTo(mid, top + w * 0.06)
        left.quadTo(a + w * 0.25, top - w * 0.04, a, top + w * 0.04)
        left.lineTo(a, bot - w * 0.06)
        left.quadTo(a + w * 0.25, bot - w * 0.14, mid, bot)
        p.drawPath(left)
        right = QPainterPath()
        right.moveTo(mid, top + w * 0.06)
        right.quadTo(b - w * 0.25, top - w * 0.04, b, top + w * 0.04)
        right.lineTo(b, bot - w * 0.06)
        right.quadTo(b - w * 0.25, bot - w * 0.14, mid, bot)
        p.drawPath(right)
        p.drawLine(QPointF(mid, top + w * 0.06), QPointF(mid, bot))


def make_icon(
    name: str, color: str = "#c8cdd6", size: int = 18, angle: float = 0.0
) -> QIcon:
    scale = 2  # рисуем крупнее для чётких краёв на HiDPI
    pm = QPixmap(size * scale, size * scale)
    pm.setDevicePixelRatio(scale)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    if angle:
        # поворот вокруг центра — для микро-анимации (кнопка ⇄ и т.п.)
        mid = size / 2.0
        p.translate(mid, mid)
        p.rotate(angle)
        p.translate(-mid, -mid)
    pen = QPen(QColor(color))
    pen.setWidthF(1.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    _draw(name, p, size)
    p.end()
    return QIcon(pm)
