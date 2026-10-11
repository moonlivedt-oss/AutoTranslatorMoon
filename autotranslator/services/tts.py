"""Озвучка перевода (text-to-speech) через pyttsx3 — необязательная зависимость.

Если pyttsx3 не установлен, ``Speaker.available`` == False и кнопка/горячая
клавиша просто ничего не делают (приложение остаётся полностью рабочим).

Речь синтезируется в отдельном потоке-демоне, чтобы окно не подвисало: движок
блокирующий (runAndWait), поэтому запросы кладём в очередь и проговариваем по
одному; если фразы копятся быстрее, чем звучат, проговаривается только
последняя (актуально в режиме «Чтение»).
"""

from __future__ import annotations

import threading
from queue import Queue


class Speaker:
    def __init__(self) -> None:
        self._queue: Queue[tuple[str, str] | None] = Queue()
        self._engine = None
        self._voices: list = []
        self.available = False
        try:
            import sys

            import pyttsx3  # noqa: F401

            if sys.platform == "win32":
                # драйвер SAPI5 тянет pywin32; без него движок падает уже в потоке,
                # а кнопка озвучки висела бы мёртвой — проверяем заранее
                import pythoncom  # noqa: F401
            self.available = True
        except Exception:  # noqa: BLE001 — нет пакета или нет голосов в системе
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        import pyttsx3

        try:
            self._engine = pyttsx3.init()
        except Exception:  # noqa: BLE001 — нет голосов/драйвера: молча без озвучки
            self.available = False
            return
        try:
            self._voices = list(self._engine.getProperty("voices"))
        except Exception:  # noqa: BLE001
            self._voices = []
        while True:
            item = self._queue.get()
            if item is None:
                break
            text, lang = item
            # Если в очереди накопились новые фразы — проговариваем только последнюю.
            while not self._queue.empty():
                nxt = self._queue.get_nowait()
                if nxt is None:
                    return
                text, lang = nxt
            self._speak_blocking(text, lang)

    def _pick_voice(self, lang: str) -> None:
        want = lang.lower()  # уже префикс голоса (tts_prefix) — его даёт окно
        for v in self._voices:
            langs = " ".join(
                str(x).lower() for x in (getattr(v, "languages", []) or [])
            )
            hay = f"{v.id} {getattr(v, 'name', '')} {langs}".lower()
            if want in hay:
                try:
                    self._engine.setProperty("voice", v.id)
                except Exception:  # noqa: BLE001
                    pass
                return

    def _speak_blocking(self, text: str, lang: str) -> None:
        if not text.strip():
            return
        try:
            self._pick_voice(lang)
            self._engine.say(text)
            self._engine.runAndWait()
        except Exception:  # noqa: BLE001 — сбой синтеза не должен ронять приложение
            pass

    def speak(self, text: str, lang: str = "en") -> None:
        """Ставит фразу в очередь озвучки (не блокирует)."""
        if self.available and text.strip():
            self._queue.put((text, lang))

    def stop(self) -> None:
        if self.available and self._engine is not None:
            try:
                self._engine.stop()
            except Exception:  # noqa: BLE001
                pass

    def shutdown(self) -> None:
        if self.available:
            self._queue.put(None)
