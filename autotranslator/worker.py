"""Фоновый воркер окна: держит движки, переводит и распознаёт по запросу.

Живёт в отдельном QThread — окно не подвисает ни на сети, ни на OCR. Запросы
приходят Qt-сигналами, ответы уходят сигналами же; у каждого запроса номер,
чтобы окно отбрасывало устаревшие ответы.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from .core.langs import AUTO
from .core.translate import NetworkError

log = logging.getLogger(__name__)

OCR_ENHANCE, OCR_MERGE, OCR_SKIP_SAME = 1, 2, 4
# экранный перевод по частям: сначала блоки от стольких символов (реплики, абзацы),
# мелочь (кнопки, подписи) — вторым запросом. Делим, только если блоков много.
LENS_BIG_CHARS = 25
LENS_SPLIT_MIN_BLOCKS = 6


class Worker(QObject):
    """Живёт в фоновом потоке: держит движки, делает перевод и OCR по запросу."""

    # req_id, перевод, определённый язык источника ("" — неизвестно),
    # каким движком на самом деле переведено ("" — выбранным, "offline" — подстраховка)
    translated = pyqtSignal(int, str, str, str)
    ocr_done = pyqtSignal(int, str)
    ocr_same = pyqtSignal(int)  # кадр не изменился — OCR не запускали
    failed = pyqtSignal(int, str, str)  # req_id, kind ("translate"/"ocr"), текст
    lens_blocks = pyqtSignal(int, object)  # req_id, list[ocr.Block]
    lens_translated = pyqtSignal(
        int, object
    )  # req_id, list[str] — перевод каждого блока
    lens_failed = pyqtSignal(int, str)
    # req_id, list[str] — уже готовые блоки ("" — ещё переводится)
    lens_partial = pyqtSignal(int, object)
    # живой перевод (захват и OCR — в live/capture.py): req_id, list[str]
    live_translated = pyqtSignal(int, object)
    live_failed = pyqtSignal(int, str)

    def __init__(self) -> None:
        super().__init__()
        self._engines: dict = {}
        self._last_shot: tuple | None = None  # (область, отпечаток пикселей)
        # нет сети — переводить офлайн, если модель пары уже стоит (флаг из окна)
        self.offline_fallback = True
        self._lang_picker = None  # подбор языка OCR (создаётся при первом OCR)

    def _engine(self, name: str):
        if name not in self._engines:
            from .core.translate import make_engine

            self._engines[name] = make_engine(name)
        return self._engines[name]

    def _fallback(self, text: str, source: str, target: str):
        """Офлайн-движок, если он установлен и переведёт без скачивания модели."""
        if not self.offline_fallback:
            return None
        try:
            engine = self._engine("offline")
        except Exception:  # noqa: BLE001 — Argos не установлен
            return None
        return engine if engine.has_model(text, source, target) else None

    @pyqtSlot(int, str, str, str, str)
    def do_translate(self, req_id, text, engine_name, source, target) -> None:
        try:
            engine = self._engine(engine_name)
            result = engine.translate(text, source, target)
            detected = engine.detected(text) if source == AUTO else None
            self.translated.emit(req_id, result, detected or "", "")
        except NetworkError as e:
            spare = (
                self._fallback(text, source, target)
                if engine_name == "online"
                else None
            )
            if spare is None:
                self.failed.emit(req_id, "translate", str(e))
                return
            try:
                result = spare.translate(text, source, target)
            except Exception as e2:  # noqa: BLE001
                log.warning("офлайн-подстраховка не удалась: %s", e2)
                self.failed.emit(req_id, "translate", str(e))
                return
            detected = spare.detected(text) if source == AUTO else None
            self.translated.emit(req_id, result, detected or "", "offline")
        except Exception as e:  # noqa: BLE001 — показываем любую ошибку в статусе
            self.failed.emit(req_id, "translate", str(e))

    @property
    def _picker(self):
        if self._lang_picker is None:
            from .services.ocr import LangPicker

            self._lang_picker = LangPicker()
        return self._lang_picker

    def _ocr_text(self, image, lang_tag: str, flags: int) -> str:
        """OCR в текст; не тот язык OCR (мусор вместо букв) — подобрать другой."""
        from .core.textutil import text_quality
        from .services import ocr

        return self._picker.run(
            lang_tag or None,
            lambda tag: ocr.ocr_image(
                image,
                tag,
                enhance=bool(flags & OCR_ENHANCE),
                merge_lines=bool(flags & OCR_MERGE),
            ),
            lambda text: text_quality(text) if len(text) >= 20 else 1.0,
        )

    @pyqtSlot(int, int, int, int, int, str, int)
    def do_ocr(self, req_id, left, top, width, height, lang_tag, flags) -> None:
        """flags: OCR_ENHANCE | OCR_MERGE | OCR_SKIP_SAME (битовая маска)."""
        try:
            from .services import ocr

            image = ocr.grab_region(left, top, width, height)
            # Авто-тик по неизменной картинке: распознавать нечего — экономим CPU.
            shot = (
                (left, top, width, height, lang_tag, flags & ~OCR_SKIP_SAME),
                ocr.image_digest(image),
            )
            if flags & OCR_SKIP_SAME and shot == self._last_shot:
                self.ocr_same.emit(req_id)
                return
            text = self._ocr_text(image, lang_tag, flags)
            self._last_shot = shot
            self.ocr_done.emit(req_id, text)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(req_id, "ocr", str(e))

    @pyqtSlot(int, object, str, int)
    def do_ocr_image(self, req_id, image, lang_tag, flags) -> None:
        """OCR готовой картинки (из буфера обмена или перетащенного файла)."""
        try:
            text = self._ocr_text(image.convert("RGB"), lang_tag, flags)
            self.ocr_done.emit(req_id, text)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(req_id, "ocr", str(e))

    @pyqtSlot(int, object, str, int, str, str, str)
    def do_lens(
        self, req_id, image, lang_tag, flags, engine_name, source, target
    ) -> None:
        """Экранный перевод: блоки текста с картинки + перевод всех блоков разом."""
        try:
            from .services import ocr

            blocks = ocr.ocr_blocks(
                image,
                lang_tag or None,
                enhance=bool(flags & OCR_ENHANCE),
                picker=self._picker,
            )
        except Exception as e:  # noqa: BLE001
            self.lens_failed.emit(req_id, "Ошибка OCR: " + str(e))
            return
        self.lens_blocks.emit(req_id, blocks)
        if blocks:
            self.do_lens_translate(req_id, blocks, engine_name, source, target)

    @pyqtSlot(int, object, str, str, str)
    def do_lens_translate(self, req_id, blocks, engine_name, source, target) -> None:
        """Перевод уже найденных блоков (и повторный — при смене языка на оверлее).

        Если блоков много, сначала одним запросом идут крупные (реплики, абзацы) —
        их видно через долю секунды, а мелочь (кнопки, подписи) дорисуется следом.
        """
        big = [i for i, b in enumerate(blocks) if len(b.text) >= LENS_BIG_CHARS]
        small = [i for i, b in enumerate(blocks) if len(b.text) < LENS_BIG_CHARS]
        if len(blocks) < LENS_SPLIT_MIN_BLOCKS or not big or not small:
            batches = [list(range(len(blocks)))]
        else:
            batches = [big, small]
        out = [""] * len(blocks)
        for n, batch in enumerate(batches):
            try:
                done = self._translate_blocks(
                    [blocks[i] for i in batch], engine_name, source, target
                )
            except Exception as e:  # noqa: BLE001
                self.lens_failed.emit(req_id, str(e))
                return
            for i, dst in zip(batch, done, strict=False):
                out[i] = dst
            if n < len(batches) - 1:
                self.lens_partial.emit(req_id, list(out))
        self.lens_translated.emit(req_id, out)

    @pyqtSlot(int, object, str, str, str)
    def do_live_translate(self, req_id, blocks, engine_name, source, target) -> None:
        """Перевод блоков живого перевода (у сеанса свой экземпляр воркера и поток)."""
        try:
            texts = self._translate_blocks(blocks, engine_name, source, target)
        except Exception as e:  # noqa: BLE001
            self.live_failed.emit(req_id, str(e))
            return
        self.live_translated.emit(req_id, texts)

    def _translate_blocks(self, blocks, engine_name, source, target) -> list[str]:
        # каждый абзац блока — отдельная строка запроса (так переводчик не склеит
        # реплику с подписью кнопки), потом собираем обратно по блокам
        units: list[str] = []
        owners: list[int] = []
        for bi, b in enumerate(blocks):
            for para in b.text.split("\n"):
                if para.strip():
                    units.append(para)
                    owners.append(bi)
        if source != AUTO and source == target:
            results = units
        else:
            try:
                results = self._engine(engine_name).translate_many(
                    units, source, target
                )
            except NetworkError:
                spare = (
                    self._fallback(" ".join(units), source, target)
                    if engine_name == "online"
                    else None
                )
                if spare is None:
                    raise
                results = spare.translate_many(units, source, target)
        per_block: list[list[str]] = [[] for _ in blocks]
        for owner, res in zip(owners, results, strict=False):
            per_block[owner].append(res)
        return ["\n".join(x) for x in per_block]
