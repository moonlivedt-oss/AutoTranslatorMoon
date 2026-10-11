"""Журнал ошибок: у .pyw нет консоли, и без него падения исчезают бесследно.

- ``logging`` пишет в ``%APPDATA%\\AutoTranslator\\autotranslator.log`` (ротация,
  файл не разрастается);
- необработанные исключения GUI-потока и фоновых потоков попадают в журнал, а
  окно получает короткое сообщение (через ``on_error``) — программа не падает;
- под ``pythonw`` ``sys.stderr`` равен None, и любой ``print``/warning в нём
  ронял бы приложение — подменяем на запись в журнал;
- предупреждения самого Qt тоже идут в журнал.
"""

from __future__ import annotations

import logging
import sys
import threading
import traceback
from collections.abc import Callable
from logging.handlers import RotatingFileHandler

from ..paths import LOG_FILE

log = logging.getLogger("autotranslator")


class _LogStream:
    """Файлоподобный объект вместо отсутствующего stderr/stdout."""

    def __init__(self, level: int) -> None:
        self._level = level

    def write(self, text: str) -> int:
        text = text.rstrip()
        if text:
            log.log(self._level, text)
        return len(text)

    def flush(self) -> None:
        pass


def install(on_error: Callable[[str], None] | None = None) -> None:
    """Включает журнал и перехват исключений. Безопасно вызывать один раз при старте."""
    handlers: list[logging.Handler] = []
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                LOG_FILE, maxBytes=512 * 1024, backupCount=2, encoding="utf-8"
            )
        )
    except OSError:
        pass  # нет доступа к APPDATA — живём без файла
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
    # сторонние библиотеки болтливы, а Argos на INFO пишет в журнал сами тексты
    # переводов — личное не должно оседать в файле
    for noisy in ("argostranslate", "stanza", "comtypes", "urllib3", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    if sys.stderr is None:
        sys.stderr = _LogStream(logging.ERROR)  # type: ignore[assignment]
    if sys.stdout is None:
        sys.stdout = _LogStream(logging.INFO)  # type: ignore[assignment]

    def report(exc_type, exc, tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        log.error(
            "необработанная ошибка:\n%s",
            "".join(traceback.format_exception(exc_type, exc, tb)),
        )
        if on_error is not None:
            try:
                on_error(f"{exc_type.__name__}: {exc}")
            except Exception:  # noqa: BLE001 — сам отчёт не должен падать
                pass

    sys.excepthook = report
    threading.excepthook = lambda a: report(a.exc_type, a.exc_value, a.exc_traceback)

    try:
        from PyQt6.QtCore import QtMsgType, qInstallMessageHandler

        levels = {
            QtMsgType.QtDebugMsg: logging.DEBUG,
            QtMsgType.QtInfoMsg: logging.INFO,
            QtMsgType.QtWarningMsg: logging.WARNING,
            QtMsgType.QtCriticalMsg: logging.ERROR,
            QtMsgType.QtFatalMsg: logging.CRITICAL,
        }
        qInstallMessageHandler(
            lambda mode, _ctx, msg: logging.getLogger("qt").log(
                levels.get(mode, logging.WARNING), msg
            )
        )
    except Exception:  # noqa: BLE001
        pass
    log.info("запуск, Python %s", sys.version.split()[0])
