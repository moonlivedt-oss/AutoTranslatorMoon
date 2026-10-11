"""OCR области экрана через встроенный Windows.Media.Ocr (winsdk) + захват через mss.

Языковые пакеты (en-US, ru, ja, ko, zh-Hans …) должны стоять в системе. Всё
завёрнуто в синхронные функции: WinRT-корутины гоняем через asyncio.run.

Перед распознаванием картинку можно **предобработать** (увеличить, обесцветить,
поднять контраст) — мелкий экранный текст так узнаётся заметно точнее.

После распознавания строки **склеиваются в абзацы** по геометрии (``reflow``):
переводчику нужен связный текст, а не обрывки строк, перенесённых вёрсткой.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import re
import threading
import time
from typing import NamedTuple

from PIL import Image, ImageOps

from ..core.textutil import text_quality

log = logging.getLogger(__name__)

# Иероглифы/кана/хангыль — между ними пробелы не нужны (Windows OCR их вставляет).
_CJK = r"぀-ヿ㐀-䶿一-鿿가-힯＀-￯　-〿"
_CJK_SPACE = re.compile(rf"(?<=[{_CJK}]) +(?=[{_CJK}])")
_CJK_CHAR = re.compile(rf"[{_CJK}]")
_BULLET = re.compile(r"^\s*(?:[-–—•·*▪►]|\d{1,3}[.)])\s")
_END_PUNCT = tuple('.!?:;…。！？」』"»)')
_LETTER = re.compile(r"[^\W\d_]")


class OcrLine(NamedTuple):
    text: str
    left: float
    top: float
    width: float
    height: float


def grab_region(left: int, top: int, width: int, height: int) -> Image.Image:
    """Скриншот прямоугольной области экрана -> PIL.Image (RGB).

    Координаты — **физические** пиксели (как их видит mss). Пересчёт из
    логических Qt-координат делает вызывающая сторона по DPI-масштабу экрана.
    """
    import mss

    factory = getattr(mss, "MSS", None) or mss.mss  # mss.mss устарел в новых версиях
    with factory() as sct:
        shot = sct.grab({"left": left, "top": top, "width": width, "height": height})
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


def image_digest(image: Image.Image) -> bytes:
    """Короткий отпечаток пикселей — чтобы не распознавать заново тот же кадр."""
    return hashlib.blake2b(image.tobytes(), digest_size=16).digest()


def preprocess(image: Image.Image, scale: float = 2.0) -> Image.Image:
    """Увеличивает и нормализует картинку под OCR.

    - апскейл (LANCZOS) — мелкий шрифт становится крупнее и чётче;
    - оттенки серого + автоконтраст — убирает фон и подсветку, оставляя буквы.
    Крупные картинки не увеличиваем: Windows OCR ограничен ~10000 px по стороне,
    а точности большой текст от апскейла не прибавляет.
    """
    w, h = image.size
    if max(w, h) * scale > 4000:
        scale = max(1.0, 4000 / max(w, h))
    if scale < 1.3:
        scale = 1.0  # прибавка в пару процентов только тратит время на ресайз
    if scale != 1.0:
        image = image.resize(
            (max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS
        )
    gray = ImageOps.grayscale(image)
    return ImageOps.autocontrast(gray, cutoff=2)


_MAX_DIM: int | None = None


def max_image_dimension() -> int:
    """Предел стороны картинки для Windows OCR (кэшируется; 10000 — если не узнать)."""
    global _MAX_DIM
    if _MAX_DIM is None:
        try:
            from winsdk.windows.media.ocr import OcrEngine

            _MAX_DIM = int(OcrEngine.max_image_dimension) or 10000
        except Exception:  # noqa: BLE001
            _MAX_DIM = 10000
    return _MAX_DIM


_AVAILABLE: list[str] | None = None


def available_ocr_languages() -> list[str]:
    """Языки, для которых в Windows стоит OCR (кэшируется; пусто — не узнать)."""
    global _AVAILABLE
    if _AVAILABLE is None:
        try:
            from winsdk.windows.media.ocr import OcrEngine

            _AVAILABLE = [
                lang.language_tag for lang in OcrEngine.available_recognizer_languages
            ]
        except Exception:  # noqa: BLE001
            _AVAILABLE = []
    return _AVAILABLE


# ---------- склейка строк в абзацы -------------------------------------------
def clean_line(text: str) -> str:
    """Убирает пробелы, которые Windows OCR ставит между CJK-символами."""
    return _CJK_SPACE.sub("", text.strip())


def _joiner(prev: str, nxt: str) -> tuple[str, str]:
    """Как соединить две строки одного абзаца: (новый prev, разделитель)."""
    # перенос по слогам: «перево-» + «дчик» -> «переводчик»
    if (
        len(prev) > 2
        and prev.endswith("-")
        and prev[-2].isalpha()
        and nxt[:1].islower()
    ):
        return prev[:-1], ""
    if _CJK_CHAR.match(prev[-1:]) or _CJK_CHAR.match(nxt[:1]):
        return prev, ""
    return prev, " "


def reflow(lines: list[OcrLine]) -> str:
    """Собирает распознанные строки в абзацы по их геометрии.

    Строки одного абзаца склеиваются (вёрстка переносит их по ширине, а не по
    смыслу — переводчику нужны целые предложения). Новый абзац начинается, если:
    - между строками заметный вертикальный зазор;
    - предыдущая строка закончилась заметно раньше правого края (и пунктуацией);
    - строка похожа на пункт списка.
    """
    lines = [ln._replace(text=clean_line(ln.text)) for ln in lines]
    lines = [ln for ln in lines if ln.text]
    if not lines:
        return ""
    heights = sorted(ln.height for ln in lines)
    med_h = heights[len(heights) // 2] or 1.0
    right_edge = max(ln.left + ln.width for ln in lines)
    left_edge = min(ln.left for ln in lines)
    full_w = max(1.0, right_edge - left_edge)

    paragraphs: list[str] = []
    cur = lines[0].text
    for prev, ln in zip(lines, lines[1:], strict=False):
        gap = ln.top - (prev.top + prev.height)
        short = (prev.left + prev.width - left_edge) < 0.7 * full_w
        new_par = (
            gap > 0.9 * med_h
            or (short and prev.text.endswith(_END_PUNCT))
            or (short and len(lines) > 2 and prev.width < 0.4 * full_w)
            or bool(_BULLET.match(ln.text))
            or abs(ln.height - prev.height) > 0.6 * med_h  # смена кегля: заголовок
        )
        if new_par:
            paragraphs.append(cur)
            cur = ln.text
        else:
            cur, sep = _joiner(cur, ln.text)
            cur = cur + sep + ln.text
    paragraphs.append(cur)
    return "\n".join(paragraphs)


# ---------- распознавание ----------------------------------------------------
_engines: dict = {}
# OCR зовут из нескольких потоков (окно, живой перевод); движки общие, а их
# потокобезопасность WinRT не обещает — распознаём по одному (OCR и так грузит CPU)
_LOCK = threading.Lock()


def _get_engine(lang_tag: str | None):
    """OcrEngine под язык (кэшируется: создание движка не бесплатное)."""
    from winsdk.windows.globalization import Language
    from winsdk.windows.media.ocr import OcrEngine

    if lang_tag in _engines:
        return _engines[lang_tag]
    engine = None
    if lang_tag:
        engine = OcrEngine.try_create_from_language(Language(lang_tag))
    if engine is None:
        engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        raise RuntimeError(
            "Не удалось создать OCR-движок. Установите языковой пакет "
            "в Параметрах Windows (Время и язык → Язык)."
        )
    _engines[lang_tag] = engine
    return engine


async def _recognize(png_bytes: bytes, lang_tag: str | None) -> list[OcrLine]:
    from winsdk.windows.graphics.imaging import BitmapDecoder
    from winsdk.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream.get_output_stream_at(0))
    writer.write_bytes(png_bytes)
    await writer.store_async()
    await writer.flush_async()
    stream.seek(0)

    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    result = await _get_engine(lang_tag).recognize_async(bitmap)

    out: list[OcrLine] = []
    for line in result.lines:
        rects = [w.bounding_rect for w in line.words]
        if rects:
            x0 = min(r.x for r in rects)
            y0 = min(r.y for r in rects)
            x1 = max(r.x + r.width for r in rects)
            y1 = max(r.y + r.height for r in rects)
        else:
            x0 = y0 = x1 = y1 = 0.0
        out.append(OcrLine(line.text, x0, y0, x1 - x0, y1 - y0))
    return out


def recognize_lines(
    image: Image.Image, lang_tag: str | None = None, *, enhance: bool = True
) -> list[OcrLine]:
    """Строки OCR с координатами в пикселях ИСХОДНОЙ картинки (до апскейла)."""
    proc = preprocess(image) if enhance else image
    # Windows OCR не берёт картинки больше max_image_dimension (обычно 10000 px):
    # широкий снимок нескольких мониторов / 8K иначе молча не распознаётся
    limit = max_image_dimension()
    if max(proc.size) > limit:
        k_down = limit / max(proc.size)
        proc = proc.resize(
            (max(1, int(proc.width * k_down)), max(1, int(proc.height * k_down))),
            Image.LANCZOS,
        )
    buf = io.BytesIO()
    proc.save(buf, format="PNG")
    with _LOCK:
        lines = asyncio.run(_recognize(buf.getvalue(), lang_tag))
    k = image.width / max(1, proc.width)
    if abs(k - 1.0) > 1e-6:
        lines = [
            OcrLine(ln.text, ln.left * k, ln.top * k, ln.width * k, ln.height * k)
            for ln in lines
        ]
    return lines


def ocr_image(
    image: Image.Image,
    lang_tag: str | None = None,
    *,
    enhance: bool = True,
    merge_lines: bool = True,
) -> str:
    """Распознаёт текст на картинке. lang_tag, например 'ru' или 'en-US'."""
    lines = recognize_lines(image, lang_tag, enhance=enhance)
    if merge_lines:
        return reflow(lines)
    return "\n".join(t for t in (clean_line(ln.text) for ln in lines) if t)


# ---------- блоки текста для экранного перевода -------------------------------
class Block(NamedTuple):
    """Связный кусок текста на экране (реплика, пункт меню, подпись)."""

    text: str  # абзацы через перевод строки
    left: float
    top: float
    width: float
    height: float
    line_h: float  # типичная высота строки — от неё подбирается кегль перевода
    bg: tuple[int, int, int]  # цвет фона вокруг текста — им закрашиваем оригинал
    align: str = "left"  # left | center | right — как выровнены строки оригинала


def _span(lines: list[OcrLine]) -> tuple[float, float]:
    return min(ln.left for ln in lines), max(ln.left + ln.width for ln in lines)


def group_blocks(lines: list[OcrLine]) -> list[list[OcrLine]]:
    """Раскладывает строки всего экрана по блокам.

    Строка попадает в блок, если она идёт сразу под его последней строкой
    (зазор меньше высоты строки), того же кегля и перекрывается с ним по
    горизонтали (или выровнена по тому же левому краю). Иначе — новый блок:
    так реплика, имя персонажа, пункты меню и кнопки интерфейса переводятся
    и рисуются раздельно, каждая на своём месте.
    """
    lines = [ln._replace(text=clean_line(ln.text)) for ln in lines]
    lines = sorted((ln for ln in lines if ln.text), key=lambda ln: (ln.top, ln.left))
    blocks: list[list[OcrLine]] = []
    for ln in lines:
        target = None
        for b in reversed(blocks):
            last = b[-1]
            h = max(1.0, min(last.height, ln.height))
            gap = ln.top - (last.top + last.height)
            if gap > 1.0 * h or gap < -0.6 * h:
                continue
            if abs(ln.height - last.height) > 0.45 * max(last.height, ln.height):
                continue
            x0, x1 = _span(b)
            overlap = min(x1, ln.left + ln.width) - max(x0, ln.left)
            if overlap < 0.25 * min(ln.width, x1 - x0) and abs(ln.left - x0) > 1.5 * h:
                continue
            target = b
            break
        if target is None:
            blocks.append([ln])
        else:
            target.append(ln)
    return blocks


def is_noise(text: str) -> bool:
    """Переводить нечего: цифры, время, значки, одиночная буква.

    Такие блоки на экране (часы, счётчики, «×», номера страниц) только
    захламляют перевод — их оставляем как есть.
    """
    letters = _LETTER.findall(text)
    if any(_CJK_CHAR.match(ch) for ch in letters):
        return False  # один иероглиф — уже слово
    return len(letters) < 2


def block_alignment(lines: list[OcrLine]) -> str:
    """Выравнивание абзаца по геометрии строк: левые края, правые или центры.

    Для одной строки судить не по чему — считаем, что по левому краю.
    """
    if len(lines) < 2:
        return "left"
    h = max(1.0, sorted(ln.height for ln in lines)[len(lines) // 2])
    lefts = [ln.left for ln in lines]
    rights = [ln.left + ln.width for ln in lines]
    mids = [ln.left + ln.width / 2 for ln in lines]

    def spread(xs: list[float]) -> float:
        return max(xs) - min(xs)

    tol = 0.6 * h
    if spread(lefts) <= tol:
        return "left"  # и у выключки по ширине левые края ровные
    if spread(mids) <= tol:
        return "center"
    if spread(rights) <= tol:
        return "right"
    return "left"


def _edge_color(
    image: Image.Image, x0: int, y0: int, x1: int, y1: int
) -> tuple[int, int, int]:
    """Медианный цвет тонкой каймы вокруг прямоугольника — это фон под текстом."""
    w, h = image.size
    pad = 3
    x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
    x1, y1 = min(w, x1 + pad), min(h, y1 + pad)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return (30, 30, 46)
    strips = (
        (x0, y0, x1, y0 + 2),
        (x0, y1 - 2, x1, y1),
        (x0, y0, x0 + 2, y1),
        (x1 - 2, y0, x1, y1),
    )
    px: list[tuple[int, int, int]] = []
    for box in strips:
        px.extend(image.crop(box).getdata())
    if not px:
        return (30, 30, 46)
    mid = len(px) // 2
    return tuple(sorted(c[i] for c in px)[mid] for i in range(3))  # type: ignore[return-value]


GARBAGE_QUALITY = 0.4  # блок с «осмысленностью» ниже — шум фона, не текст
GARBAGE_MIN_LETTERS = 6  # короткие подписи так не судим: мало букв для вывода


def is_garbage(text: str) -> bool:
    """Мусор OCR: узор фона или текст, прочитанный не тем алфавитом."""
    letters = sum(ch.isalpha() for ch in text)
    return letters >= GARBAGE_MIN_LETTERS and text_quality(text) < GARBAGE_QUALITY


def blocks_quality(blocks: list[Block]) -> float:
    """Осмысленность всего кадра (по буквам). Мало текста — судить не по чему: 1."""
    total = good = 0.0
    for b in blocks:
        n = sum(ch.isalpha() for ch in b.text)
        total += n
        good += n * text_quality(b.text)
    return good / total if total >= 20 else 1.0


def _raw_blocks(image: Image.Image, lang_tag: str | None, enhance: bool) -> list[Block]:
    rgb = image.convert("RGB")
    out: list[Block] = []
    for group in group_blocks(recognize_lines(rgb, lang_tag, enhance=enhance)):
        text = reflow(group)
        if not text.strip() or is_noise(text):
            continue
        x0, x1 = _span(group)
        y0 = min(ln.top for ln in group)
        y1 = max(ln.top + ln.height for ln in group)
        heights = sorted(ln.height for ln in group)
        bg = _edge_color(rgb, int(x0), int(y0), int(x1) + 1, int(y1) + 1)
        out.append(
            Block(
                text,
                x0,
                y0,
                x1 - x0,
                y1 - y0,
                heights[len(heights) // 2],
                bg,
                block_alignment(group),
            )
        )
    return out


class LangPicker:
    """Подбирает язык OCR под то, что на самом деле на экране.

    Windows OCR читает только «свой» алфавит: русский текст, распознанный
    английским движком, превращается в «pa60Tana nepeB0A». Если кадр выглядит
    мусором, пробуем другие установленные языки OCR и запоминаем победителя —
    следующие кадры сразу читаются им. Неудачную попытку не повторяем каждый
    кадр (мусор бывает и просто на фоне): не чаще раза в ``RETRY_S``.
    """

    GOOD = 0.7  # кадр с осмысленностью выше — язык подходит, ничего не ищем
    WIN_BY = 0.15  # другой язык должен быть заметно лучше
    RETRY_S = 20.0

    def __init__(self) -> None:
        self._best: dict[str | None, str | None] = {}
        self._tried: dict[tuple, float] = {}

    def chosen(self, lang_tag: str | None) -> str | None:
        return self._best.get(lang_tag, lang_tag)

    def run(self, lang_tag: str | None, recognize, quality):
        """recognize(tag) -> результат, quality(результат) -> 0..1."""
        tag = self.chosen(lang_tag)
        result = recognize(tag)
        score = quality(result)
        now = time.monotonic()
        if score >= self.GOOD and tag != lang_tag:
            # читаем «чужим» языком — изредка проверить, не вернулся ли на экран
            # текст выбранного (сменили игру/сцену): тогда вернуться к нему
            if now - self._tried.get((lang_tag, "back"), -1e9) >= self.RETRY_S:
                self._tried[(lang_tag, "back")] = now
                own = recognize(lang_tag)
                if quality(own) >= score - 0.02:
                    log.info("OCR: снова читаю языком %s", lang_tag or "профиля")
                    self._best[lang_tag] = lang_tag
                    return own
            return result
        if (
            score >= self.GOOD
            or now - self._tried.get((lang_tag, tag), -1e9) < self.RETRY_S
        ):
            return result
        self._tried[(lang_tag, tag)] = now
        others = [lang_tag, *available_ocr_languages()]
        for alt in dict.fromkeys(others):  # без повторов, порядок сохраняем
            if alt == tag:
                continue
            res2 = recognize(alt)
            q2 = quality(res2)
            if q2 >= score + self.WIN_BY:
                result, score, tag = res2, q2, alt
        if tag != self.chosen(lang_tag):
            log.info(
                "OCR: язык %s читает экран лучше, чем %s", tag, lang_tag or "профиль"
            )
        self._best[lang_tag] = tag
        self._tried[(lang_tag, "back")] = now  # обратно проверим не сразу
        return result


def ocr_blocks(
    image: Image.Image,
    lang_tag: str | None = None,
    *,
    enhance: bool = True,
    picker: LangPicker | None = None,
    drop_garbage: bool = True,
) -> list[Block]:
    """Распознаёт картинку целиком и возвращает блоки текста с координатами.

    picker — подбирать язык OCR, если с заданным получается мусор;
    drop_garbage — выбросить блоки-мусор (узоры фона, нечитаемое).
    """
    if picker is None:
        blocks = _raw_blocks(image, lang_tag, enhance)
    else:
        blocks = picker.run(
            lang_tag, lambda tag: _raw_blocks(image, tag, enhance), blocks_quality
        )
    if drop_garbage:
        blocks = [b for b in blocks if not is_garbage(b.text)]
    return blocks


def ocr_region(
    left: int,
    top: int,
    width: int,
    height: int,
    lang_tag: str | None = None,
    *,
    enhance: bool = True,
    merge_lines: bool = True,
) -> str:
    return ocr_image(
        grab_region(left, top, width, height),
        lang_tag,
        enhance=enhance,
        merge_lines=merge_lines,
    )
