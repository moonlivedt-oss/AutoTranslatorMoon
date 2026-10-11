"""«Игрок перелистнул реплику»: опрос ЛКМ / Пробела / Enter без хуков ввода.

Нужен режиму «Чтение» («перечитывать по клику») и живому переводу. Раньше у
каждого был свой таймер с одинаковым опросом; теперь один общий наблюдатель:
кто нуждается — берёт его (``acquire``), отпускает (``release``), а таймер
крутится, только пока есть хоть один подписчик.

Опрос — ``GetAsyncKeyState`` раз в 30 мс: без глобальных хуков (их не любят
античиты и они тормозят ввод), без прав администратора. Нажатия в окна самого
переводчика не считаются — это не перелистывание игры.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, QPoint, QTimer, pyqtSignal
from PyQt6.QtGui import QCursor

from ..win.winutil import foreground_is_ours, input_state

POLL_MS = 30


class AdvanceWatcher(QObject):
    # "mouse" | "key", где был курсор (логические координаты экрана)
    advanced = pyqtSignal(str, QPoint)
    # зажат Shift — «подсмотреть оригинал» в живом переводе
    peek_changed = pyqtSignal(bool)

    def __init__(self) -> None:
        super().__init__()
        self._owners: set[int] = set()
        self._mouse = self._key = self._peek = False
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._poll)

    @property
    def supported(self) -> bool:
        return input_state() is not None

    def acquire(self, owner: object) -> bool:
        """Начать опрос для owner. False — опрос на этой системе недоступен."""
        if not self.supported:
            return False
        self._owners.add(id(owner))
        if not self._timer.isActive():
            self._mouse, self._key, self._peek = self._read()
            self._timer.start()
        return True

    def release(self, owner: object) -> None:
        self._owners.discard(id(owner))
        if not self._owners:
            self._timer.stop()
            if self._peek:
                self._peek = False
                self.peek_changed.emit(False)

    @staticmethod
    def _read() -> tuple[bool, bool, bool]:
        return input_state() or (False, False, False)

    def _poll(self) -> None:
        mouse, key, peek = self._read()
        pressed = (
            "mouse"
            if mouse and not self._mouse
            else "key"
            if key and not self._key
            else ""
        )
        self._mouse, self._key = mouse, key
        if peek != self._peek:
            self._peek = peek
            self.peek_changed.emit(peek)
        if pressed and not foreground_is_ours():
            self.advanced.emit(pressed, QCursor.pos())


_WATCHER: AdvanceWatcher | None = None


def watcher() -> AdvanceWatcher:
    """Общий наблюдатель на всё приложение (создаётся при первом обращении)."""
    global _WATCHER
    if _WATCHER is None:
        _WATCHER = AdvanceWatcher()
    return _WATCHER
