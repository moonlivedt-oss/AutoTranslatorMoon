"""Мост между PIL (снимки экрана, OCR) и Qt (отрисовка)."""

from __future__ import annotations

from PyQt6.QtGui import QImage


def pil_to_qimage(image, logical_width: int) -> QImage:
    """PIL.Image (RGB) -> QImage с devicePixelRatio под логическую ширину области.

    Снимок — в физических пикселях, окна Qt — в логических: ratio связывает их,
    чтобы картинка легла на экран один в один при любом масштабе Windows.
    """
    rgb = image.convert("RGB") if image.mode != "RGB" else image
    qimg = QImage(
        rgb.tobytes("raw", "RGB"),
        rgb.width,
        rgb.height,
        rgb.width * 3,
        QImage.Format.Format_RGB888,
    ).copy()
    qimg.setDevicePixelRatio(rgb.width / max(1, logical_width))
    return qimg
