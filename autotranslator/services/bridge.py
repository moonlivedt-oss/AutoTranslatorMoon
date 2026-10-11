"""Локальный «мост» для игр: игра шлёт реплики — AutoTranslator возвращает перевод.

Модуль ``zzz_testmenu_translate.rpy`` (из Test Menu Moon) внутри Ren'Py-игры берёт
текст реплик прямо из движка — без скриншотов и OCR, поэтому без единой ошибки
распознавания, — и спрашивает перевод здесь. Перевод игра показывает сама, в
своём окне диалога.

Протокол — HTTP + JSON, только на 127.0.0.1 (наружу не слушаем):

- ``GET  /info`` -> {"app", "version", "source", "target", "engine"}
- ``POST /hello`` {"game", "names": [...], "dict": N, "total": N}  (v2)
  игра подключилась: имена персонажей (их не переводим) и размер словаря
  -> {"ok": true, "fix_rev": N}
- ``POST /translate`` {"texts": [...], "game": "Имя", "bulk": false,
  "done": N, "total": N}  (done/total — прогресс перевода всей игры, v2)
  -> {"tr": [перевод | null, ...], "target": "ru", "fix_rev": N}
- ``GET  /fixes?game=Имя&since=N``  (v2) — правки перевода, сделанные в окне
  -> {"rev": M, "items": [[оригинал, перевод], ...]}

``bulk`` — фоновый перевод всей игры заранее (большие пачки), обычные запросы —
текущая и предсказанные реплики; они обрабатываются параллельно, так что
перевод «на будущее» не задерживает то, что игрок читает сейчас.

Правки: игрок исправил перевод реплики в окне — правка хранится здесь (по игре и
языку, переживает перезапуск) и уходит в словарь игры: в каждом ответе есть
``fix_rev``, и игра, увидев новый номер, забирает правки через ``/fixes``.

Безопасность: слушаем только 127.0.0.1, но запросы к localhost умеет слать и
любая открытая веб-страница. Поэтому отвечаем только «своим»: заголовок Host —
127.0.0.1/localhost (защита от DNS rebinding), а запросы с ``Origin`` /
``Sec-Fetch-Site`` (их ставит только браузер) отклоняем; POST — только JSON
(браузер не пошлёт его на чужой адрес без preflight, а OPTIONS мы не отвечаем).
"""

from __future__ import annotations

import json
import logging
import queue
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from PyQt6.QtCore import QObject, pyqtSignal

from ..core import renpy_text

log = logging.getLogger(__name__)

DEFAULT_PORT = 47811
VERSION = 2
MAX_BODY = 4 * 1024 * 1024
MAX_NAMES = 200
MAX_TEXTS = 2000  # строк в одном запросе /translate
_LOCAL_HOSTS = ("127.0.0.1", "localhost")


def _int(value, default: int = 0) -> int:
    """Число из JSON игры; мусор -> default (а не обрыв соединения)."""
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def request_allowed(headers, port: int) -> bool:
    """Запрос от игры, а не от веб-страницы в браузере."""
    host = (headers.get("Host") or "").strip().lower()
    allowed = set(_LOCAL_HOSTS) | {f"{h}:{port}" for h in _LOCAL_HOSTS}
    if host not in allowed:
        return False
    return not (headers.get("Origin") or headers.get("Sec-Fetch-Site"))


def parse_glossary(text: str) -> dict[str, str]:
    """Строки «термин = перевод» (или просто «термин» — не переводить) -> словарь."""
    out: dict[str, str] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        src, sep, dst = line.partition("=")
        src, dst = src.strip(), dst.strip()
        if src:
            out[src] = dst if sep and dst else src
    return out


class Bridge(QObject):
    """HTTP-сервер в фоновом потоке + Qt-сигналы о происходящем для окна."""

    line_translated = pyqtSignal(
        str, str, str
    )  # игра, оригинал, перевод (живые реплики)
    bulk_progress = pyqtSignal(
        str, int, int
    )  # игра, сделано строк, всего (0 — неизвестно)
    game_hello = pyqtSignal(str, int)  # игра подключилась: имя, строк в её словаре
    failed = pyqtSignal(str)

    def __init__(self, port: int = DEFAULT_PORT) -> None:
        super().__init__()
        self.port = port
        # снимок настроек окна; GUI обновляет его, потоки сервера только читают
        self.settings = {
            "engine": "online",
            "source": "auto",
            "target": "ru",
            "keep_names": True,  # имена персонажей из игры не переводить
            "glossary": {},  # свои термины {термин: перевод}
        }
        # правки перевода: {игра: {язык: {оригинал: перевод}}}; порядок — по номеру правки
        self.fixes: dict[str, dict[str, dict[str, str]]] = {}
        self._fix_log: list[
            tuple[int, str, str, str, str]
        ] = []  # (rev, игра, язык, src, dst)
        self.fix_rev = 0
        self.names: dict[str, list[str]] = {}  # игра -> имена персонажей
        self._lock = threading.Lock()
        # пул готовых движков: ThreadingHTTPServer заводит поток на каждое
        # соединение, и движок «на поток» создавался бы (с TLS-рукопожатием)
        # заново на каждый запрос
        self._pool: dict[str, queue.SimpleQueue] = {}
        self._offline_lock = threading.Lock()  # Argos не любит параллельные вызовы
        self._server: ThreadingHTTPServer | None = None
        self.error = ""

    # ---------- правки и глоссарий ----------
    def load_fixes(self, data: dict) -> None:
        """Правки из конфига окна (переживают перезапуск)."""
        with self._lock:
            self.fixes = {}
            self._fix_log = []
            for game, langs in (data or {}).items():
                for lang, pairs in (langs or {}).items():
                    for src, dst in (pairs or {}).items():
                        self._add_fix_locked(str(game), str(lang), str(src), str(dst))

    def _add_fix_locked(self, game: str, lang: str, src: str, dst: str) -> None:
        self.fixes.setdefault(game, {}).setdefault(lang, {})[src] = dst
        self.fix_rev += 1
        self._fix_log.append((self.fix_rev, game, lang, src, dst))

    def add_fix(self, game: str, src: str, dst: str) -> None:
        """Правка перевода из окна: игра заберёт её при следующем запросе."""
        lang = self.settings["target"]
        with self._lock:
            self._add_fix_locked(game, lang, src, dst)

    def fixes_since(
        self, game: str, lang: str, since: int
    ) -> tuple[int, list[list[str]]]:
        with self._lock:
            items = [
                [src, dst]
                for rev, g, lg, src, dst in self._fix_log
                if rev > since and g == game and lg == lang
            ]
            return self.fix_rev, items

    def fixes_for(self, game: str, lang: str) -> dict[str, str]:
        with self._lock:
            return dict(self.fixes.get(game, {}).get(lang, {}))

    def glossary_for(self, game: str) -> dict[str, str]:
        g: dict[str, str] = {}
        if self.settings.get("keep_names", True):
            for n in self.names.get(game, ()):
                g[n] = n
        g.update(self.settings.get("glossary") or {})
        return g

    # ---------- жизненный цикл ----------
    def start(self) -> bool:
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:  # тихо: без консоли
                pass

            def _guard(self) -> bool:
                if request_allowed(self.headers, bridge.port):
                    return True
                log.warning(
                    "мост: отклонён запрос %s (Host=%r, Origin=%r)",
                    self.path,
                    self.headers.get("Host"),
                    self.headers.get("Origin"),
                )
                self._send(403, {"error": "forbidden"})
                return False

            def _send(self, code: int, payload: dict) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _body(self) -> dict:
                ctype = (self.headers.get("Content-Type") or "").lower()
                if "json" not in ctype:
                    raise ValueError("expected application/json")
                size = _int(self.headers.get("Content-Length"))
                if size <= 0 or size > MAX_BODY:
                    raise ValueError("bad size")
                req = json.loads(self.rfile.read(size).decode("utf-8"))
                if not isinstance(req, dict):
                    raise ValueError("bad body")
                return req

            def do_GET(self) -> None:  # noqa: N802
                if not self._guard():
                    return
                url = urlsplit(self.path)
                if url.path == "/info":
                    s = bridge.settings
                    self._send(
                        200,
                        {
                            "app": "AutoTranslator",
                            "version": VERSION,
                            "source": s["source"],
                            "target": s["target"],
                            "engine": s["engine"],
                            "fix_rev": bridge.fix_rev,
                        },
                    )
                elif url.path == "/fixes":
                    q = parse_qs(url.query)
                    game = (q.get("game") or ["игра"])[0]
                    try:
                        since = int((q.get("since") or ["0"])[0])
                    except ValueError:
                        since = 0
                    lang = (q.get("lang") or [bridge.settings["target"]])[0]
                    rev, items = bridge.fixes_since(game, lang, since)
                    self._send(200, {"rev": rev, "items": items})
                else:
                    self._send(404, {"error": "not found"})

            def do_POST(self) -> None:  # noqa: N802
                if not self._guard():
                    return
                path = urlsplit(self.path).path
                try:
                    req = self._body()
                except Exception as e:  # noqa: BLE001
                    self._send(400, {"error": str(e)})
                    return
                game = str(req.get("game") or "игра")
                if path == "/hello":
                    raw = req.get("names")
                    names = (
                        [str(n) for n in raw][:MAX_NAMES]
                        if isinstance(raw, list)
                        else []
                    )
                    bridge.names[game] = [n for n in names if n.strip()]
                    dict_size = _int(req.get("dict"))
                    bridge.game_hello.emit(game, dict_size)
                    total = _int(req.get("total"))
                    if total:
                        bridge.bulk_progress.emit(game, dict_size, total)
                    self._send(200, {"ok": True, "fix_rev": bridge.fix_rev})
                    return
                if path != "/translate":
                    self._send(404, {"error": "not found"})
                    return
                raw = req.get("texts")
                if not isinstance(raw, list) or len(raw) > MAX_TEXTS:
                    self._send(400, {"error": "texts: list expected"})
                    return
                texts = [str(t) for t in raw]
                try:
                    tr = bridge.translate(texts, game)
                except Exception as e:  # noqa: BLE001 — нет сети и т.п.
                    log.warning("мост: перевод не удался: %s", e)
                    bridge.failed.emit(str(e))
                    self._send(502, {"error": str(e)})
                    return
                if req.get("bulk"):
                    done = _int(req.get("done")) + len(texts)
                    bridge.bulk_progress.emit(game, done, _int(req.get("total")))
                elif texts and tr and tr[-1]:
                    bridge.line_translated.emit(game, texts[-1], tr[-1])
                self._send(
                    200,
                    {
                        "tr": tr,
                        "target": bridge.settings["target"],
                        "fix_rev": bridge.fix_rev,
                    },
                )

        try:
            self._server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        except OSError as e:
            self.error = f"порт {self.port} занят: {e}"
            log.warning("мост не запущен: %s", self.error)
            return False
        self._server.daemon_threads = True
        threading.Thread(
            target=self._server.serve_forever, name="bridge", daemon=True
        ).start()
        return True

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    # ---------- перевод ----------
    @contextmanager
    def _engine(self, name: str):
        """Движок из пула на время запроса (нет свободного — создаём ещё один)."""
        with self._lock:
            pool = self._pool.setdefault(name, queue.SimpleQueue())
        try:
            engine = pool.get_nowait()
        except queue.Empty:
            from ..core.translate import make_engine

            engine = make_engine(name)
        try:
            yield engine
        finally:
            pool.put(engine)

    def translate(self, texts: list[str], game: str = "") -> list[str | None]:
        s = dict(self.settings)
        if not texts:
            return []
        if s["source"] != "auto" and s["source"] == s["target"]:
            return list(texts)
        # правки игрока важнее переводчика: такие строки в движок не отправляем
        fixed = self.fixes_for(game, s["target"])
        out: list[str | None] = [fixed.get(t) for t in texts]
        todo = [i for i, v in enumerate(out) if v is None]
        if not todo:
            return out
        glossary = self.glossary_for(game)
        batch = [texts[i] for i in todo]
        with self._engine(s["engine"]) as engine:
            if s["engine"] == "offline":
                with self._offline_lock:
                    res = renpy_text.translate_many(
                        engine, batch, s["source"], s["target"], glossary
                    )
            else:
                res = renpy_text.translate_many(
                    engine, batch, s["source"], s["target"], glossary
                )
        for i, v in zip(todo, res, strict=True):
            out[i] = v
        return out
