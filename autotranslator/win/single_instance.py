"""Только один AutoTranslator на пользователя.

Второй запуск (двойной клик по ярлыку, автозапуск + ручной) раньше поднимал ещё
одну копию: два значка в трее, двойные горячие клавиши и «порт моста занят».
Теперь вторая копия через именованный канал (QLocalServer) просит первую
показать окно и сразу выходит.
"""

from __future__ import annotations

import getpass
from collections.abc import Callable

from PyQt6.QtNetwork import QLocalServer, QLocalSocket

_MSG_SHOW = b"show"


def _server_name() -> str:
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001
        user = "user"
    return f"AutoTranslator-{user}"


class SingleInstance:
    def __init__(self) -> None:
        self._server: QLocalServer | None = None

    def notify_running(self, timeout_ms: int = 400) -> bool:
        """True — копия уже запущена (подключение к ней и есть просьба показаться)."""
        sock = QLocalSocket()
        sock.connectToServer(_server_name())
        if not sock.waitForConnected(timeout_ms):
            return False
        sock.write(_MSG_SHOW)
        sock.flush()
        sock.waitForBytesWritten(timeout_ms)
        # дождаться «ok», чтобы канал не оборвался раньше, чем его заметят
        sock.waitForReadyRead(timeout_ms)
        sock.disconnectFromServer()
        return True

    def listen(self, on_show: Callable[[], None]) -> bool:
        """Становимся «главной» копией: ждём просьб от следующих запусков."""
        name = _server_name()
        server = QLocalServer()
        if not server.listen(name):
            # остался «мёртвый» канал от упавшего процесса — убираем и пробуем ещё
            QLocalServer.removeServer(name)
            if not server.listen(name):
                return False

        def on_connection() -> None:
            while server.hasPendingConnections():
                conn = server.nextPendingConnection()
                if conn is None:
                    break
                # канал свой (имя с пользователем) — подключение и есть «покажись»;
                # содержимое не разбираем: на Windows оно может не успеть дойти
                conn.write(b"ok")
                conn.flush()
                conn.disconnected.connect(conn.deleteLater)
                on_show()

        server.newConnection.connect(on_connection)
        self._server = server
        return True

    def close(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server = None
