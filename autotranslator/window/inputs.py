"""Вставка из буфера и перетаскивание: картинки — в OCR, текстовые файлы — в поле ввода.

Часть главного окна: методы примешиваются к MainWindow (см. window/main.py)."""

from __future__ import annotations

import io
import logging
from pathlib import Path

from PyQt6.QtCore import (
    QBuffer,
    QIODevice,
)
from PyQt6.QtGui import (
    QImage,
)
from PyQt6.QtWidgets import (
    QApplication,
)

from ..core.langs import (
    ocr_tag,
)
from ..worker import OCR_ENHANCE, OCR_MERGE

log = logging.getLogger(__name__)

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif", ".tif", ".tiff"}
TEXT_EXT = {".txt", ".md", ".srt", ".vtt", ".ass", ".csv", ".json", ".rpy"}
MAX_DROP_TEXT = 512 * 1024  # больше — это уже не «перевести», а «зависнуть»


class InputMixin:
    def paste_and_translate(self) -> None:
        mime = QApplication.clipboard().mimeData()
        clip = QApplication.clipboard().text()
        if not clip.strip() and mime is not None:
            # скриншот (Win+Shift+S) или скопированный файл картинки — распознать
            if mime.hasImage():
                self.ocr_qimage(QApplication.clipboard().image())
                return
            if mime.hasUrls() and self._open_paths(
                [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]
            ):
                return
        if not clip.strip():
            self._set_status("Буфер обмена пуст", "error")
            return
        self.input.setPlainText(clip)
        self._debounce.stop()
        self.translate_now()

    def ocr_qimage(self, qimg: QImage) -> None:
        """Распознаёт картинку (QImage) и переводит текст с неё."""
        if qimg.isNull():
            self._set_status("Картинка не читается", "error")
            return
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        qimg.save(buf, "PNG")
        self._ocr_pil(bytes(buf.data()))

    def _ocr_pil(self, data: bytes) -> None:
        from PIL import Image

        try:
            image = Image.open(io.BytesIO(data))
            image.load()
        except Exception as e:  # noqa: BLE001 — битый/неподдерживаемый файл
            self._set_status("Картинка не читается", "error")
            self.status.setToolTip(str(e))
            return
        flags = OCR_MERGE if self.merge_chk.isChecked() else 0
        if self.enhance_chk.isChecked():
            flags |= OCR_ENHANCE
        self._ocr_req += 1
        self._ocr_busy = True
        self._ocr_auto = False
        self._pending_text = ""
        self._set_status("Распознаю картинку", "working")
        self.request_ocr_image.emit(
            self._ocr_req, image, ocr_tag(self.source_box.currentData()) or "", flags
        )

    def _open_paths(self, paths: list[str]) -> bool:
        """Первый подходящий файл: картинку — в OCR, текст — в поле ввода."""
        for raw in paths:
            path = Path(raw)
            ext = path.suffix.lower()
            try:
                if ext in IMAGE_EXT:
                    self._ocr_pil(path.read_bytes())
                    return True
                if ext in TEXT_EXT:
                    if path.stat().st_size > MAX_DROP_TEXT:
                        self._set_status("Файл слишком большой для перевода", "error")
                        return True
                    data = path.read_bytes()
                    try:
                        text = data.decode("utf-8-sig")
                    except UnicodeDecodeError:
                        text = data.decode("cp1251", errors="replace")
                    self.input.setPlainText(text)
                    self._debounce.stop()
                    self.translate_now()
                    return True
            except OSError as e:
                self._set_status("Не удалось открыть файл", "error")
                self.status.setToolTip(str(e))
                return True
        return False

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        mime = event.mimeData()
        if mime.hasImage() or mime.hasText() or mime.hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        mime = event.mimeData()
        if mime.hasUrls() and self._open_paths(
            [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]
        ):
            event.acceptProposedAction()
            return
        if mime.hasImage():
            self.ocr_qimage(QImage(mime.imageData()))
        elif mime.hasText() and mime.text().strip():
            self.input.setPlainText(mime.text())
            self._debounce.stop()
            self.translate_now()
        else:
            self._set_status(
                "Этот файл не перевести: нужна картинка или текст", "error"
            )
            return
        event.acceptProposedAction()
