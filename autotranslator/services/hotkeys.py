"""Глобальные горячие клавиши через пакет ``keyboard`` — необязательная зависимость.

Работают, даже когда окно не в фокусе (в игре, читалке, браузере) — это главное,
ради чего они нужны в экранном переводчике. Библиотека ``keyboard`` слушает ввод
в своём потоке, поэтому колбэки прилетают НЕ из GUI-потока; мы превращаем их в
Qt-сигналы, и уже слоты выполняются безопасно в главном потоке.

Если пакет не установлен, ``HotkeyManager.available`` == False — приложение
работает как обычно, просто без глобальных клавиш.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import QObject, pyqtSignal

log = logging.getLogger(__name__)

# Действие -> сочетание по умолчанию (синтаксис пакета keyboard).
DEFAULTS = {
    "lens": "ctrl+alt+a",  # перевести весь экран (перевод поверх текста)
    "lens_area": "ctrl+alt+q",  # выделить кусок экрана и перевести его поверх
    "live": "ctrl+alt+w",  # вкл/выкл живой перевод экрана (для новелл)
    "region": "ctrl+alt+z",  # выбрать область
    "reading": "ctrl+alt+x",  # вкл/выкл чтение
    "recapture": "ctrl+alt+c",  # перечитать сейчас
    "clipboard": "ctrl+alt+v",  # перевести из буфера
    "speak": "ctrl+alt+s",  # озвучить перевод
    "selection": "ctrl+alt+t",  # перевести выделенный текст в любом окне
}


class HotkeyManager(QObject):
    lens_pressed = pyqtSignal()
    lens_area_pressed = pyqtSignal()
    live_pressed = pyqtSignal()
    region_pressed = pyqtSignal()
    reading_pressed = pyqtSignal()
    recapture_pressed = pyqtSignal()
    clipboard_pressed = pyqtSignal()
    speak_pressed = pyqtSignal()
    selection_pressed = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.available = False
        self.active = False  # хуки сейчас стоят (не на паузе)
        self._kb = None
        self._handles: list = []
        try:
            import keyboard

            self._kb = keyboard
            self.available = True
        except Exception:  # noqa: BLE001 — нет пакета или нет прав на хук
            return

    def _signal_for(self, action: str):
        return {
            "lens": self.lens_pressed,
            "lens_area": self.lens_area_pressed,
            "live": self.live_pressed,
            "region": self.region_pressed,
            "reading": self.reading_pressed,
            "recapture": self.recapture_pressed,
            "clipboard": self.clipboard_pressed,
            "speak": self.speak_pressed,
            "selection": self.selection_pressed,
        }[action]

    def register(self, bindings: dict[str, str] | None = None) -> list[str]:
        """Ставит хуки. Возвращает «действие: сочетание», которые не удалось занять
        (битый синтаксис в конфиге и т.п.) — окно покажет их пользователю."""
        if not self.available:
            return []
        self.unregister()
        failed: list[str] = []
        binds = {**DEFAULTS, **(bindings or {})}
        for action, combo in binds.items():
            if not combo or action not in DEFAULTS:
                continue
            sig = self._signal_for(action)
            try:
                # trigger_on_release снижает риск авто-повтора при удержании
                h = self._kb.add_hotkey(combo, sig.emit, trigger_on_release=True)
                self._handles.append(h)
            except Exception as e:  # noqa: BLE001 — занятое/некорректное сочетание
                log.warning("горячая клавиша %s=%r не занята: %s", action, combo, e)
                failed.append(f"{action}: {combo}")
        self.active = bool(self._handles)
        return failed

    def modifiers_held(self) -> bool:
        """Зажат ли ещё Ctrl/Alt/Shift/Win (после срабатывания сочетания)."""
        if not self.available:
            return False
        try:
            return any(
                self._kb.is_pressed(k) for k in ("ctrl", "alt", "shift", "windows")
            )
        except Exception:  # noqa: BLE001
            return False

    def send_copy(self) -> bool:
        """Жмёт Ctrl+C в активном окне — копирует выделение. False, если не вышло."""
        if not self.available:
            return False
        try:
            self._kb.send("ctrl+c")
            return True
        except Exception:  # noqa: BLE001
            return False

    def unregister(self) -> None:
        if not self.available:
            return
        for h in self._handles:
            try:
                self._kb.remove_hotkey(h)
            except Exception:  # noqa: BLE001
                pass
        self._handles.clear()
        self.active = False
