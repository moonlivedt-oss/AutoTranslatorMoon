"""Перевод реплик Ren'Py так, чтобы не сломать разметку движка.

В тексте реплики живут вещи, которые переводить НЕЛЬЗЯ — иначе игра упадёт или
покажет мусор:

- текстовые теги ``{i}``, ``{/b}``, ``{w}``, ``{color=#f00}``, ``{nw}`` …;
- подстановки ``[name]``, ``[player.money]``, ``[count!t]``;
- экранирование ``{{`` / ``[[``, старый стиль ``%(name)s``, перевод строки.

Перед отправкой в переводчик каждая такая вставка заменяется меткой ``{0}``,
``{1}`` … (Google их не трогает — проверено), после перевода метки возвращаются.
Если переводчик потерял/задвоил метку — переводим куски между вставками по
отдельности: качество чуть ниже, зато разметка гарантированно цела. Если и это
не вышло — строка остаётся без перевода (игра покажет оригинал, а не упадёт).
"""

from __future__ import annotations

import re

_TOKEN = re.compile(
    r"\{\{|\[\[|\{[^{}]*\}|\[[^\[\]]*\]|%\([^)]*\)[-#0 +]*\d*(?:\.\d+)?[sdifr]|\n"
    r"|[{}\[\]]"  # одиночные скобки — тоже вставки, чтобы не путать с метками
)
_MARK = re.compile(r"\{\s*(\d+)\s*\}")
_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)


def glossary_regex(glossary: dict[str, str] | None) -> re.Pattern | None:
    """Регэксп «тег/подстановка ИЛИ термин глоссария» (термины — целыми словами).

    Теги идут первыми: имя внутри ``[alice.name]`` остаётся частью подстановки.
    Длинные термины раньше коротких: «Mary Jane» не разрежется на «Mary»."""
    terms = sorted((t for t in (glossary or {}) if t.strip()), key=len, reverse=True)
    if not terms:
        return None
    alt = "|".join(re.escape(t) for t in terms)
    return re.compile(_TOKEN.pattern + r"|(?<!\w)(?:" + alt + r")(?!\w)")


def protect(
    text: str,
    glossary: dict[str, str] | None = None,
    pattern: re.Pattern | None = None,
) -> tuple[str, list[str]]:
    """Заменяет теги/подстановки (и термины глоссария) метками {N}.

    Возвращает (текст, список вставок). Термин глоссария возвращается на место
    уже в виде своего перевода (имя персонажа — как есть или как задано)."""
    tokens: list[str] = []
    rx = pattern or (glossary_regex(glossary) if glossary else None) or _TOKEN

    def sub(m: re.Match) -> str:
        s = m.group(0)
        tokens.append(glossary[s] if glossary and s in glossary else s)
        return f"{{{len(tokens) - 1}}}"

    return rx.sub(sub, text), tokens


def restore(text: str, tokens: list[str]) -> str | None:
    """Возвращает вставки на место меток. None — если метки потеряны/задвоены."""
    found = [int(m.group(1)) for m in _MARK.finditer(text)]
    if sorted(found) != list(range(len(tokens))):
        return None
    out = _MARK.sub(lambda m: tokens[int(m.group(1))], text)
    # одиночная фигурная/квадратная скобка от переводчика сломала бы разметку
    rest = _MARK.sub("", text)
    if any(ch in rest for ch in "{}[]"):
        return None
    return out


def _pieces(masked: str) -> list[str]:
    """Режет защищённый текст на [текст, метка, текст, метка, …, текст]."""
    return re.split(r"(\{\d+\})", masked)


def translate_many(
    engine,
    texts: list[str],
    source: str,
    target: str,
    glossary: dict[str, str] | None = None,
) -> list[str | None]:
    """Переводит реплики Ren'Py пачкой. Для строк без букв — сама строка.

    ``glossary`` — {термин: как писать в переводе}: имена персонажей и свои
    термины не уходят в переводчик (Lucky не станет «Счастливчиком»)."""
    results: list[str | None] = [None] * len(texts)
    masked: list[tuple[int, str, list[str]]] = []
    rx = glossary_regex(glossary)
    for i, t in enumerate(texts):
        m, toks = protect(t, glossary, rx)
        if not _LETTER.search(_MARK.sub("", m)):
            # одни теги/подстановки/термины — переводить нечего (но термин заменяем)
            results[i] = restore(m, toks) if glossary else t
            continue
        masked.append((i, m, toks))
    if not masked:
        return results

    first = engine.translate_many([m for _, m, _ in masked], source, target)
    retry: list[tuple[int, str, list[str]]] = []
    for (i, m, toks), tr in zip(masked, first, strict=True):
        fixed = restore(tr, toks) if tr else None
        if fixed is None:
            retry.append((i, m, toks))
        else:
            results[i] = fixed

    if retry:
        # запасной путь: переводим только текстовые куски между вставками
        jobs: list[tuple[int, int]] = []  # (номер в retry, номер куска)
        units: list[str] = []
        split = [_pieces(m) for _, m, _ in retry]
        for r, parts in enumerate(split):
            for k, part in enumerate(parts):
                if k % 2 == 0 and _LETTER.search(part):
                    jobs.append((r, k))
                    units.append(part)
        done = engine.translate_many(units, source, target) if units else []
        for (r, k), tr in zip(jobs, done, strict=True):
            part = split[r][k]
            lead = part[: len(part) - len(part.lstrip())]
            tail = part[len(part.rstrip()) :]
            split[r][k] = lead + tr.strip() + tail if tr else part
        for (i, _m, toks), parts in zip(retry, split, strict=True):
            results[i] = restore("".join(parts), toks)
    return results
