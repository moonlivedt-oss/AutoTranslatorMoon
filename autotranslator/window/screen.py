"""Экранный перевод (весь экран / кусок) и живой перевод экрана.

Часть главного окна: методы примешиваются к MainWindow (см. window/main.py)."""

from __future__ import annotations

import logging

from PyQt6.QtCore import (
    QPoint,
    QRect,
    QTimer,
    pyqtSlot,
)
from PyQt6.QtGui import (
    QCursor,
    QGuiApplication,
)
from PyQt6.QtWidgets import (
    QApplication,
)

from ..core.langs import (
    ocr_tag,
)
from ..live import LiveSession, LiveWorkers
from ..ui import theme
from ..ui.icons import make_icon
from ..ui.imaging import pil_to_qimage
from ..ui.lens import LensOverlay
from ..ui.region import RegionSelector
from ..ui.theme import PALETTE
from ..win.winutil import client_rect, pick_game_window, to_logical, to_physical
from ..worker import OCR_ENHANCE

log = logging.getLogger(__name__)


class ScreenMixin:
    def open_lens(self, rect: QRect | None = None, *, full: bool | None = None) -> None:
        """Снимает экран (или кусок), показывает его застывшим и переводит поверх."""
        if self._lens is not None and not self._lens.closing:
            self._close_lens()  # та же клавиша ещё раз — закрыть
            return
        if full is None:
            full = rect is None
        if rect is None:
            screen = (
                QGuiApplication.screenAt(QCursor.pos())
                or QGuiApplication.primaryScreen()
            )
            rect = screen.geometry()
        try:
            from ..services import ocr

            image = ocr.grab_region(
                *to_physical(rect.x(), rect.y(), rect.width(), rect.height())
            )
        except Exception as e:  # noqa: BLE001
            self._set_status("Не удалось снять экран: " + str(e), "error")
            return
        qimg = pil_to_qimage(image, rect.width())
        lens = LensOverlay(
            rect,
            qimg,
            full=full,
            target=self.target_box.currentData() or "ru",
            screens=self._screen_labels() if full else [],
        )
        lens.closed.connect(lambda lens=lens: self._on_lens_closed(lens))
        lens.copied.connect(
            lambda _t: self._set_status("Скопировано", "success", transient=True)
        )
        lens.retranslate.connect(self._lens_retranslate)
        lens.to_window.connect(self._show_window)
        lens.screen_requested.connect(self._lens_on_screen)
        live_rect = QRect(rect)
        lens.live_requested.connect(lambda: self._lens_to_live(live_rect))
        self._lens = lens
        self._lens_data = []
        lens.show()
        self._lens_req += 1
        # «Улучшать» (апскейл ×2) утраивает время OCR, а на целом экране текст и так
        # крупный — включаем его только для выделенного куска
        flags = OCR_ENHANCE if self.enhance_chk.isChecked() and not full else 0
        self._set_status("Экранный перевод", "working")
        self.request_lens.emit(
            self._lens_req,
            image,
            ocr_tag(self.source_box.currentData()) or "",
            flags,
            self.engine_box.currentData(),
            self.source_box.currentData(),
            self.target_box.currentData(),
        )

    def pick_lens_area(self) -> None:
        """Выделить кусок экрана мышкой — перевод сразу ляжет поверх него."""
        self._close_lens()
        self._lens_selector = RegionSelector()
        self._lens_selector.selected.connect(
            lambda x, y, w, h: QTimer.singleShot(
                30, lambda: self.open_lens(QRect(x, y, w, h))
            )
        )
        self._lens_selector.cancelled.connect(
            lambda: self._set_status("Отменено", "idle")
        )
        self._lens_selector.show()
        self._lens_selector.activateWindow()

    @staticmethod
    def _screen_labels() -> list[str]:
        """Подписи мониторов для меню оверлея; пустая строка — пункт не нужен."""
        screens = QGuiApplication.screens()
        if len(screens) < 2:
            return []
        return [
            f"Экран {i + 1} — {s.geometry().width()}×{s.geometry().height()}"
            for i, s in enumerate(screens)
        ]

    def _lens_on_screen(self, n: int) -> None:
        """Меню оверлея: перевести другой монитор (или все сразу)."""
        screens = QGuiApplication.screens()
        if n < 0:
            if len({round(s.devicePixelRatio(), 3) for s in screens}) > 1:
                self._set_status(
                    "«Все экраны» — только при одинаковом масштабе мониторов", "error"
                )
                return
            rect = QRect()
            for s in screens:
                rect = rect.united(s.geometry())
        elif n < len(screens):
            rect = screens[n].geometry()
        else:
            return
        self._close_lens()
        QTimer.singleShot(60, lambda: self.open_lens(rect, full=True))

    def _toggle_live(self, on: bool) -> None:
        if on:
            self.start_live()
        elif self._live is not None:
            self._live.stop()  # -> _on_live_stopped

    def _live_workers(self) -> LiveWorkers:
        if self._live_threads is None:
            self._live_threads = LiveWorkers(
                bool(self.cfg.get("offline_fallback", True))
            )
        return self._live_threads

    def _live_target(self) -> tuple[QRect, int]:
        """Что переводить вживую: окно игры (по умолчанию) или весь экран под курсором."""
        if self.cfg.get("live_target", "window") == "window":
            hwnd = pick_game_window()
            phys = client_rect(hwnd) if hwnd else None
            if phys is not None:
                return QRect(*to_logical(*phys)), hwnd
        screen = (
            QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        )
        return screen.geometry(), 0

    def start_live(self, rect: QRect | None = None) -> None:
        """Перевод поверх игры, который обновляется сам, пока листаешь реплики."""
        if self._live is not None:
            self._live.stop()
        self._close_lens()
        hwnd = 0
        if rect is None:
            rect, hwnd = self._live_target()
        # режим «Чтение» на это время выключаем: две рамки и два OCR поверх игры
        # только мешали бы друг другу; после живого перевода он вернётся
        self._live_paused_reading = self.reading_btn.isChecked()
        if self._live_paused_reading:
            self.reading_btn.setChecked(False)
        offset = self.cfg.get("live_bar_offset")
        workers = self._live_workers()
        live = LiveSession(
            rect,
            workers.capture,
            workers.translator,
            self._live_settings,
            hwnd=hwnd,
            bar_offset=QPoint(*offset) if offset else None,
            parent=self,
        )
        live.stopped.connect(self._on_live_stopped)
        live.status.connect(lambda text, kind: self._set_status(text, kind))
        live.bar_moved.connect(
            lambda pos: self.cfg.__setitem__("live_bar_offset", [pos.x(), pos.y()])
        )
        self._live = live
        # окно переводчика не должно закрывать игру — прячем на время
        self._live_hid_window = self.isVisible() and not self._hidden_for_reading
        if self._live_hid_window:
            self.hide()
        self.live_btn.blockSignals(True)
        self.live_btn.setChecked(True)
        self.live_btn.blockSignals(False)
        self._set_live_icon(True)
        live.start()

    def _set_live_icon(self, on: bool) -> None:
        self.live_btn.setIcon(
            make_icon("live", PALETTE["accent_text"] if on else theme.ICON)
        )

    def _live_settings(self) -> tuple[str, str, str, str]:
        source = self.source_box.currentData()
        return (
            self.engine_box.currentData(),
            source,
            self.target_box.currentData(),
            ocr_tag(source) or "",
        )

    def _on_live_stopped(self) -> None:
        live, self._live = self._live, None
        if live is not None:
            live.deleteLater()
        self.live_btn.blockSignals(True)
        self.live_btn.setChecked(False)
        self.live_btn.blockSignals(False)
        self._set_live_icon(False)
        self._set_status("Живой перевод выключен", "idle")
        if self._live_hid_window:
            self._live_hid_window = False
            self._show_window()
        if self._live_paused_reading:
            self._live_paused_reading = False
            self.reading_btn.setChecked(True)

    def _refresh_live(self, *_args) -> None:
        if self._live is not None:
            self._live.refresh()

    def _lens_to_live(self, rect: QRect) -> None:
        QTimer.singleShot(60, lambda: self.start_live(rect))

    def _lens_retranslate(self, code: str) -> None:
        """Язык сменили прямо на оверлее — переводим уже найденные блоки заново."""
        if not self._lens_data or self._lens is None:
            return
        self.target_box.setCurrentIndex(max(0, self.target_box.findData(code)))
        self._lens_req += 1
        self._set_status("Экранный перевод", "working")
        self.request_lens_tr.emit(
            self._lens_req,
            self._lens_data,
            self.engine_box.currentData(),
            self.source_box.currentData(),
            code,
        )

    def _close_lens(self) -> None:
        """Закрыть экранный перевод. Он ещё гаснет пару кадров, но для окна его уже
        нет: можно сразу открыть новый (другой монитор, живой перевод)."""
        lens, self._lens = self._lens, None
        if lens is not None:
            self._lens_req += 1  # запоздалые ответы воркера гаснущему не нужны
            lens.close()
            if self._status_kind == "working":
                self._set_status("Готово", "idle")

    def _on_lens_closed(self, lens) -> None:
        if lens is not self._lens:
            return  # закрылся уже отпущенный (гаснущий) оверлей
        self._lens = None
        self._lens_req += 1  # запоздалый ответ воркера закрытому оверлею не нужен
        if self._status_kind == "working":
            self._set_status("Готово", "idle")

    @pyqtSlot(int, object)
    def _on_lens_blocks(self, req_id, blocks) -> None:
        if req_id == self._lens_req and self._lens is not None:
            self._lens_data = list(blocks)
            self._lens.set_blocks(blocks)
            if not blocks:
                self._set_status("Текст на экране не найден", "idle")

    @pyqtSlot(int, object)
    def _on_lens_partial(self, req_id, texts) -> None:
        if req_id == self._lens_req and self._lens is not None:
            self._lens.set_partial(texts)

    @pyqtSlot(int, object)
    def _on_lens_translated(self, req_id, texts) -> None:
        if req_id != self._lens_req or self._lens is None:
            return
        self._lens.set_translations(texts)
        src, dst = self._lens.all_text()
        self._set_status("Готово", "success", transient=True)
        if dst:
            # весь распознанный текст и перевод — ещё и в окно (и в историю)
            self.input.setPlainText(src)
            self._debounce.stop()
            self.output.setPlainText(dst)
            self._push_history(src, dst)
            if self.autocopy_chk.isChecked():
                QApplication.clipboard().setText(dst)

    @pyqtSlot(int, str)
    def _on_lens_failed(self, req_id, error) -> None:
        if req_id != self._lens_req or self._lens is None:
            return
        self._lens.set_error(error)
        self._set_status("Ошибка экранного перевода", "error")
        self.status.setToolTip(error)
