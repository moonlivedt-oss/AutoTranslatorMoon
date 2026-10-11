"""Движки перевода: онлайн (Google через deep-translator) и офлайн (Argos Translate).

У обоих единый интерфейс translate(text, source, target). source может быть "auto".
Ошибки не глушим наглухо — пробрасываем понятное сообщение, окно покажет его в статусе.

Поверх движков добавлены две вещи, важные для режима «Чтение», где один и тот же
кадр перечитывается по многу раз:
- **кэш** уже переведённых строк (LRU) — мгновенный ответ на повтор, не дёргаем сеть;
- **повтор с задержкой** для онлайна — сеть иногда моргает, один ретрай спасает кадр.
"""

from __future__ import annotations

import threading
import time

from .cache import TranslationCache
from .langs import AUTO, LANGUAGES, argos_code, detect_lang, google_code

__all__ = [
    "CACHE",
    "NetworkError",
    "clear_cache",
    "detect_lang",
    "make_engine",
    "split_chunks",
]

# --- кэш переводов (общий для окна, моста и пула потоков) ---------------------
CACHE = TranslationCache()


def _cache_get(key: tuple) -> str | None:
    return CACHE.get(key)


def _cache_put(key: tuple, value: str) -> None:
    CACHE.put(key, value)


def clear_cache() -> None:
    CACHE.clear()


class NetworkError(RuntimeError):
    """Онлайн-перевод не удался из-за сети/лимита — можно подстраховаться офлайном."""


# Google в ответе сообщает язык источника — коды приводим к нашим (zh-CN и т.п.)
_GOOGLE_TO_CODE = {v["google"].lower(): k for k, v in LANGUAGES.items()}
_GOOGLE_TO_CODE.update({"zh": "zh-CN", "zh-cn": "zh-CN", "zh-tw": "zh-CN"})

_GTX_URL = "https://translate.googleapis.com/translate_a/single"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class OnlineEngine:
    """Google Translate. Требует интернет, шлёт текст наружу.

    Основной путь — JSON-эндпоинт ``translate.googleapis.com`` через одну
    keep-alive сессию: он быстрее веб-страницы (без повторного TLS-рукопожатия
    на каждый кадр) и не упирается в её лимит «429 Too Many Requests». Если он
    недоступен — запасной путь через deep-translator.
    """

    name = "online"
    label = "Онлайн (Google)"

    def __init__(self) -> None:
        import requests  # ленивый импорт (идёт вместе с deep-translator)

        self._session = requests.Session()
        self._session.headers["User-Agent"] = _UA
        self._fallback_cls = None
        self._detected: dict[str, str] = {}  # текст -> язык, определённый Google
        self._det_lock = threading.Lock()

    def detected(self, text: str) -> str | None:
        """Язык источника, который определил Google для этого текста (если знаем)."""
        with self._det_lock:
            return self._detected.get(text.strip())

    def _remember_detected(self, text: str, code: str) -> None:
        lang = _GOOGLE_TO_CODE.get((code or "").lower())
        if not lang:
            return
        with self._det_lock:
            if len(self._detected) > 256:
                self._detected.clear()
            self._detected[text] = lang

    def _gtx(self, text: str, source: str, target: str) -> str:
        src = AUTO if source == AUTO else google_code(source)
        resp = self._session.post(
            _GTX_URL,
            params={"client": "gtx", "sl": src, "tl": google_code(target), "dt": "t"},
            data={"q": text},
            timeout=(5, 20),
        )
        resp.raise_for_status()
        data = resp.json()
        if source == AUTO and len(data) > 2 and isinstance(data[2], str):
            self._remember_detected(text, data[2])
        return "".join(seg[0] for seg in (data[0] or []) if seg and seg[0])

    def _deep(self, text: str, source: str, target: str) -> str:
        if self._fallback_cls is None:
            from deep_translator import GoogleTranslator

            self._fallback_cls = GoogleTranslator
        src = AUTO if source == AUTO else google_code(source)
        return (
            self._fallback_cls(source=src, target=google_code(target)).translate(text)
            or ""
        )

    def _part(self, text: str, source: str, target: str) -> str:
        try:
            return self._gtx(text, source, target)
        except Exception as first:  # noqa: BLE001
            try:
                return self._deep(text, source, target)
            except Exception:  # noqa: BLE001
                raise first from None

    def _once(self, text: str, source: str, target: str) -> str:
        # у Google лимит ~5000 символов на запрос — длинный текст режем по абзацам
        out = "\n".join(self._part(p, source, target) for p in split_chunks(text))
        return out.rstrip("\n")

    def translate(self, text: str, source: str, target: str) -> str:
        text = text.strip()
        if not text:
            return ""
        key = ("online", source, target, text)
        cached = _cache_get(key)
        if cached is not None:
            return cached
        result = self._with_retries(text, source, target)
        _cache_put(key, result)
        return result

    def _with_retries(self, text: str, source: str, target: str) -> str:
        """Перевод без кэша, с повторами при морганиях сети."""
        # Сеть иногда моргает — повторяем с растущей паузой (0.5 с, 1 с). На «429»
        # (лимит Google) не повторяем: это только продлевает бан, а окно быстрее
        # получит ошибку и сможет перевести офлайн.
        last_err: Exception | None = None
        for attempt in range(RETRIES):
            try:
                return self._once(text, source, target)
            except Exception as e:  # noqa: BLE001 — сетевые ошибки бывают разными
                last_err = e
                if _is_rate_limit(e):
                    break
                if attempt + 1 < RETRIES:
                    time.sleep(0.5 * (2**attempt))
        raise NetworkError(_friendly_net_error(last_err))

    def translate_many(self, texts: list[str], source: str, target: str) -> list[str]:
        """Переводит много коротких кусков ОДНИМ запросом (строки через перевод строки).

        Нужно экранному переводу: на экране десятки блоков, и запрос на каждый —
        это секунды ожидания. Уже переведённое берётся из кэша. Если Google
        вдруг склеил/разбил строки и их число не сошлось — переводим по одному.
        """
        out: list[str | None] = [None] * len(texts)
        todo: list[int] = []
        for i, raw in enumerate(texts):
            t = " ".join(raw.split())
            if not t:
                out[i] = ""
                continue
            cached = _cache_get(("online", source, target, t))
            if cached is not None:
                out[i] = cached
            else:
                todo.append(i)
        if todo:
            units = [" ".join(texts[i].split()) for i in todo]
            # склейку целиком не кэшируем: она одноразовая и вытесняла бы из кэша
            # нужные строки — кэшируется каждая строка по отдельности
            joined = self._with_retries("\n".join(units), source, target)
            parts = joined.split("\n")
            if len(parts) == len(units):
                for i, unit, res in zip(todo, units, parts, strict=True):
                    out[i] = res.strip()
                    _cache_put(("online", source, target, unit), out[i])
            else:
                from concurrent.futures import ThreadPoolExecutor

                with ThreadPoolExecutor(max_workers=6) as pool:
                    results = list(
                        pool.map(lambda u: self.translate(u, source, target), units)
                    )
                for i, res in zip(todo, results, strict=True):
                    out[i] = res
        return [o or "" for o in out]


class OfflineEngine:
    """Argos Translate — локально, без интернета. Модель языковой пары ставится один раз."""

    name = "offline"
    label = "Офлайн (Argos)"

    def __init__(self) -> None:
        import argostranslate.translate  # проверяем, что пакет вообще есть

        self._mod = argostranslate.translate
        self._ready: set[tuple[str, str]] = set()  # пары, для которых модель уже есть

    def detected(self, text: str) -> str | None:
        return detect_lang(text) if text.strip() else None

    def has_model(self, text: str, source: str, target: str) -> bool:
        """Переведёт ли офлайн БЕЗ скачивания модели (для подстраховки без сети)."""
        src = detect_lang(text) if source == AUTO else source
        src, tgt = argos_code(src), argos_code(target)
        if src == tgt or (src, tgt) in self._ready:
            return True
        try:
            return self._has_translation(src, tgt)
        except Exception:  # noqa: BLE001 — битая установка Argos
            return False

    def _has_translation(self, source: str, target: str) -> bool:
        installed = {lang.code: lang for lang in self._mod.get_installed_languages()}
        if source not in installed or target not in installed:
            return False
        return installed[source].get_translation(installed[target]) is not None

    def _ensure_package(self, source: str, target: str) -> None:
        """Ставит модель source->target, если её ещё нет (тянет из сети один раз).

        Прямой модели для многих пар нет (например, ru→ja) — тогда ставим две
        через английский: Argos сам строит составной перевод source→en→target.
        """
        if (source, target) in self._ready:
            return
        if self._has_translation(source, target):
            self._ready.add((source, target))
            return
        import argostranslate.package as pkg

        try:
            pkg.update_package_index()
            available = pkg.get_available_packages()
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                "Нет офлайн-модели для этой пары, а скачать её не удалось "
                f"(нужен интернет один раз): {e}"
            ) from e

        def find(a: str, b: str):
            return next(
                (p for p in available if p.from_code == a and p.to_code == b), None
            )

        direct = find(source, target)
        if direct is not None:
            plan = [direct]
        else:
            plan = [find(source, "en"), find("en", target)]
            if None in plan or "en" in (source, target):
                raise RuntimeError(
                    f"Нет офлайн-модели {source}→{target} в индексе Argos. "
                    "Попробуйте онлайн-движок или другую пару языков."
                )
        for p in plan:
            if not self._has_translation(p.from_code, p.to_code):
                pkg.install_from_path(p.download())
        self._ready.add((source, target))

    def translate(self, text: str, source: str, target: str) -> str:
        text = text.strip()
        if not text:
            return ""
        src = detect_lang(text) if source == AUTO else source
        src, tgt = argos_code(src), argos_code(target)
        if src == tgt:
            return text
        key = ("offline", src, tgt, text)
        cached = _cache_get(key)
        if cached is not None:
            return cached
        self._ensure_package(src, tgt)
        result = self._mod.translate(text, src, tgt)
        _cache_put(key, result)
        return result

    def translate_many(self, texts: list[str], source: str, target: str) -> list[str]:
        # локальная модель: по одному (кэш всё равно спасает повторяющиеся блоки)
        return [self.translate(" ".join(t.split()), source, target) for t in texts]


CHUNK_MAX = 4500
RETRIES = 3


def split_chunks(text: str, limit: int = CHUNK_MAX) -> list[str]:
    """Режет текст на куски <= limit символов, стараясь не рвать абзацы/предложения."""
    if len(text) <= limit:
        return [text]
    pieces: list[str] = []
    for para in text.split("\n"):
        while len(para) > limit:
            cut = max(para.rfind(". ", 0, limit), para.rfind(" ", 0, limit))
            cut = cut + 1 if cut > limit // 2 else limit
            pieces.append(para[:cut].rstrip())
            para = para[cut:].lstrip()
        pieces.append(para)
    chunks: list[str] = []
    cur = ""
    for piece in pieces:
        if cur and len(cur) + 1 + len(piece) > limit:
            chunks.append(cur)
            cur = piece
        else:
            cur = f"{cur}\n{piece}" if cur else piece
    if cur:
        chunks.append(cur)
    return chunks


def _is_rate_limit(err: Exception) -> bool:
    msg = str(err).lower()
    return "429" in msg or "too many" in msg


def _friendly_net_error(err: Exception | None) -> str:
    msg = str(err or "")
    low = msg.lower()
    if "429" in low or "too many" in low:
        return (
            "Google временно ограничил запросы — подождите минуту или включите офлайн."
        )
    if any(
        w in low
        for w in ("connection", "resolve", "timed out", "network", "getaddrinfo")
    ):
        return "Нет сети для онлайн-перевода. Проверьте интернет или включите офлайн-движок."
    return f"Ошибка онлайн-перевода: {msg}" if msg else "Онлайн-перевод недоступен."


def make_engine(name: str):
    """Фабрика по имени движка. Кидает понятную ошибку, если движок не установлен."""
    try:
        return OnlineEngine() if name == "online" else OfflineEngine()
    except ImportError as e:
        need = "requests / deep-translator" if name == "online" else "argostranslate"
        raise RuntimeError(
            f"Движок '{name}' недоступен: не установлен {need} ({e})."
        ) from e
