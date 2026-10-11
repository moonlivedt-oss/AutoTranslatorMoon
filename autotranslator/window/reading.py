"""Режим «Чтение»: область экрана, рамка, субтитр, авто-обновление и «допечатка».

Часть главного окна: методы примешиваются к MainWindow (см. window/main.py)."""

from __future__ import annotations

import logging
import time

from PyQt6.QtCore import (
    QPoint,
    QTimer,
    pyqtSlot,
)
from PyQt6.QtWidgets import (
    QMenu,
)

from ..core.langs import (
    ocr_tag,
)
from ..core.textutil import norm
from ..services import advance
from ..ui import theme
from ..ui.icons import make_icon
from ..ui.overlay import ReadingOverlay, SubtitleOverlay
from ..ui.region import RegionSelector
from ..ui.theme import PALETTE
from ..win.winutil import to_physical
from ..worker import OCR_ENHANCE, OCR_MERGE, OCR_SKIP_SAME

log = logging.getLogger(__name__)

STABLE_RECHECK_MS = 280  # «допечатка»: через сколько перепроверить изменившийся текст
STABLE_MAX_S = 3.0  # дольше этого текст не ждём — переводим как есть
CLICK_DELAY_MS = (
    220  # через сколько после клика снимать кадр (игра успевает сменить реплику)
)
DISPLAY_MODES = (
    ("window", "Перевод: в окне"),
    ("below", "Перевод: под областью"),
    ("cover", "Перевод: поверх текста"),
)


class ReadingMixin:
    def _on_merge_toggled(self, _on: bool) -> None:
        # другая склейка — другой текст: перечитать область, если чтение идёт
        if self.reading_btn.isChecked():
            self.capture_region()

    def _on_interval_changed(self, value: int) -> None:
        self.cfg["auto_interval_ms"] = value
        self._auto_timer.setInterval(value)

    def pick_region(self) -> None:
        """Открывает выбор области. По завершении входим в режим чтения."""
        if self._overlay is not None:
            self._overlay.hide()  # не мешаем и не попадаем в свой же скриншот
        self._selector = RegionSelector()
        self._selector.selected.connect(self._on_region_selected)
        self._selector.cancelled.connect(self._on_pick_cancelled)
        if not self.isMinimized():
            self.showMinimized()
        self._selector.show()

    def _on_region_selected(self, left, top, width, height) -> None:
        self.cfg["region"] = [left, top, width, height]
        self.showNormal()
        self.raise_()
        # Выбор области сразу включает чтение (в этом весь смысл функции).
        if self.reading_btn.isChecked():
            self._start_reading()
        else:
            self.reading_btn.setChecked(True)  # -> _toggle_reading -> _start_reading

    def _on_pick_cancelled(self) -> None:
        self.showNormal()
        self._set_status("Отменено", "idle")
        # рамку вернём, если чтение всё ещё активно и область есть
        if (
            self._overlay is not None
            and self.reading_btn.isChecked()
            and self.cfg.get("region")
        ):
            self._position_overlay()
            self._overlay.show()
        elif not self.cfg.get("region") and self.reading_btn.isChecked():
            self.reading_btn.setChecked(False)  # нечего показывать — выключаем

    def _ensure_overlay(self) -> ReadingOverlay:
        if self._overlay is None:
            self._overlay = ReadingOverlay()
            self._overlay.region_changed.connect(self._on_overlay_moved)
            self._overlay.menu_requested.connect(self._show_frame_menu)
        return self._overlay

    def _ensure_subtitle(self) -> SubtitleOverlay:
        if self._subtitle is None:
            self._subtitle = SubtitleOverlay()
            self._subtitle.set_font_scale(self.cfg.get("font_scale", 100))
            self._subtitle.restore_requested.connect(self._restore_from_subtitle)
            self._subtitle.stop_requested.connect(
                lambda: self.reading_btn.setChecked(False)
            )
        return self._subtitle

    def _enter_subtitle_mode(self) -> None:
        """Прячет окно и показывает плашку-субтитр у выбранной области."""
        region = self.cfg.get("region")
        if not region:
            return
        sub = self._ensure_subtitle()
        sub.set_cover(self._display_mode() == "cover")
        sub.set_text(self.output.toPlainText())
        sub.place_by(*region)
        sub.show()
        sub.raise_()
        self._hidden_for_reading = True
        self.hide()

    def _leave_subtitle_mode(self, *, show_window: bool = True) -> None:
        """Убирает субтитр и (по умолчанию) возвращает окно."""
        if self._subtitle is not None:
            self._subtitle.hide()
        if show_window and self._hidden_for_reading:
            self.showNormal()
            self.raise_()
            self.activateWindow()
        self._hidden_for_reading = False

    def _restore_from_subtitle(self) -> None:
        """Кнопка «показать окно» на плашке — вернуть карточку, не прерывая чтение."""
        self._set_display_mode("window")  # -> _on_display_mode -> окно назад

    def _display_mode(self) -> str:
        return self.display_box.currentData() or "window"

    def _set_display_mode(self, mode: str) -> None:
        self.display_box.setCurrentIndex(max(0, self.display_box.findData(mode)))

    def _on_display_mode(self, _idx: int) -> None:
        # переключатель имеет эффект только при активном чтении
        if not self.reading_btn.isChecked():
            return
        mode = self._display_mode()
        if mode == "window":
            self._leave_subtitle_mode()
        elif self._hidden_for_reading and self._subtitle is not None:
            self._subtitle.set_cover(mode == "cover")
            self._subtitle.set_text(self.output.toPlainText())
            self._subtitle.place_by(*self.cfg["region"])
            self._subtitle.show()
        else:
            self._enter_subtitle_mode()

    def _show_frame_menu(self, pos: QPoint) -> None:
        """ПКМ по рамке чтения — всё управление под рукой, не открывая окно."""
        menu = QMenu(self)
        menu.addAction("Перечитать сейчас").triggered.connect(
            lambda: self.capture_region()
        )
        menu.addSeparator()
        cur = self._display_mode()
        for mode, label in DISPLAY_MODES:
            act = menu.addAction(("● " if mode == cur else "○ ") + label)
            act.triggered.connect(lambda _=False, m=mode: self._set_display_mode(m))
        menu.addSeparator()
        auto = menu.addAction(
            ("● " if self.auto_chk.isChecked() else "○ ") + "Авто-обновление"
        )
        auto.triggered.connect(lambda: self.auto_chk.toggle())
        on_click = self.click_chk.isChecked()
        click = menu.addAction(("● " if on_click else "○ ") + "Перечитывать по клику")
        click.setToolTip("ЛКМ / Пробел / Enter в игре — сразу перечитать область")
        click.triggered.connect(lambda: self.click_chk.toggle())
        menu.addAction("Выбрать другую область").triggered.connect(self.pick_region)
        menu.addAction("Показать окно").triggered.connect(self._show_main_from_menu)
        menu.addSeparator()
        menu.addAction("Остановить чтение").triggered.connect(
            lambda: self.reading_btn.setChecked(False)
        )
        menu.exec(pos)

    def _show_main_from_menu(self) -> None:
        if self._hidden_for_reading:
            self._set_display_mode("window")
        else:
            self.showNormal()
            self.raise_()
            self.activateWindow()

    def _on_overlay_moved(self, left, top, width, height) -> None:
        """Пользователь подвигал/растянул рамку — двигаем область OCR следом."""
        self.cfg["region"] = [left, top, width, height]
        self._set_status("Область перемещена", "idle")
        if self._subtitle is not None and self._subtitle.isVisible():
            self._subtitle.place_by(left, top, width, height)
        if self.reading_btn.isChecked():
            self._region_timer.start()  # перечитать, когда рамка «устаканится»

    def _position_overlay(self) -> None:
        region = self.cfg.get("region")
        if region and self._overlay is not None:
            self._overlay.set_region(*region)

    def _set_reading_icon(self, on: bool) -> None:
        self.reading_btn.setIcon(
            make_icon("eye", PALETTE["accent_text"] if on else theme.ICON)
        )

    def _start_reading(self) -> None:
        """Показывает рамку-маркер и запускает живой перевод области."""
        if not self.cfg.get("region"):
            self.pick_region()
            return
        overlay = self._ensure_overlay()
        self._position_overlay()
        overlay.set_active(True)
        overlay.show()
        self.reading_chip.setVisible(True)
        self._set_reading_icon(True)
        if self.auto_chk.isChecked():
            self._auto_timer.start()
        self._update_click_timer()
        self._set_status("● Чтение включено", "success", transient=True)
        self._last_ocr_text = ""  # первый кадр перевести заново (из кэша — мгновенно)
        if self.auto_chk.isChecked():
            # как авто-тик: недопечатанная реплика подождёт «допечатки»
            self._force_ocr = True
            self.capture_region(auto=True)
        else:
            self.capture_region()
        # прячем большое окно, оставляя перевод субтитром у области
        if self._display_mode() != "window":
            self._enter_subtitle_mode()

    def _stop_reading(self) -> None:
        self._auto_timer.stop()
        advance.watcher().release(self)
        self._pending_text = ""
        if self._overlay is not None:
            self._overlay.hide()
        self._leave_subtitle_mode()
        self.reading_chip.setVisible(False)
        self._set_reading_icon(False)
        self._set_status("Чтение выключено", "idle")

    def _update_click_timer(self) -> None:
        """Подписка на «перелистнул реплику» — пока идёт чтение с этой галочкой."""
        watcher = advance.watcher()
        if self.reading_btn.isChecked() and self.cfg.get("capture_on_click", True):
            watcher.acquire(self)
        else:
            watcher.release(self)

    def _set_capture_on_click(self, on: bool) -> None:
        self.cfg["capture_on_click"] = on
        self._update_click_timer()

    def _on_advance(self, _kind: str, _pos) -> None:
        if self.reading_btn.isChecked() and self.cfg.get("capture_on_click", True):
            QTimer.singleShot(CLICK_DELAY_MS, self._capture_after_click)

    def _capture_after_click(self) -> None:
        if self.reading_btn.isChecked() and self._lens is None:
            # кадр мог смениться еле заметно — распознать в любом случае
            self._force_ocr = True
            self.capture_region(auto=True)

    def _toggle_reading(self, on: bool) -> None:
        if on:
            self._start_reading()
        else:
            self._stop_reading()

    def _recapture_clicked(self) -> None:
        if self.cfg.get("region"):
            self.capture_region()
        else:
            self.pick_region()  # области ещё нет — сразу предложить выбрать

    def capture_region(self, *, auto: bool = False) -> None:
        region = self.cfg.get("region")
        if not region:
            self._set_status("Сначала выберите область", "error")
            return
        # авто-тик не ставит новый OCR, пока не вернулся предыдущий (медленный OCR
        # на большой области иначе копил бы очередь в потоке-воркере)
        if auto and self._ocr_busy:
            return
        lang = ocr_tag(self.source_box.currentData()) or ""
        left, top, width, height = self._physical_region(region)
        flags = 0
        if self.enhance_chk.isChecked():
            flags |= OCR_ENHANCE
        if self.merge_chk.isChecked():
            flags |= OCR_MERGE
        if auto and not self._force_ocr:
            flags |= OCR_SKIP_SAME
        self._force_ocr = False
        self._ocr_req += 1
        self._ocr_busy = True
        self._ocr_auto = auto
        if not auto:
            self._pending_text = ""
        if not auto:
            self._set_status("Распознаю", "working")
        if self._subtitle is not None and self._subtitle.isVisible() and not auto:
            self._subtitle.set_busy(True)
        self.request_ocr.emit(self._ocr_req, left, top, width, height, lang, flags)

    def _physical_region(self, region) -> tuple[int, int, int, int]:
        return to_physical(*(int(v) for v in region))

    @pyqtSlot(int)
    def _on_ocr_same(self, req_id) -> None:
        if req_id != self._ocr_req:
            return
        self._ocr_busy = False
        if self._pending_text:
            # кадр перестал меняться — текст допечатан, переводим
            self._accept_ocr_text(self._pending_text)
            return
        if self._subtitle is not None:
            self._subtitle.set_busy(False)
        if self._status_kind == "working" and not self._loading:
            self._set_status("Без изменений", "idle")

    @pyqtSlot(int, str)
    def _on_ocr(self, req_id, text) -> None:
        if req_id != self._ocr_req:
            return  # устаревший OCR — уже есть более свежий кадр
        self._ocr_busy = False
        text = text.strip()
        sub = self._subtitle
        if not text:
            self._pending_text = ""
            if sub is not None:
                sub.set_busy(False)
                if sub.cover and sub.isVisible() and self._ocr_auto:
                    # окно диалога пропало — не закрываем картинку старым переводом
                    sub.set_text("")
                    self._last_ocr_text = ""
            if self._ocr_auto:
                self._set_status("В области нет текста", "idle")
            else:
                self._set_status("Текст не распознан", "error")
            return
        # В авто-обновлении не дёргаем перевод, если на экране ничего не изменилось
        if self._ocr_auto and norm(text) == norm(self._last_ocr_text):
            self._pending_text = ""
            if sub is not None:
                sub.set_busy(False)
                if sub.cover and sub.isVisible() and self.output.toPlainText():
                    sub.set_text(self.output.toPlainText())
            if not self._loading:
                self._set_status("Без изменений", "idle")
            return
        # «Допечатка»: в новеллах реплика появляется по буквам — ждём, пока текст
        # перестанет меняться, иначе переводили бы обрывки на каждом тике.
        if self._ocr_auto and self.cfg.get("ocr_wait_stable", True):
            now = time.monotonic()
            if not self._pending_text or (
                norm(text) != norm(self._pending_text)
                and now - self._pending_since < STABLE_MAX_S
            ):
                if not self._pending_text:
                    self._pending_since = now
                self._pending_text = text
                self._set_status("Текст печатается", "working")
                QTimer.singleShot(STABLE_RECHECK_MS, self._recheck_pending)
                return
        self._accept_ocr_text(text)

    def _recheck_pending(self) -> None:
        if self._pending_text and self.reading_btn.isChecked():
            self.capture_region(auto=True)

    def _accept_ocr_text(self, text: str) -> None:
        """Распознанный текст устоялся — показываем и переводим."""
        self._pending_text = ""
        self._last_ocr_text = text
        if self._subtitle is not None and self._subtitle.isVisible():
            self._subtitle.set_busy(True)
        self.input.setPlainText(text)
        # setPlainText уже запустил debounce через textChanged — гасим его,
        # чтобы не переводить один и тот же кадр дважды.
        self._debounce.stop()
        self.translate_now()
        self._ocr_tr_req = self._tr_req

    def _toggle_auto_refresh(self, on: bool) -> None:
        # авто-обновление имеет смысл только при активном чтении
        if self.reading_btn.isChecked():
            if on:
                self._auto_timer.start()
                self.capture_region()
            else:
                self._auto_timer.stop()
