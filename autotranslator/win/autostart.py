"""Автозапуск вместе с Windows — строка в ``HKCU\\...\\Run`` (без прав администратора).

Включается только явно, галочкой в меню трея. Запускаем через ``pythonw``,
чтобы при входе в систему не мелькало консольное окно.
"""

from __future__ import annotations

import sys
from pathlib import Path

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE = "AutoTranslator"
_ENTRY = Path(__file__).resolve().parents[2] / "run.pyw"


def supported() -> bool:
    return sys.platform == "win32" and _ENTRY.exists()


def command() -> str:
    exe = Path(sys.executable)
    windowed = exe.with_name("pythonw.exe")
    if windowed.exists():
        exe = windowed
    return f'"{exe}" "{_ENTRY}"'


def is_enabled() -> bool:
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            val, _ = winreg.QueryValueEx(key, _VALUE)
            return bool(val)
    except OSError:
        return False


def set_enabled(on: bool) -> None:
    """Включает/выключает автозапуск. OSError — пусть покажет вызывающий."""
    import winreg

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        if on:
            winreg.SetValueEx(key, _VALUE, 0, winreg.REG_SZ, command())
        else:
            try:
                winreg.DeleteValue(key, _VALUE)
            except FileNotFoundError:
                pass
