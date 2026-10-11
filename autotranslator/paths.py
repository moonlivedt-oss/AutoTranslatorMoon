"""Где что лежит: пользовательские данные (%APPDATA%) и ассеты пакета."""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "AutoTranslator"

# настройки, кэш переводов и журнал — у пользователя, а не рядом с программой
DATA_DIR = Path(os.getenv("APPDATA") or Path.home()) / APP_NAME
CONFIG_FILE = DATA_DIR / "config.json"
CACHE_FILE = DATA_DIR / "cache.json"
LOG_FILE = DATA_DIR / "autotranslator.log"

# картинки (иконка приложения, маскот) — внутри пакета
ASSETS_DIR = Path(__file__).resolve().parent / "assets"


def asset(name: str) -> str:
    """Путь к ассету строкой (для QIcon/QPixmap). Файла может не быть — Qt это переживёт."""
    return str(ASSETS_DIR / name)
