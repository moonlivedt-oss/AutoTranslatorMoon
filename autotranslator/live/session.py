"""Сеанс живого перевода: когда снимать экран, что переводить, что показывать.

Цикл:
1. тик (раз в ``TICK_MS``) или нажатие «дальше» в игре -> снять кадр
   (``CaptureWorker`` сам решает, нужен ли OCR, см. capture.py);
2. блоки кадра -> ``BlockTracker``: какие тексты новые, какие ещё «печатаются»,
   какие остались прежними (их перевод не трогаем);
3. устоявшиеся тексты без перевода -> переводчик (свой поток, кэш переводов);
4. переводы -> ``LiveOverlay`` поверх игры.

Окно игры: если сеанс привязан к окну (``hwnd``), слой следует за ним — окно
передвинули или растянули, слой и область захвата едут следом; игра свёрнута или
её закрыло другое окно (браузер) — перевод прячется и не висит поверх чужого;
игру закрыли — сеанс останавливается сам.
"""

from __future__ import annotations

import time

from PyQt6.QtCore import QObject, QPoint, QRect, QThread, QTimer, pyqtSignal

from ..services import advance
from ..ui.imaging import pil_to_qimage
from ..win.winutil import (
    client_rect,
    covered_by_other,
    foreground_window,
    to_logical,
    to_physical,
    window_pid,
    window_state,
)
from ..worker import Worker
from .capture import CaptureWorker
from .overlay import LiveBar, LiveOverlay
from .tracker import BlockTracker, DialogueArea

TICK_MS = 700  # фоновая проверка экрана (неизменный кадр почти бесплатен)
CLICK_DELAY_MS = 180  # после клика игре нужно мгновение, чтобы сменить реплику
CLICK_SETTLE_S = (
    0.7  # столько после клика ждём смены текста, прежде чем вернуть перевод
)
CLICK_RETRY_MS = 300  # клик ещё ничего не сменил — перепроверить через столько
STABLE_RECHECK_MS = 260  # «допечатка»: через сколько перепроверить изменившийся текст
DIALOGUE_LEARN_S = 2.0  # столько после клика сменившийся текст считаем репликой
DIALOGUE_MARGIN = 12  # запас вокруг окна диалога для слежки, физ. пиксели
RETRY_FAILED_MS = 4000  # перевод не удался (нет сети) — повторить через столько


class LiveWorkers:
    """Два фоновых потока живого перевода: захват+OCR и перевод.

    Раздельно — чтобы OCR следующего кадра не ждал медленную сеть. Потоки живут,
    пока живёт окно, и переиспользуются всеми сеансами.
    """

    def __init__(self, offline_fallback: bool = True) -> None:
        self.capture = CaptureWorker()
        self.translator = Worker()
        self.translator.offline_fallback = offline_fallback
        self._threads: list[QThread] = []
        for obj, name in ((self.capture, "live-capture"), (self.translator, "live-tr")):
            th = QThread()
            th.setObjectName(name)
            obj.moveToThread(th)
            th.start()
            self._threads.append(th)

    def set_offline_fallback(self, on: bool) -> None:
        self.translator.offline_fallback = on

    @property
    def threads(self) -> list[QThread]:
        return list(self._threads)

    def shutdown(self, wait_ms: int = 1500) -> None:
        for th in self._threads:
            th.quit()
        for th in self._threads:
            th.wait(wait_ms)


def _dialogue_like(block, area_width: float) -> bool:
    """Похоже на реплику (её сменит клик), а не на подпись кнопки интерфейса."""
    text = block.text
    return "\n" in text or len(text.split()) >= 4 or block.width >= 0.3 * area_width


class LiveSession(QObject):
    """Живой перевод области экрана (или окна игры)."""

    stopped = pyqtSignal()
    status = pyqtSignal(str, str)  # текст, kind для статуса главного окна
    bar_moved = pyqtSignal(QPoint)  # где панель относительно области (запомнить)
    request_capture = pyqtSignal(int, int, int, int, int, str, bool, object)
    request_translate = pyqtSignal(int, object, str, str, str)
    request_reset = pyqtSignal()

    def __init__(
        self,
        rect: QRect,
        capture,
        translator,
        settings,
        *,
        hwnd: int = 0,
        bar_offset: QPoint | None = None,
        parent=None,
    ) -> None:
        """settings() -> (engine, source, target, ocr_lang) — текущие из окна."""
        super().__init__(parent)
        self._rect = QRect(rect)
        self._phys = to_physical(rect.x(), rect.y(), rect.width(), rect.height())
        self._hwnd = hwnd
        self._game_pid = window_pid(hwnd) if hwnd else 0
        self._settings = settings
        self._bar_offset = bar_offset
        self._capture_w = capture
        self._translator = translator
        self._tracker = BlockTracker()
        self._dialogue = DialogueArea()
        self._req = 0
        self._busy = False
        self._again: bool | None = None  # кадр попросили, пока прошлый не вернулся
        self._watch_ids: list[int | None] = []
        self._tr_req = 0
        self._asked: dict[int, list[tuple[int, str]]] = {}
        self._image = None  # последний распознанный кадр (PIL)
        self._qimage = None
        self._paused = False
        self._original = False
        self._peek = False
        self._hidden_away = False  # игра свёрнута / закрыта другим окном
        self._click_at = 0.0
        self._click_retried = False
        self._last_status: tuple[str, str] = ("", "")
        self._stopped = False

        self.overlay = LiveOverlay(self._rect)
        self.bar = LiveBar()
        self.bar.stop_requested.connect(self.stop)
        self.bar.pause_toggled.connect(self.set_paused)
        self.bar.original_toggled.connect(self._set_original)
        self.bar.refresh_requested.connect(self.reread)
        self.bar.moved.connect(self._on_bar_moved)

        self.request_capture.connect(capture.capture)
        self.request_reset.connect(capture.reset)
        self.request_translate.connect(translator.do_live_translate)
        self._links = (
            (capture.frame, self._on_frame),
            (capture.same, self._on_same),
            (capture.dirty, self._on_dirty),
            (capture.failed, self._on_capture_failed),
            (translator.live_translated, self._on_translated),
            (translator.live_failed, self._on_translate_failed),
        )
        for sig, slot in self._links:
            sig.connect(slot)

        self._tick = QTimer(self)
        self._tick.setInterval(TICK_MS)
        self._tick.timeout.connect(self._on_tick)
        self._retry = QTimer(self)
        self._retry.setSingleShot(True)
        self._retry.setInterval(RETRY_FAILED_MS)
        self._retry.timeout.connect(self._translate_ready)

    @property
    def rect(self) -> QRect:
        return QRect(self._rect)

    @property
    def hwnd(self) -> int:
        return self._hwnd

    # ---------- жизненный цикл ----------
    def start(self) -> None:
        self.request_reset.emit()
        self.overlay.show()
        self.bar.place_on(self._rect, self._bar_offset)
        self.bar.show()
        self._tick.start()
        watcher = advance.watcher()
        watcher.advanced.connect(self._on_advance)
        watcher.peek_changed.connect(self._on_peek)
        watcher.acquire(self)
        self._status("Читаю экран", "work")
        self.capture(force=True)

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        self._tick.stop()
        self._retry.stop()
        watcher = advance.watcher()
        watcher.release(self)
        for sig, slot in (
            *self._links,
            (watcher.advanced, self._on_advance),
            (watcher.peek_changed, self._on_peek),
        ):
            try:
                sig.disconnect(slot)
            except TypeError:
                pass
        self.overlay.close()
        self.bar.close()
        self.stopped.emit()

    def set_paused(self, on: bool) -> None:
        self._paused = on
        if on:
            self.overlay.hide()
            self._status("Пауза", "pause")
        else:
            self.overlay.show()
            self._status("Читаю экран", "work")
            self.capture(force=True)

    def _set_original(self, on: bool) -> None:
        self._original = on
        self.overlay.set_peek(self._original or self._peek)

    def _on_peek(self, on: bool) -> None:
        self._peek = on
        self.overlay.set_peek(self._original or self._peek)

    def refresh(self) -> None:
        """Сменили язык / движок: тексты на экране те же — только перевести заново."""
        self._asked.clear()
        self._tracker.forget_translations()
        self.overlay.clear()
        if self._image is None:
            self.capture(force=True)
        else:
            self._translate_ready()

    def reread(self) -> None:
        """Кнопка «перечитать»: распознать экран с нуля."""
        self._asked.clear()
        self._tracker.reset()
        self.overlay.clear()
        self.request_reset.emit()
        self._status("Читаю экран", "work")
        self.capture(force=True)

    def _status(self, text: str, state: str) -> None:
        if (text, state) == self._last_status:
            return
        self._last_status = (text, state)
        self.bar.set_status(text, state)
        kind = {"ok": "success", "work": "working", "error": "error"}.get(state, "idle")
        self.status.emit(text, kind)

    def _idle_status(self) -> None:
        if self._paused or self._hidden_away:
            return
        if self._asked:
            self._status("Перевожу", "work")
        elif self._tracker.pending:
            self._status("Жду, пока допечатается", "work")
        elif not self._tracker.slots:
            self._status("Нет текста на экране", "ok")
        else:
            self._status("Живой перевод", "ok")

    def _on_bar_moved(self, pos: QPoint) -> None:
        self._bar_offset = pos - self._rect.topLeft()
        self.bar_moved.emit(self._bar_offset)

    # ---------- окно игры ----------
    def _on_tick(self) -> None:
        if self._hwnd and not self._follow_window():
            return
        self.capture(force=False)

    def _follow_window(self) -> bool:
        """Следить за окном игры. False — снимать кадр сейчас не нужно."""
        state = window_state(self._hwnd)
        if state == "gone":
            self._status("Окно игры закрыто", "pause")
            self.stop()
            return False
        if state == "minimized":
            self._set_hidden_away(True, "Игра свёрнута")
            return False
        if covered_by_other(self._hwnd):
            self._set_hidden_away(True, "Игра за другим окном")
            return False
        phys = client_rect(self._hwnd)
        if phys is not None and phys != self._phys:
            self._move_to(phys)
        if self._hidden_away:
            self._set_hidden_away(False)
            self.capture(force=True)
            return False
        return True

    def _set_hidden_away(self, on: bool, why: str = "") -> None:
        if on == self._hidden_away:
            return
        self._hidden_away = on
        if on:
            self.overlay.hide()
            self.bar.hide()
            self._status(why, "pause")
        else:
            if not self._paused:
                self.overlay.show()
            self.bar.show()
            self._idle_status()

    def _move_to(self, phys: tuple[int, int, int, int]) -> None:
        """Окно игры передвинули / растянули — слой и область захвата следом."""
        self._phys = phys
        self._rect = QRect(*to_logical(*phys))
        self.overlay.setGeometry(self._rect)
        self.bar.place_on(self._rect, self._bar_offset)
        self._asked.clear()
        self._tracker.reset()
        self._dialogue = DialogueArea()
        self.overlay.clear()
        self._image = self._qimage = None
        self.request_reset.emit()

    # ---------- захват ----------
    def capture(self, *, force: bool) -> None:
        if self._stopped or self._paused or self._hidden_away:
            return
        if self._busy:
            # прошлый кадр ещё распознаётся — снимем следующий сразу после него
            self._again = bool(self._again) or force
            return
        _engine, _src, _dst, lang = self._settings()
        self._req += 1
        self._busy = True
        slots = self._tracker.slots
        self._watch_ids = [s.id for s in slots]
        watch = [s.rect for s in slots]
        area = self._dialogue.rect
        if area is not None:
            # окно диалога — под особым присмотром, даже когда оно пустое
            x, y, w, h = area
            m = DIALOGUE_MARGIN
            self._watch_ids.append(None)
            watch.append((x - m, y - m, w + 2 * m, h + 2 * m))
        self.request_capture.emit(self._req, *self._phys, lang, force, watch)

    def _done(self) -> None:
        """Кадр обработан: если пока ждали, попросили ещё один — снять его."""
        self._busy = False
        if self._again is not None:
            force, self._again = self._again, None
            self.capture(force=force)

    def _on_advance(self, kind: str, pos: QPoint) -> None:
        """Игрок перелистнул реплику (клик / Пробел / Enter)."""
        if self._paused or self._hidden_away:
            return
        if kind == "mouse" and not self._rect.contains(pos):
            return  # клик мимо игры (другой монитор, панель задач)
        if (
            kind == "mouse"
            and self.bar.isVisible()
            and self.bar.geometry().contains(pos)
        ):
            return  # клик по своей панели (она не забирает фокус у игры)
        if kind == "key" and self._hwnd:
            fg = foreground_window()
            if fg != self._hwnd and window_pid(fg) != self._game_pid:
                return  # печатают в другом окне — игра тут ни при чём
        self._click_at = time.monotonic()
        self._click_retried = False
        self._dialogue.clicked()
        # реплика сейчас сменится — её перевод гаснет сразу, подписи кнопок остаются
        self.overlay.hide_slots(
            s.id for s in self._tracker.slots if s.dst and self._is_dialogue(s)
        )
        QTimer.singleShot(CLICK_DELAY_MS, lambda: self.capture(force=True))

    def _is_dialogue(self, slot) -> bool:
        """Реплика: в найденном окне диалога, а пока оно не найдено — по виду."""
        if self._dialogue.rect is not None:
            return self._dialogue.contains(slot.rect)
        return _dialogue_like(slot.block, self._phys[2])

    def _clicked_recently(self) -> bool:
        return time.monotonic() - self._click_at < CLICK_SETTLE_S

    def _maybe_retry_click(self) -> bool:
        """После клика текст ещё не сменился (медленный переход) — глянуть ещё раз."""
        if self._clicked_recently() and not self._click_retried:
            self._click_retried = True
            QTimer.singleShot(CLICK_RETRY_MS, lambda: self.capture(force=True))
            return True
        return False

    # ---------- ответы захвата ----------
    def _on_dirty(self, req_id: int, indices) -> None:
        if req_id != self._req:
            return
        ids = [
            self._watch_ids[i]
            for i in indices
            if i < len(self._watch_ids) and self._watch_ids[i] is not None
        ]
        self.overlay.hide_slots(ids)  # под ними меняется картинка — гасим заранее

    def _on_same(self, req_id: int, identical: bool) -> None:
        if req_id != self._req:
            return
        if identical:
            if self._tracker.pending:
                self._tracker.settle()  # кадр не менялся — текст допечатан
                self._translate_ready()
            if self.overlay.hidden_ids() and not self._maybe_retry_click():
                self.overlay.unhide()  # клик ничего не сменил — вернуть перевод
        self._idle_status()
        self._done()

    def _on_capture_failed(self, req_id: int, error: str) -> None:
        if req_id != self._req:
            return
        self._status(error, "error")
        self._done()

    def _on_frame(self, req_id: int, blocks, image) -> None:
        if req_id != self._req:
            return
        if self._paused or self._stopped:
            self._done()
            return
        self._image, self._qimage = image, None
        before = {s.id: s.text for s in self._tracker.slots}
        self._tracker.update(list(blocks), time.monotonic())
        if time.monotonic() - self._click_at < DIALOGUE_LEARN_S:
            for s in self._tracker.slots:
                if before.get(s.id) != s.text:
                    self._dialogue.changed(s.block)  # учимся, где окно диалога
        if not blocks:
            self.overlay.clear()
        self._render()
        if self.overlay.hidden_ids() and not self._maybe_retry_click():
            self.overlay.unhide()  # оставшиеся на месте блоки не сменились
        if self._tracker.pending:
            QTimer.singleShot(STABLE_RECHECK_MS, lambda: self.capture(force=True))
        self._translate_ready()
        self._idle_status()
        self._done()

    # ---------- перевод ----------
    def _translate_ready(self) -> None:
        """Перевести устоявшиеся тексты, у которых ещё нет перевода."""
        if self._stopped or self._paused:
            return
        slots = self._tracker.to_translate()
        if not slots:
            return
        engine, source, target, _lang = self._settings()
        self._tr_req += 1
        self._asked[self._tr_req] = [(s.id, s.text) for s in slots]
        for s in slots:
            s.asked = s.text
        self.request_translate.emit(
            self._tr_req, [s.block for s in slots], engine, source, target
        )
        self._idle_status()

    def _on_translated(self, req_id: int, texts) -> None:
        asked = self._asked.pop(req_id, None)
        if asked is None or self._stopped:
            return  # устарел: язык сменили или сеанс перечитан
        if self._tracker.apply(asked, list(texts)):
            self._render()
        self._idle_status()

    def _on_translate_failed(self, req_id: int, error: str) -> None:
        asked = self._asked.pop(req_id, None)
        if asked is None or self._stopped:
            return
        for sid, text in asked:
            slot = self._tracker.find(sid)
            if slot is not None and slot.asked == text:
                slot.asked = ""  # попробовать ещё раз
        self._status(error, "error")
        self._retry.start()

    # ---------- отрисовка ----------
    def _render(self) -> None:
        if self._image is not None and self._qimage is None:
            self._qimage = pil_to_qimage(self._image, self._rect.width())
        self.overlay.sync(
            [(s.id, s.block, s.dst) for s in self._tracker.slots if s.dst],
            self._qimage,
        )
