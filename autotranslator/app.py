"""Точка запуска AutoTranslator: приложение Qt, журнал ошибок, один экземпляр.

Само окно — пакет ``window/`` (MainWindow собран из частей по темам: трей,
тема, история, режим «Чтение», экранный и живой перевод, мост для игр).
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QGuiApplication, QIcon
from PyQt6.QtWidgets import QApplication

from . import __version__
from .paths import asset
from .win import crashlog
from .win.single_instance import SingleInstance
from .window import MainWindow

log = logging.getLogger(__name__)

__all__ = ["MainWindow", "main"]


def main() -> int:
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    if sys.platform == "win32":
        # свой AppUserModelID — Windows покажет нашу иконку, а не значок Python
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "MoonLight.AutoTranslator"
            )
        except Exception:  # noqa: BLE001
            pass
    app = QApplication(sys.argv)
    app.setApplicationName("AutoTranslator")
    app.setApplicationVersion(__version__)
    app.setWindowIcon(QIcon(asset("app.ico")))
    app.setQuitOnLastWindowClosed(True)
    app.setFont(QFont("Segoe UI", 10))

    holder: dict = {}

    def on_error(msg: str) -> None:
        win = holder.get("win")
        if win is not None:
            win._set_status("Внутренняя ошибка — подробности в журнале (F1)", "error")
            win.status.setToolTip(msg)

    crashlog.install(on_error)

    instance = SingleInstance()
    if instance.notify_running():
        log.info("уже запущен — показываем существующее окно")
        return 0

    win = MainWindow()
    holder["win"] = win
    instance.listen(win.bring_to_front)
    app.aboutToQuit.connect(instance.close)
    # если чтение восстановилось в субтитр-режиме, окно уже спрятано — не показываем
    if not win._hidden_for_reading:
        win.appear()  # окно проявляется, а не «выпрыгивает»
    code = app.exec()
    busy = [
        th.objectName() or "worker" for th in win.background_threads() if th.isRunning()
    ]
    if busy:
        # поток завис в долгом сетевом запросе (таймаут до 20 с): Qt уронил бы
        # процесс, уничтожая работающий поток. Всё уже сохранено в closeEvent —
        # выходим сразу, без разрушения объектов
        log.warning("выход при занятых потоках: %s", ", ".join(busy))
        logging.shutdown()
        os._exit(code)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
