"""Таблица языков: единый источник правды для перевода, OCR и озвучки.

Раньше приложение знало только пару RU↔EN четырьмя фиксированными «направлениями».
Здесь язык описан один раз, а разные подсистемы берут из него свой код:

- ``google`` — код для онлайн-движка (deep-translator / Google);
- ``argos`` — код для офлайн-движка (Argos Translate);
- ``ocr``    — language_tag для Windows.Media.Ocr (winsdk);
- ``tts``    — префикс BCP-47 для подбора системного голоса озвучки.

Добавить язык — одна строка в ``LANGUAGES`` (плюс, при желании, языковой пакет
OCR и офлайн-модель Argos, которые докачиваются сами).
"""

from __future__ import annotations

import re

# code -> (человекочитаемое имя, google, argos, ocr-tag, tts-префикс)
# code — внутренний ключ (совпадает с google там, где это удобно).
LANGUAGES: dict[str, dict[str, str]] = {
    "en": {
        "name": "English",
        "google": "en",
        "argos": "en",
        "ocr": "en-US",
        "tts": "en",
    },
    "ru": {"name": "Русский", "google": "ru", "argos": "ru", "ocr": "ru", "tts": "ru"},
    "ja": {"name": "日本語", "google": "ja", "argos": "ja", "ocr": "ja", "tts": "ja"},
    "zh-CN": {
        "name": "中文 (упр.)",
        "google": "zh-CN",
        "argos": "zh",
        "ocr": "zh-Hans",
        "tts": "zh",
    },
    "ko": {"name": "한국어", "google": "ko", "argos": "ko", "ocr": "ko", "tts": "ko"},
    "es": {"name": "Español", "google": "es", "argos": "es", "ocr": "es", "tts": "es"},
    "fr": {"name": "Français", "google": "fr", "argos": "fr", "ocr": "fr", "tts": "fr"},
    "de": {"name": "Deutsch", "google": "de", "argos": "de", "ocr": "de", "tts": "de"},
    "it": {"name": "Italiano", "google": "it", "argos": "it", "ocr": "it", "tts": "it"},
    "pt": {
        "name": "Português",
        "google": "pt",
        "argos": "pt",
        "ocr": "pt",
        "tts": "pt",
    },
    "uk": {
        "name": "Українська",
        "google": "uk",
        "argos": "uk",
        "ocr": "uk",
        "tts": "uk",
    },
}

AUTO = "auto"  # псевдо-код источника: определить язык автоматически


def name(code: str) -> str:
    if code == AUTO:
        return "Авто"
    return LANGUAGES.get(code, {}).get("name", code)


def google_code(code: str) -> str:
    return AUTO if code == AUTO else LANGUAGES.get(code, {}).get("google", code)


def argos_code(code: str) -> str:
    return LANGUAGES.get(code, {}).get("argos", code)


def ocr_tag(code: str) -> str | None:
    """language_tag для winsdk; None для авто (профиль пользователя)."""
    if code == AUTO:
        return None
    return LANGUAGES.get(code, {}).get("ocr")


def tts_prefix(code: str) -> str:
    return LANGUAGES.get(code, {}).get("tts", code)


# Диапазоны скриптов Unicode для грубого определения языка по написанию.
_SCRIPTS = (
    ("ja", re.compile(r"[぀-ヿ]")),  # хирагана/катакана → японский
    ("ko", re.compile(r"[가-힣]")),  # хангыль → корейский
    ("zh-CN", re.compile(r"[一-鿿]")),  # ханьцзы → китайский (после ja/ko)
    ("ru", re.compile(r"[Ѐ-ӿ]")),  # кириллица → русский
)


_UK_ONLY = re.compile(r"[іїєґІЇЄҐ]")
_RU_ONLY = re.compile(r"[ыэъёЫЭЪЁ]")
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)

# Частые служебные слова латинских языков — грубо, но для «Авто» хватает.
_STOPWORDS: dict[str, frozenset[str]] = {
    "en": frozenset(
        "the and of to is in that it you for with are this was on be not have".split()
    ),
    "es": frozenset(
        "el la los las de que y en un una es por con para no se del lo al".split()
    ),
    "fr": frozenset(
        "le la les de des et est un une du que qui pas pour en dans ce je vous".split()
    ),
    "de": frozenset(
        "der die das und ist nicht ein eine zu den mit von sich auf ich es dem".split()
    ),
    "it": frozenset(
        "il la di che e è un una per non sono gli del della le lo con ma".split()
    ),
    "pt": frozenset(
        "o a os as de que e é um uma não do da em para com por se são".split()
    ),
}
# Буквы, которые почти однозначно указывают на язык.
_LATIN_MARKS = (
    ("de", re.compile(r"[äöüßÄÖÜ]")),
    ("es", re.compile(r"[ñ¿¡Ñ]")),
    ("pt", re.compile(r"[ãõÃÕ]")),
    ("fr", re.compile(r"[œæëïÿçŒ]|\b(?:l|d|qu|n|c|j)'", re.IGNORECASE)),
)


def _detect_latin(text: str) -> str:
    words = [w.lower() for w in _WORD.findall(text)]
    scores = {code: 0.0 for code in _STOPWORDS}
    for w in words:
        for code, stop in _STOPWORDS.items():
            if w in stop:
                scores[code] += 1
    for code, pattern in _LATIN_MARKS:
        scores[code] += 1.5 * min(3, len(pattern.findall(text)))
    best = max(scores, key=lambda c: scores[c])
    # при равенстве/нуле — английский (самый частый случай на экране)
    if scores[best] <= scores["en"] or scores[best] < 1:
        return "en"
    return best


def detect_lang(text: str) -> str:
    """Определяет язык текста. По умолчанию — английский.

    Сначала — по преобладающему скрипту (кана, хангыль, ханьцзы, кириллица).
    Порядок важен: японский текст часто содержит и кандзи (ханьцзы), поэтому
    хирагану/катакану проверяем раньше китайского. Кириллицу различаем на
    русский/украинский по характерным буквам, латиницу — по частым словам и
    диакритике. Возвращает код из LANGUAGES.
    """
    for code, pattern in _SCRIPTS:
        if pattern.search(text):
            if code == "ru":
                uk, ru = len(_UK_ONLY.findall(text)), len(_RU_ONLY.findall(text))
                return "uk" if uk > ru else "ru"
            return code
    return _detect_latin(text)
