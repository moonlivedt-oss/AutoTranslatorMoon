"""Кэш переводов: LRU в памяти + (по желанию) файл на диске.

Кэшем пользуются сразу несколько потоков: воркер окна, потоки моста для игр и
пул, которым онлайн-движок переводит блоки по одному, — поэтому все операции
под замком (``OrderedDict.move_to_end`` при параллельной записи иначе падает).

Файл на диске нужен режиму «Чтение» и новеллам: реплики, переведённые вчера,
сегодня показываются мгновенно и без сети. Пишем атомарно и только если кэш
изменился; битый файл просто игнорируем — это всего лишь кэш.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections import OrderedDict
from pathlib import Path

log = logging.getLogger(__name__)

Key = tuple[str, str, str, str]  # (движок, источник, цель, текст)


class TranslationCache:
    def __init__(self, max_items: int = 5000) -> None:
        self.max_items = max_items
        self._data: OrderedDict[Key, str] = OrderedDict()
        self._lock = threading.Lock()
        self._dirty = False

    def __len__(self) -> int:
        return len(self._data)

    def get(self, key: Key) -> str | None:
        with self._lock:
            val = self._data.get(key)
            if val is not None:
                self._data.move_to_end(key)  # отметить как недавно использованный
            return val

    def put(self, key: Key, value: str) -> None:
        with self._lock:
            if self._data.get(key) == value:
                self._data.move_to_end(key)
                return
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self.max_items:
                self._data.popitem(last=False)  # выбросить самый старый
            self._dirty = True

    def clear(self) -> None:
        with self._lock:
            self._dirty = bool(self._data)
            self._data.clear()

    # ---------- диск ----------
    def load(self, path: Path) -> int:
        """Подгружает кэш с диска. Возвращает число записей (0 — нет/битый файл)."""
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return 0
        except (OSError, ValueError) as e:
            log.warning("кэш переводов не прочитан: %s", e)
            return 0
        if not isinstance(rows, list):
            return 0
        with self._lock:
            for row in rows[-self.max_items :]:
                if (
                    isinstance(row, list)
                    and len(row) == 5
                    and all(isinstance(x, str) for x in row)
                ):
                    self._data[tuple(row[:4])] = row[4]  # type: ignore[index]
            while len(self._data) > self.max_items:
                self._data.popitem(last=False)
            self._dirty = False
            return len(self._data)

    def save(self, path: Path) -> bool:
        """Пишет кэш атомарно, если он менялся. True — записали."""
        with self._lock:
            if not self._dirty:
                return False
            rows = [[*k, v] for k, v in self._data.items()]
            self._dirty = False
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
            return True
        except OSError as e:
            log.warning("кэш переводов не сохранён: %s", e)
            with self._lock:
                self._dirty = True
            return False
