"""Простое хранилище настроек в JSON рядом с пользовательскими данными приложения.

Схема версионируется: ``_migrate`` мягко переносит старые файлы (например,
единое поле ``direction`` вида ``"ru->en"`` — в пару ``source``/``target``),
чтобы обновление приложения не сбрасывало настройки пользователя.
"""

from __future__ import annotations

import json
import os

from ..paths import CONFIG_FILE as _FILE
from ..paths import DATA_DIR as _DIR

SCHEMA = 2

DEFAULTS = {
    "schema": SCHEMA,
    "engine": "online",  # online | offline
    "source": "auto",  # код языка источника или "auto" (см. langs.py)
    "target": "ru",  # код языка перевода
    "always_on_top": True,
    "opacity": 100,  # 0..100
    "reading": False,  # режим чтения: рамка-маркер + живой перевод области
    "subtitle": True,  # в режиме чтения прятать окно и показывать перевод субтитром
    "subtitle_mode": "below",  # где субтитр: below — под областью, cover — поверх текста
    "ocr_wait_stable": True,  # ждать, пока текст «допечатается» (эффект печатной машинки)
    "capture_on_click": True,
    "live_target": "window",  # живой перевод: window — окно игры, screen — весь экран
    "live_bar_offset": None,  # [dx, dy] панели живого перевода от угла области  # в режиме чтения перечитывать область сразу после клика/Пробела
    "bridge_enabled": True,  # мост для игр: Ren'Py-модуль шлёт реплики напрямую, без OCR
    "bridge_port": 47811,
    "game_keep_names": True,  # имена персонажей, присланные игрой, не переводить
    "game_glossary": "",  # свои термины для игр: строки «термин = перевод»
    "game_tts": False,  # озвучивать перевод реплик игры
    "game_fixes": {},  # правки перевода из окна: {игра: {язык: {оригинал: перевод}}}
    "auto_refresh": True,  # периодически перечитывать область в режиме чтения
    "auto_interval_ms": 1200,  # период авто-обновления
    "autocopy": False,  # сразу копировать перевод в буфер
    "ocr_enhance": True,  # предобработка картинки перед OCR (точнее распознаёт)
    "ocr_merge_lines": True,  # склеивать строки OCR в абзацы (связный перевод)
    "theme": "dark",  # dark | light (эффективная тема; см. appearance_mode)
    "appearance_mode": "dark",  # dark | light | system (следовать за темой Windows)
    "flavor": "mocha",  # флейвор Catppuccin для тёмной темы: mocha|macchiato|frappe
    "font_scale": 100,  # масштаб текста полей/субтитра, %
    "favorites": [],  # закреплённые переводы [{"src":..., "dst":...}]
    "tts_enabled": False,  # озвучивать перевод
    "hotkeys_enabled": True,  # глобальные горячие клавиши
    "hotkeys": {},  # свои сочетания поверх hotkeys.DEFAULTS, напр. {"lens": "f9"}
    "history": [],  # последние переводы [{"src":..., "dst":...}]
    "history_max": 50,
    "offline_fallback": True,  # нет сети — перевести офлайн, если модель пары уже стоит
    "persist_cache": True,  # хранить кэш переводов на диске между запусками
    "restore_clipboard": True,  # Ctrl+Alt+T: вернуть буфер обмена как был
    "close_to_tray": False,  # крестик прячет в трей, а не закрывает программу
    "settings_open": False,  # не используется с 1.3 (настройки — отдельная страница)
    "region": None,  # [left, top, width, height]
    "geometry": None,  # base64 QByteArray сохранённой геометрии окна
}

# Старое поле direction -> (source, target)
_OLD_DIRECTIONS = {
    "ru->en": ("ru", "en"),
    "en->ru": ("en", "ru"),
    "auto->en": ("auto", "en"),
    "auto->ru": ("auto", "ru"),
}


def _migrate(data: dict) -> dict:
    if data.get("schema", 1) < 2:
        direction = data.pop("direction", "auto->ru")
        src, tgt = _OLD_DIRECTIONS.get(direction, ("auto", "ru"))
        data.setdefault("source", src)
        data.setdefault("target", tgt)
        data.pop("ocr_lang", None)  # OCR-язык теперь следует за источником
        data["schema"] = 2
    return data


# Числа, которые UI ждёт в определённых пределах (битое значение -> в рамки).
_RANGES = {
    "opacity": (35, 100),
    "auto_interval_ms": (300, 5000),
    "font_scale": (70, 180),
    "history_max": (1, 1000),
    "bridge_port": (1024, 65535),
}
_CHOICES = {
    "engine": ("online", "offline"),
    "subtitle_mode": ("below", "cover"),
    "live_target": ("window", "screen"),
    "theme": ("dark", "light"),
    "appearance_mode": ("dark", "light", "system"),
}


def _sanitize(data: dict) -> dict:
    """Чинит значения неверного типа/диапазона, чтобы ручная правка конфига не роняла окно.

    Тип сверяется с DEFAULTS: bool не путаем с int, у None-ключей (region,
    geometry) допускаем и None, и ожидаемый тип.
    """
    for key, default in DEFAULTS.items():
        val = data.get(key, default)
        if default is None:
            ok = (
                val is None
                or (
                    key == "region"
                    and isinstance(val, list)
                    and len(val) == 4
                    and all(isinstance(v, int | float) for v in val)
                )
                or (key == "geometry" and isinstance(val, str))
                or (
                    key == "live_bar_offset"
                    and isinstance(val, list)
                    and len(val) == 2
                    and all(isinstance(v, int) for v in val)
                )
            )
        elif isinstance(default, bool):
            ok = isinstance(val, bool)
        elif isinstance(default, int):
            ok = isinstance(val, int | float) and not isinstance(val, bool)
            if ok:
                val = int(val)
        else:
            ok = isinstance(val, type(default))
        data[key] = (
            val
            if ok
            else (
                dict(default)
                if isinstance(default, dict)
                else list(default)
                if isinstance(default, list)
                else default
            )
        )
    for key, (lo, hi) in _RANGES.items():
        data[key] = max(lo, min(hi, int(data[key])))
    for key, allowed in _CHOICES.items():
        if data[key] not in allowed:
            data[key] = DEFAULTS[key]
    for key in ("history", "favorites"):
        data[key] = [
            {"src": str(it.get("src", "")), "dst": str(it.get("dst", ""))}
            for it in data[key]
            if isinstance(it, dict)
        ]
    data["hotkeys"] = {k: v for k, v in data["hotkeys"].items() if isinstance(v, str)}
    return data


def load() -> dict:
    data = dict(DEFAULTS)
    try:
        stored = json.loads(_FILE.read_text(encoding="utf-8"))
        if not isinstance(stored, dict):
            raise ValueError("config is not an object")
        data.update(_migrate(stored))
        # старые конфиги знали только theme=dark|light — переносим в appearance_mode,
        # чтобы у вернувшегося пользователя не сбросился выбор светлой темы
        if "appearance_mode" not in stored and "theme" in stored:
            data["appearance_mode"] = stored["theme"]
    except FileNotFoundError:
        pass
    except (ValueError, OSError):
        # битый файл не молча затираем — откладываем копию, чтобы можно было достать
        try:
            _FILE.replace(_FILE.with_suffix(".broken.json"))
        except OSError:
            pass
    # гарантируем, что все ключи по умолчанию присутствуют и годного типа
    return _sanitize(data)


def dumps(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def save(data: dict) -> None:
    """Пишет атомарно: сначала во временный файл, потом подмена.

    Если процесс упадёт посреди записи, старый config.json останется целым.
    """
    _DIR.mkdir(parents=True, exist_ok=True)
    tmp = _FILE.with_suffix(".tmp")
    tmp.write_text(dumps(data), encoding="utf-8")
    os.replace(tmp, _FILE)
