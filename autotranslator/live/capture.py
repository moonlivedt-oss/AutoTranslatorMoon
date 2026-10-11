"""Захват экрана для живого перевода: снять кадр, понять, что изменилось, распознать.

Живёт в своём потоке — OCR не ждёт сетевой перевод и не мешает окну.

Как решается, распознавать ли кадр (OCR всего экрана — самое дорогое):
- кадр сравнивается с кадром ПРОШЛОГО РАСПОЗНАВАНИЯ по уменьшенной серой копии
  (дёшево: доли миллисекунды), мелкий шум (сжатие, мерцание) отсекается порогом;
- ничего не изменилось — OCR не нужен (новелла ждёт клика);
- изменилось там, где сейчас лежит текст, или сменилась сцена (крупная доля
  кадра), или это перепроверка после клика — распознаём сразу;
- изменилось только в стороне (анимированный фон, мигающий спрайт, частицы) —
  распознаём не чаще раза в ``IDLE_OCR_S``: новый текст там всё равно найдётся,
  но анимация не грузит процессор полным OCR каждые полсекунды.

Заодно воркер сразу (до OCR) сообщает, под какими блоками перевода картинка
поменялась, — их перевод гаснет мгновенно, а не после распознавания.
"""

from __future__ import annotations

import time

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

SIG_SCALE = 4  # во сколько раз уменьшать кадр для сравнения
NOISE = 20  # разница яркости (из 255) меньше этой — не изменение
SCENE_SHARE = 0.2  # изменилось больше этой доли кадра — новая сцена
IDLE_OCR_S = 1.5  # распознавать «изменения в стороне» не чаще


class CaptureWorker(QObject):
    frame = pyqtSignal(int, object, object)  # req_id, list[ocr.Block], PIL.Image
    # req_id, кадр тот же самый (True) или OCR отложен — изменения только в стороне
    same = pyqtSignal(int, bool)
    dirty = pyqtSignal(int, object)  # req_id, list[int] — индексы изменившихся рамок
    failed = pyqtSignal(int, str)

    def __init__(self) -> None:
        super().__init__()
        self._ref = None  # уменьшенный серый кадр последнего распознавания
        self._ref_area: tuple | None = None
        self._last_ocr = 0.0
        self._picker = None  # подбор языка OCR: свой у потока захвата

    @pyqtSlot()
    def reset(self) -> None:
        self._ref = None
        self._ref_area = None

    @staticmethod
    def signature(image):
        return image.convert("L").reduce(SIG_SCALE)

    @staticmethod
    def changes(ref, sig, watch: list[tuple[float, float, float, float]]):
        """(доля изменившихся пикселей, индексы изменившихся рамок из watch)."""
        from PIL import ImageChops

        mask = ImageChops.difference(ref, sig).point(lambda v: 255 if v > NOISE else 0)
        if mask.getbbox() is None:
            return 0.0, []
        share = mask.histogram()[255] / max(1, sig.width * sig.height)
        hit = []
        k = SIG_SCALE
        for i, (x, y, w, h) in enumerate(watch):
            box = (
                max(0, int(x // k)),
                max(0, int(y // k)),
                min(sig.width, int((x + w) // k) + 1),
                min(sig.height, int((y + h) // k) + 1),
            )
            if box[2] > box[0] and box[3] > box[1] and mask.crop(box).getbbox():
                hit.append(i)
        return max(share, 1e-9), hit

    @pyqtSlot(int, int, int, int, int, str, bool, object)
    def capture(self, req_id, left, top, width, height, lang_tag, force, watch) -> None:
        """Снять область; если есть что распознавать — найти блоки текста.

        watch — рамки блоков (физ. пиксели кадра), за которыми следим особо;
        force — перепроверка после клика или «допечатки»: любое изменение
        распознаётся сразу, без ограничения частоты.
        """
        try:
            from ..services import ocr

            image = ocr.grab_region(left, top, width, height)
            sig = self.signature(image)
            area = (left, top, width, height, lang_tag)
            if self._ref is not None and self._ref_area == area:
                share, hit = self.changes(self._ref, sig, watch or [])
                if share == 0.0:
                    self.same.emit(req_id, True)
                    return
                if hit:
                    self.dirty.emit(req_id, hit)
                urgent = force or hit or share >= SCENE_SHARE
                if not urgent and time.monotonic() - self._last_ocr < IDLE_OCR_S:
                    self.same.emit(req_id, False)  # анимация в стороне — позже
                    return
            if self._picker is None:
                self._picker = ocr.LangPicker()
            blocks = ocr.ocr_blocks(
                image, lang_tag or None, enhance=False, picker=self._picker
            )
            self._ref, self._ref_area = sig, area
            self._last_ocr = time.monotonic()
            self.frame.emit(req_id, blocks, image)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(req_id, "Ошибка OCR: " + str(e))
