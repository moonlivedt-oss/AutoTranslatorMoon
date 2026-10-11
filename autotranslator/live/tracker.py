"""Слежение за блоками текста между кадрами живого перевода (без Qt — легко тестировать).

Раньше живой перевод считал экран одним целым: пока менялся хоть один блок
(часы в углу, «дрожащая» надпись на фоне), ждал всю «допечатку» до 3 секунд и
переводил всё заново. Теперь у каждого блока своя судьба:

- блок сопоставляется с блоком прошлого кадра по месту (перекрытие рамок), так
  что «печатающаяся» реплика остаётся тем же блоком, пока растёт;
- блок переводится, когда его текст устоялся (одинаков в двух кадрах подряд или
  кадр вовсе не менялся), не дожидаясь остальных;
- дрожание OCR («Hello,» / «Hello.», «Hel1o») не сбрасывает готовый перевод;
- новый текст на месте старого сразу снимает старый перевод — он не висит над
  новой репликой.
"""

from __future__ import annotations

import itertools
from collections import deque
from dataclasses import dataclass, field

from ..core.textutil import norm, same_text

STABLE_MAX_S = 3.0  # дольше «печатающийся» текст не ждём — переводим как есть
MATCH_OVERLAP = 0.5  # доля меньшей рамки, которую должна перекрыть пара блоков

_ids = itertools.count(1)


@dataclass
class Slot:
    """Место на экране, где живёт блок текста, и его перевод."""

    block: object  # services.ocr.Block (физические пиксели кадра)
    since: float  # когда текст начал меняться (для предела «допечатки»)
    stable: bool = False  # текст устоялся — можно переводить
    dst: str | None = None  # перевод (None — ещё нет)
    dst_for: str = ""  # для какого текста этот перевод
    asked: str = ""  # текст, перевод которого уже запрошен
    id: int = field(default_factory=lambda: next(_ids))

    @property
    def text(self) -> str:
        return self.block.text

    @property
    def rect(self) -> tuple[float, float, float, float]:
        b = self.block
        return b.left, b.top, b.width, b.height

    @property
    def needs_translation(self) -> bool:
        return self.stable and self.dst is None and self.asked != self.text


def _overlap(a: tuple, b: tuple, pad: float) -> float:
    """Доля меньшей рамки, перекрытая другой (рамки чуть расширены по вертикали)."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ay, ah, by, bh = ay - pad, ah + 2 * pad, by - pad, bh + 2 * pad
    w = min(ax + aw, bx + bw) - max(ax, bx)
    h = min(ay + ah, by + bh) - max(ay, by)
    if w <= 0 or h <= 0:
        return 0.0
    return (w * h) / max(1.0, min(aw * ah, bw * bh))


class BlockTracker:
    def __init__(self) -> None:
        self.slots: list[Slot] = []

    def reset(self) -> None:
        self.slots = []

    def forget_translations(self) -> None:
        """Сменили язык/движок: тексты те же, переводы — заново."""
        for s in self.slots:
            s.dst, s.dst_for, s.asked = None, "", ""

    def update(self, blocks: list, now: float) -> None:
        """Новый кадр OCR: сопоставить блоки с прошлыми и обновить их состояние."""
        free = list(self.slots)
        fresh: list[Slot] = []
        for b in blocks:
            pad = 0.5 * max(1.0, b.line_h)
            rect = (b.left, b.top, b.width, b.height)
            best, score = None, MATCH_OVERLAP
            for s in free:
                o = _overlap(s.rect, rect, pad)
                if o >= score:
                    best, score = s, o
            if best is None:
                fresh.append(Slot(b, since=now))
                continue
            free.remove(best)
            fresh.append(best)
            self._advance(best, b, now)
        self.slots = fresh

    @staticmethod
    def _advance(slot: Slot, block, now: float) -> None:
        old = slot.text
        slot.block = block
        if slot.dst is not None and same_text(block.text, slot.dst_for):
            slot.stable = True  # дрожание OCR — перевод остаётся
            return
        if norm(block.text) == norm(old):
            slot.stable = True  # два кадра подряд одно и то же — допечатано
            return
        # текст другой: старый перевод больше не про него
        if slot.stable or slot.dst is not None:
            slot.since = now
        slot.dst, slot.dst_for, slot.asked = None, "", ""
        slot.stable = now - slot.since > STABLE_MAX_S

    def settle(self) -> None:
        """Кадр не менялся с прошлого распознавания — все тексты устоялись."""
        for s in self.slots:
            s.stable = True

    @property
    def pending(self) -> bool:
        """Есть текст, который ещё «печатается» (стоит перепроверить кадр)."""
        return any(not s.stable for s in self.slots)

    def to_translate(self) -> list[Slot]:
        return [s for s in self.slots if s.needs_translation]

    def apply(self, asked: list[tuple[int, str]], texts: list[str]) -> bool:
        """Пришёл перевод: раздать слотам, чей текст с тех пор не сменился."""
        by_id = {s.id: s for s in self.slots}
        changed = False
        for (sid, src), dst in zip(asked, texts, strict=False):
            s = by_id.get(sid)
            if s is None or s.text != src:
                continue  # слот пропал или текст уже другой — перевод устарел
            s.dst, s.dst_for = (dst or "").strip(), src
            changed = True
        return changed

    def find(self, slot_id: int) -> Slot | None:
        return next((s for s in self.slots if s.id == slot_id), None)


class DialogueArea:
    """Где у игры окно диалога — место, где текст меняется от кликов.

    После каждого клика запоминаем самый длинный текст, который сменился. Когда
    два последних таких места совпадают, окно диалога найдено: по клику гаснет
    именно его перевод (а не всё «похожее на реплику»), и за этим местом захват
    следит особо — новая реплика распознаётся сразу, даже если до неё окно
    диалога было пустым.
    """

    KEEP = 4  # помним столько последних кликов

    def __init__(self) -> None:
        self._clicks: deque[tuple | None] = deque(maxlen=self.KEEP)

    def clicked(self) -> None:
        self._clicks.append(None)

    def changed(self, block) -> None:
        """После последнего клика сменился этот текст (берём самый длинный)."""
        if not self._clicks or len(block.text.split()) < 2:
            return
        cur = self._clicks[-1]
        rect = (block.left, block.top, block.width, block.height)
        if cur is None or len(block.text) > cur[1]:
            self._clicks[-1] = (rect, len(block.text))

    @property
    def rect(self) -> tuple[float, float, float, float] | None:
        seen = [c[0] for c in self._clicks if c is not None]
        if len(seen) < 2:
            return None
        last = seen[-1]
        same = [r for r in seen if _overlap(r, last, 0.0) >= 0.3]
        if len(same) < 2:
            return None
        x0 = min(r[0] for r in same)
        y0 = min(r[1] for r in same)
        x1 = max(r[0] + r[2] for r in same)
        y1 = max(r[1] + r[3] for r in same)
        return x0, y0, x1 - x0, y1 - y0

    def contains(self, rect: tuple) -> bool:
        area = self.rect
        return area is not None and _overlap(rect, area, 0.0) >= 0.5
