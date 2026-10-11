"""Мелкие Windows-штучки для оверлеев: исключение из захвата экрана, фокус, DPI.

- ``exclude_from_capture`` — окно видно человеку, но НЕ попадает в скриншоты
  (SetWindowDisplayAffinity + WDA_EXCLUDEFROMCAPTURE, Windows 10 2004+). Благодаря
  этому перевод можно рисовать прямо поверх текста игры, а OCR под ним всё равно
  видит оригинал — свои окна никогда не мешают распознаванию.
- ``force_foreground`` — вывести окно на передний план, даже если приложение не
  активно (после глобальной горячей клавиши Windows обычно запрещает это).
- ``to_physical`` / ``to_logical`` — логические Qt-координаты <-> физические
  пиксели (mss и WinAPI работают в физических);
- ``input_state`` — опрос мыши и клавиш «дальше» без хуков (для новелл);
- окно игры для живого перевода: ``pick_game_window``, ``client_rect``,
  ``window_state``, ``covered_by_other``.
"""

from __future__ import annotations

import ctypes
import os
import sys

from PyQt6.QtCore import QPoint
from PyQt6.QtGui import QGuiApplication

_WDA_EXCLUDEFROMCAPTURE = 0x11


def exclude_from_capture(widget) -> bool:
    """Прячет окно от захвата экрана. Безопасно вызывать повторно (после смены флагов
    Qt пересоздаёт нативное окно — тогда вызвать ещё раз)."""
    if sys.platform != "win32":
        return False
    try:
        hwnd = int(widget.winId())
        return bool(
            ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, _WDA_EXCLUDEFROMCAPTURE)
        )
    except Exception:  # noqa: BLE001 — старая Windows: просто остаётся видимым
        return False


def force_foreground(widget) -> None:
    """Активирует окно поверх всех, обходя запрет Windows на «кражу фокуса»."""
    widget.raise_()
    widget.activateWindow()
    if sys.platform != "win32":
        return
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        hwnd = int(widget.winId())
        fg = user32.GetForegroundWindow()
        fg_thread = user32.GetWindowThreadProcessId(fg, None)
        me = kernel32.GetCurrentThreadId()
        if fg_thread and fg_thread != me:
            user32.AttachThreadInput(me, fg_thread, True)
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            user32.AttachThreadInput(me, fg_thread, False)
        else:
            user32.SetForegroundWindow(hwnd)
    except Exception:  # noqa: BLE001
        pass


def screen_for(left: int, top: int, width: int, height: int):
    center = QPoint(left + width // 2, top + height // 2)
    return QGuiApplication.screenAt(center) or QGuiApplication.primaryScreen()


def to_physical(
    left: int, top: int, width: int, height: int
) -> tuple[int, int, int, int]:
    """Переводит логические Qt-координаты области в физические пиксели для mss.

    При масштабе экрана != 100% mss работает в физических пикселях, а Qt отдаёт
    логические. У Qt левый верхний угол каждого экрана совпадает с физическим,
    а масштабируется только смещение внутри экрана — поэтому пересчёт идёт
    относительно начала экрана (иначе на втором мониторе область «уезжает»).
    """
    screen = screen_for(left, top, width, height)
    if screen is None:  # мониторы недоступны (переподключение дисплея) — как есть
        return left, top, width, height
    dpr = screen.devicePixelRatio()
    if abs(dpr - 1.0) < 1e-3:
        return left, top, width, height
    origin = screen.geometry().topLeft()
    ox, oy = origin.x(), origin.y()
    return (
        ox + round((left - ox) * dpr),
        oy + round((top - oy) * dpr),
        round(width * dpr),
        round(height * dpr),
    )


def foreground_is_ours() -> bool:
    """Активное окно Windows — наше (клик в своё окно не считается кликом в игре)."""
    if sys.platform != "win32":
        return False
    try:
        return window_pid(foreground_window()) == os.getpid()
    except Exception:  # noqa: BLE001
        return False


# Клавиши, которыми листают новеллы: Пробел, Enter, PageUp / PageDown (откат и
# повтор реплики в Ren'Py). ЛКМ — отдельно: для мыши важно, где был клик.
_VK_LBUTTON = 0x01
_VK_ADVANCE_KEYS = (0x20, 0x0D, 0x21, 0x22)
_VK_SHIFT = 0x10


def input_state() -> tuple[bool, bool, bool] | None:
    """(ЛКМ зажата, клавиша «дальше» зажата, Shift зажат). None — опрос недоступен."""
    if sys.platform != "win32":
        return None
    try:
        state = ctypes.windll.user32.GetAsyncKeyState
        return (
            bool(state(_VK_LBUTTON) & 0x8000),
            any(state(vk) & 0x8000 for vk in _VK_ADVANCE_KEYS),
            bool(state(_VK_SHIFT) & 0x8000),
        )
    except Exception:  # noqa: BLE001
        return None


# ---------- окно игры (для живого перевода) ----------
class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def foreground_window() -> int:
    """hwnd активного окна (0 — нет или не Windows)."""
    if sys.platform != "win32":
        return 0
    try:
        return int(ctypes.windll.user32.GetForegroundWindow() or 0)
    except Exception:  # noqa: BLE001
        return 0


def window_pid(hwnd: int) -> int:
    if sys.platform != "win32" or not hwnd:
        return 0
    pid = ctypes.c_ulong()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def window_state(hwnd: int) -> str:
    """ "ok" — окно на экране, "minimized" — свёрнуто, "gone" — закрыто."""
    if sys.platform != "win32" or not hwnd:
        return "gone"
    user32 = ctypes.windll.user32
    if not user32.IsWindow(hwnd):
        return "gone"
    return "minimized" if user32.IsIconic(hwnd) else "ok"


def client_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """Клиентская область окна (без рамки и заголовка) в физических пикселях."""
    if sys.platform != "win32" or not hwnd:
        return None
    try:
        user32 = ctypes.windll.user32
        rc = _RECT()
        if not user32.GetClientRect(hwnd, ctypes.byref(rc)):
            return None
        origin = _POINT(0, 0)
        if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
            return None
        w, h = rc.right - rc.left, rc.bottom - rc.top
        if w < 50 or h < 50:
            return None
        return origin.x, origin.y, w, h
    except Exception:  # noqa: BLE001
        return None


def is_shell_window(hwnd: int) -> bool:
    """Рабочий стол / панель задач — это не «окно игры»."""
    if sys.platform != "win32" or not hwnd:
        return True
    buf = ctypes.create_unicode_buffer(64)
    ctypes.windll.user32.GetClassNameW(hwnd, buf, 64)
    return buf.value in (
        "Progman",
        "WorkerW",
        "Shell_TrayWnd",
        "Shell_SecondaryTrayWnd",
    )


def to_logical(
    left: int, top: int, width: int, height: int
) -> tuple[int, int, int, int]:
    """Обратный пересчёт к ``to_physical``: физические пиксели -> логические Qt."""
    for screen in QGuiApplication.screens():
        g = screen.geometry()
        dpr = screen.devicePixelRatio() or 1.0
        ox, oy = g.x(), g.y()
        if ox <= left < ox + g.width() * dpr and oy <= top < oy + g.height() * dpr:
            return (
                ox + round((left - ox) / dpr),
                oy + round((top - oy) / dpr),
                round(width / dpr),
                round(height / dpr),
            )
    return left, top, width, height


def _cloaked(hwnd: int) -> bool:
    """Окно «спрятано» DWM (фоновые UWP-окна): видимо формально, но не на экране."""
    try:
        val = ctypes.c_int(0)
        ctypes.windll.dwmapi.DwmGetWindowAttribute(
            hwnd,
            14,
            ctypes.byref(val),
            ctypes.sizeof(val),  # DWMWA_CLOAKED
        )
        return bool(val.value)
    except Exception:  # noqa: BLE001
        return False


def window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """Внешняя рамка окна в физических пикселях (left, top, width, height)."""
    if sys.platform != "win32" or not hwnd:
        return None
    rc = _RECT()
    if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rc)):
        return None
    return rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top


def pick_game_window() -> int:
    """Окно, которое, скорее всего, имеет в виду игрок: активное, а если активно
    наше — верхнее чужое окно под ним (по z-порядку). 0 — подходящего нет."""
    if sys.platform != "win32":
        return 0
    me = os.getpid()
    user32 = ctypes.windll.user32

    def suitable(hwnd: int) -> bool:
        if not hwnd or window_pid(hwnd) == me or is_shell_window(hwnd):
            return False
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd) or _cloaked(hwnd):
            return False
        if user32.GetWindowTextLengthW(hwnd) == 0:
            return False  # служебные окна без заголовка
        if user32.GetWindowLongW(hwnd, -20) & 0x80:  # WS_EX_TOOLWINDOW
            return False
        rc = client_rect(hwnd)
        return rc is not None and rc[2] >= 320 and rc[3] >= 200

    try:
        fg = foreground_window()
        if suitable(fg):
            return fg
        found: list[int] = []
        proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def on_window(hwnd, _lparam) -> bool:  # окна идут сверху вниз по z-порядку
            if suitable(int(hwnd or 0)):
                found.append(int(hwnd))
                return False
            return True

        user32.EnumWindows(proc(on_window), 0)
        return found[0] if found else 0
    except Exception:  # noqa: BLE001
        return 0


def covered_by_other(hwnd: int, share: float = 0.3) -> bool:
    """Активно чужое окно (не наше и не этой игры), и оно закрывает заметную часть
    окна игры — например, игрок переключился в браузер поверх неё."""
    fg = foreground_window()
    if not fg or fg == hwnd:
        return False
    pid = window_pid(fg)
    if pid in (os.getpid(), window_pid(hwnd)) or is_shell_window(fg):
        return False
    a, b = window_rect(hwnd), window_rect(fg)
    if a is None or b is None:
        return False
    w = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    h = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return w > 0 and h > 0 and w * h >= share * max(1, a[2] * a[3])
