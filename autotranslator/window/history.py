"""История переводов, избранное и справка.

Часть главного окна: методы примешиваются к MainWindow (см. window/main.py)."""

from __future__ import annotations

import logging

from PyQt6.QtCore import (
    QPoint,
)
from PyQt6.QtWidgets import (
    QMenu,
)

from ..services.hotkeys import DEFAULTS as HOTKEY_DEFAULTS
from ..ui import dialogs

log = logging.getLogger(__name__)


class HistoryMixin:
    def _push_history(self, src: str, dst: str) -> None:
        if not src or not dst:
            return
        hist = self.cfg.setdefault("history", [])
        if hist and hist[-1].get("src") == src and hist[-1].get("dst") == dst:
            return  # не дублируем подряд одинаковое
        hist.append({"src": src, "dst": dst})
        limit = int(self.cfg.get("history_max", 50))
        if len(hist) > limit:
            del hist[: len(hist) - limit]

    @staticmethod
    def _label_for(item: dict) -> str:
        src = (item.get("src", "") or "").replace("\n", " ").strip()
        return (src[:46] + "…") if len(src) > 46 else (src or "—")

    def _is_favorite(self, item: dict) -> bool:
        key = (item.get("src"), item.get("dst"))
        return any(
            (f.get("src"), f.get("dst")) == key for f in self.cfg.get("favorites", [])
        )

    def _toggle_favorite(self, item: dict) -> None:
        favs = self.cfg.setdefault("favorites", [])
        key = (item.get("src"), item.get("dst"))
        for i, f in enumerate(favs):
            if (f.get("src"), f.get("dst")) == key:
                del favs[i]
                self._set_status("Убрано из избранного", "idle")
                return
        favs.append({"src": item.get("src", ""), "dst": item.get("dst", "")})
        self._set_status("★ В избранном", "success", transient=True)

    def _add_history_entry(self, menu: QMenu, item: dict, *, fav: bool) -> None:
        sub = menu.addMenu(("★ " if fav else "") + self._label_for(item))
        sub.setToolTip(item.get("dst", ""))
        recall = sub.addAction("Вставить")
        recall.triggered.connect(lambda _=False, it=item: self._recall_history(it))
        star = sub.addAction("Убрать из избранного" if fav else "★ В избранное")
        star.triggered.connect(lambda _=False, it=item: self._toggle_favorite(it))

    def show_history_menu(self) -> None:
        favs = self.cfg.get("favorites", [])
        hist = self.cfg.get("history", [])
        menu = QMenu(self)
        if not favs and not hist:
            menu.addAction("История пуста").setEnabled(False)
        if favs:
            menu.addAction("★ Избранное").setEnabled(False)
            for item in reversed(favs[-10:]):
                self._add_history_entry(menu, item, fav=True)
            menu.addSeparator()
        if hist:
            menu.addAction("Недавние").setEnabled(False)
            for item in reversed(hist[-12:]):
                self._add_history_entry(menu, item, fav=self._is_favorite(item))
            menu.addSeparator()
        if favs or hist:
            menu.addAction("Вся история, поиск, экспорт…  Ctrl+H").triggered.connect(
                self.show_full_history
            )
        if hist:
            clear = menu.addAction("Очистить историю")
            clear.triggered.connect(lambda: self.cfg.__setitem__("history", []))
        menu.exec(
            self.title_bar.history_btn.mapToGlobal(
                QPoint(0, self.title_bar.history_btn.height())
            )
        )

    def show_full_history(self) -> None:
        dlg = dialogs.HistoryDialog(
            self, self.cfg, self._recall_history, self._toggle_favorite
        )
        dlg.exec()

    def show_help(self) -> None:
        binds = {**HOTKEY_DEFAULTS, **(self.cfg.get("hotkeys") or {})}
        dialogs.show_help(
            self,
            binds if self._hotkeys.available else {},
            hotkeys_on=self._hotkeys.active,
            failed=self._hk_failed,
        )

    def _recall_history(self, item: dict) -> None:
        self._debounce.stop()
        self.input.setPlainText(item.get("src", ""))
        self.output.setPlainText(item.get("dst", ""))
        self._debounce.stop()
