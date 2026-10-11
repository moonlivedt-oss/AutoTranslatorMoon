"""Мост для игр (Ren'Py без OCR): строка игры, правки перевода, глоссарий.

Часть главного окна: методы примешиваются к MainWindow (см. window/main.py)."""

from __future__ import annotations

import logging

from ..core.langs import (
    AUTO,
    tts_prefix,
)
from ..services.bridge import DEFAULT_PORT, Bridge, parse_glossary
from ..ui import dialogs

log = logging.getLogger(__name__)


class GameMixin:
    def _start_bridge(self) -> None:
        self._bridge: Bridge | None = None
        self._game = ""  # последняя подключившаяся игра
        self._game_src = ""  # оригинал реплики игры, показанной в окне
        self._game_dst = ""
        self._game_dict = 0  # строк в словаре игры (из /hello)
        self._game_progress = (0, 0)  # перевод всей игры: сделано, всего
        # правки — копией: DEFAULTS делит один пустой dict между загрузками
        self.cfg["game_fixes"] = {
            g: {lg: dict(p) for lg, p in langs.items()}
            for g, langs in (self.cfg.get("game_fixes") or {}).items()
        }
        if not self.cfg.get("bridge_enabled", True):
            return
        bridge = Bridge(int(self.cfg.get("bridge_port", DEFAULT_PORT)))
        self._bridge = bridge
        bridge.load_fixes(self.cfg["game_fixes"])
        self._sync_bridge()
        bridge.line_translated.connect(self._on_game_line)
        bridge.bulk_progress.connect(self._on_game_bulk)
        bridge.game_hello.connect(self._on_game_hello)
        if not bridge.start():
            self._bridge = None
            self.status.setToolTip("Мост для игр не запущен: " + bridge.error)

    def _sync_bridge(self, *_args) -> None:
        if getattr(self, "_bridge", None) is None:
            return
        self._bridge.settings = {
            "engine": self.engine_box.currentData() or "online",
            "source": self.source_box.currentData() or AUTO,
            "target": self.target_box.currentData() or "ru",
            "keep_names": bool(self.cfg.get("game_keep_names", True)),
            "glossary": parse_glossary(self.cfg.get("game_glossary", "")),
        }

    def _show_game(self, game: str) -> None:
        if game != self._game:
            self._game = game
            self._game_dict = 0
            self._game_progress = (0, 0)
        self.game_box.setVisible(True)
        self._update_game_chip()

    def _update_game_chip(self) -> None:
        parts = [self._game]
        done, total = self._game_progress
        if total:
            if done >= total:
                parts.append("переведена")
            else:
                parts.append(f"{100 * done // total}%")
        elif self._game_dict:
            parts.append(f"в словаре {self._game_dict}")
        fixes = sum(
            len(p) for p in (self.cfg["game_fixes"].get(self._game) or {}).values()
        )
        if fixes:
            parts.append(f"правок {fixes}")
        self.game_chip.setText(" | ".join(parts))
        tip = [f"Игра: {self._game}"]
        if total:
            tip.append(f"Перевод всей игры: {min(done, total)} из {total} строк")
        if self._game_dict:
            tip.append(f"В словаре игры: {self._game_dict} строк")
        if fixes:
            tip.append(f"Твоих правок перевода: {fixes}")
        self.game_chip.setToolTip("\n".join(tip))

    def _on_game_hello(self, game: str, dict_size: int) -> None:
        self._show_game(game)
        self._game_dict = dict_size
        self._update_game_chip()
        self._set_status(f"Подключилась игра «{game}»", "success", transient=True)

    def _on_game_line(self, game: str, src: str, dst: str) -> None:
        """Игра прислала реплику: показываем и в окне (удобно сверять и править)."""
        self._show_game(game)
        if self.game_tts_chk.isChecked() and self._speaker.available:
            self._speaker.speak(dst, tts_prefix(self.target_box.currentData()))
        if self.reading_btn.isChecked() or self.game_fix_btn.isChecked():
            return  # окно занято режимом чтения или правкой прошлой реплики
        self._game_src, self._game_dst = src, dst
        self.input.blockSignals(True)
        self.input.setPlainText(src)
        self.input.blockSignals(False)
        self._update_meta()
        self.output.setPlainText(dst)
        self.game_fix_btn.setEnabled(True)
        self._set_status(f"Игра: {game}", "success", transient=True)

    def _on_game_bulk(self, game: str, done: int, total: int) -> None:
        self._show_game(game)
        if total:
            self._game_progress = (min(done, total), total)
            self._update_game_chip()
            if done < total:
                self._set_status(
                    f"Перевожу игру «{game}»: {100 * done // total}%", "working"
                )
                self._status_revert.start(4000)
            else:
                self._set_status(
                    f"Игра «{game}» переведена целиком", "success", transient=True
                )
        else:
            self._set_status(f"Перевожу игру «{game}»: +{done} строк", "working")
            self._status_revert.start(4000)

    def _toggle_game_fix(self, on: bool) -> None:
        """Правка перевода реплики игры: вкл — правим поле перевода, выкл — отправить."""
        if on:
            if not self._game_src:
                self.game_fix_btn.setChecked(False)
                return
            self.output.setReadOnly(False)
            self.output.setFocus()
            self.output.moveCursor(self.output.textCursor().MoveOperation.End)
            self._set_status(
                "Исправь перевод и нажми карандаш ещё раз (Ctrl+Enter)", "working"
            )
            self._status_anim.stop()
            return
        self.output.setReadOnly(True)
        dst = self.output.toPlainText().strip()
        if not dst or dst == self._game_dst or self._bridge is None:
            self._set_status("Правка не изменилась", "idle")
            return
        lang = self.target_box.currentData() or "ru"
        self.cfg["game_fixes"].setdefault(self._game, {}).setdefault(lang, {})[
            self._game_src
        ] = dst
        self._bridge.add_fix(self._game, self._game_src, dst)
        self._game_dst = dst
        self._update_game_chip()
        self._save_config()
        self._set_status(
            "Правка ушла в игру: видна со следующего показа реплики", "success"
        )
        self._status_revert.start(4000)

    def _finish_game_fix(self) -> None:
        if self.game_fix_btn.isChecked():
            self.game_fix_btn.setChecked(False)  # -> _toggle_game_fix(False): отправить

    def edit_glossary(self) -> None:
        """Глоссарий для игр: имена из игры + свои термины «термин = перевод»."""
        names = (self._bridge.names.get(self._game) if self._bridge else None) or []
        res = dialogs.edit_glossary(
            self,
            keep_names=bool(self.cfg.get("game_keep_names", True)),
            glossary=self.cfg.get("game_glossary", ""),
            game=self._game,
            names=names,
        )
        if res is None:
            return
        self.cfg["game_keep_names"], self.cfg["game_glossary"] = res
        self._sync_bridge()
        self._save_config()
        n = len(parse_glossary(self.cfg["game_glossary"]))
        self._set_status(f"Глоссарий сохранён: терминов {n}", "success", transient=True)
