"""Сравнение распознанного текста: дрожание OCR не должно считаться новым текстом.

OCR одного и того же кадра даёт чуть разный результат: регистр, пробелы, знаки
(«Hello,» / «Hello.»), иногда одну букву («Hel1o»). Перевод при этом заново
запрашивать незачем, а в живом переводе — и мигать им незачем.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

_NORM = re.compile(r"[\W_]+")


def norm(text: str) -> str:
    """Текст без пробелов и знаков, в нижнем регистре — ключ для сравнения."""
    return _NORM.sub("", text.lower())


def same_text(a: str, b: str) -> bool:
    """Тот же текст с точностью до дрожания OCR (пара перепутанных букв).

    Дописывание — не дрожание: «Hello th» и «Hello there» — разные тексты (так
    «печатается» реплика в новелле), даже если отличаются на пару букв.
    """
    na, nb = norm(a), norm(b)
    if na == nb:
        return True
    if not na or not nb or na.startswith(nb) or nb.startswith(na):
        return False
    longest = max(len(na), len(nb))
    if longest < 12 or abs(len(na) - len(nb)) > max(2, longest // 20):
        return False  # короткую подпись по одной букве не угадать — считаем новой
    return SequenceMatcher(None, na, nb, autojunk=False).ratio() >= 0.9


# ---------- «осмысленность» распознанного текста ----------------------------
_TOKEN = re.compile(r"\S+")
_LATIN = re.compile(r"[A-Za-zÀ-ɏ]")
_CYRIL = re.compile(r"[Ѐ-ӿ]")
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")
_VOWELS = set("aeiouyAEIOUYàáâäèéêëìíîïòóôöùúûüаеёиоуыэюяАЕЁИОУЫЭЮЯіїєІЇЄ")
_FILE_OR_URL = re.compile(r"^[\w-]+\.[A-Za-z0-9]{1,5}$|[/_@]|://")
_EDGE_PUNCT = "\"'«»“”„()[]{}.,!?:;…-–—*/\\|<>"


def _token_ok(tok: str) -> bool | None:
    """Похоже ли слово на настоящее. None — не слово (число, знак) — не считаем."""
    word = tok.strip(_EDGE_PUNCT)
    letters = [ch for ch in word if ch.isalpha()]
    if not letters or _FILE_OR_URL.search(word):
        return None  # не слово: число, знак, имя файла, адрес
    if _CJK.search(word):
        return True  # иероглифы по буквам не проверить — верим
    if any(ch.isdigit() for ch in word):
        return False  # «pa60Tana», «Aut0Translator» — кириллица, прочитанная латиницей
    if any(ch in "«»@#$%^&*=~`|\\<>{}[]" for ch in word):
        return False
    latin, cyril = bool(_LATIN.search(word)), bool(_CYRIL.search(word))
    if latin and cyril:
        return False  # смесь алфавитов в одном слове
    if len(letters) >= 4 and not any(ch in _VOWELS for ch in letters):
        return False  # «xqyz» — без гласных слов почти не бывает
    # регистр скачет внутри слова: «ynyvuJHTS», «MOxews», «CMeHa» — две заглавные
    # подряд и строчные рядом (CamelCase «RenpyTestMenu», «iPhone», «OK» — можно)
    pairs = zip(letters, letters[1:], strict=False)
    if any(a.isupper() and b.isupper() for a, b in pairs) and any(
        ch.islower() for ch in letters
    ):
        return False
    return True


def text_quality(text: str) -> float:
    """Доля букв в «настоящих» словах, 0..1. Мусор OCR (не тот алфавит, шум
    фона) — низкая, нормальный текст — около 1. Нечего оценивать — 1."""
    good = total = 0
    for tok in _TOKEN.findall(text):
        ok = _token_ok(tok)
        if ok is None:
            continue
        n = sum(ch.isalpha() for ch in tok)
        total += n
        good += n if ok else 0
    return good / total if total else 1.0
