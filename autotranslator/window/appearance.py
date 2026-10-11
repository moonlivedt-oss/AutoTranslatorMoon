"""Тема (тёмная / светлая / как в Windows), оттенок и масштаб шрифта.

Часть главного окна: методы примешиваются к MainWindow (см. window/main.py)."""

from __future__ import annotations

import logging

from PyQt6.QtCore import (
    QPoint,
)
from PyQt6.QtWidgets import (
    QMenu,
)

from ..ui import theme
from ..ui.icons import make_icon
from ..ui.theme import PALETTE, build_qss

log = logging.getLogger(__name__)

FONT_MIN, FONT_MAX = 70, 180


def _system_is_dark() -> bool:
    """Тёмная ли тема в Windows (реестр). При любой ошибке считаем тёмной."""
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        winreg.CloseKey(key)
        return val == 0
    except Exception:  # noqa: BLE001 — нет реестра/ключа → разумный дефолт
        return True


class AppearanceMixin:
    def _effective_dark(self) -> bool:
        """Тёмная ли тема сейчас с учётом режима (система/светлая/тёмная)."""
        mode = self.cfg.get("appearance_mode", self.cfg.get("theme", "dark"))
        if mode == "system":
            return _system_is_dark()
        return mode != "light"

    def _resolve_flavor(self) -> str:
        """Имя флейвора Catppuccin под текущий режим (латте для светлой)."""
        dark = self._effective_dark()
        self.cfg["theme"] = "dark" if dark else "light"  # для обратной совместимости
        if not dark:
            return "latte"
        flavor = self.cfg.get("flavor", "mocha")
        return flavor if flavor in theme.DARK_FLAVORS else "mocha"

    def toggle_theme(self) -> None:
        """ЛКМ по кнопке темы: тёмная → светлая → системная → …"""
        order = ["dark", "light", "system"]
        cur = self.cfg.get("appearance_mode", "dark")
        nxt = order[(order.index(cur) + 1) % len(order)] if cur in order else "dark"
        self.cfg["appearance_mode"] = nxt
        theme.set_theme(self._resolve_flavor())
        self._refresh_theme()
        label = {
            "dark": "Тёмная тема",
            "light": "Светлая тема",
            "system": "Тема: как в системе",
        }[nxt]
        self._set_status(label, "success", transient=True)

    def show_flavor_menu(self) -> None:
        """ПКМ по кнопке темы: выбор флейвора Catppuccin и режима."""
        menu = QMenu(self)
        cur_flavor = self.cfg.get("flavor", "mocha")
        head = menu.addAction("Флейвор (тёмная тема)")
        head.setEnabled(False)
        for fl in theme.DARK_FLAVORS:
            act = menu.addAction(
                ("● " if fl == cur_flavor else "○ ") + theme.FLAVOR_NAMES[fl]
            )
            act.triggered.connect(lambda _=False, f=fl: self._pick_flavor(f))
        menu.addSeparator()
        cur_mode = self.cfg.get("appearance_mode", "dark")
        for mode, title in (
            ("dark", "Тёмная"),
            ("light", "Светлая"),
            ("system", "Как в системе"),
        ):
            act = menu.addAction(("● " if mode == cur_mode else "○ ") + title)
            act.triggered.connect(lambda _=False, m=mode: self._pick_mode(m))
        menu.exec(
            self.title_bar.theme_btn.mapToGlobal(
                QPoint(0, self.title_bar.theme_btn.height())
            )
        )

    def _pick_flavor(self, flavor: str) -> None:
        self.cfg["flavor"] = flavor
        if not self._effective_dark():
            self.cfg["appearance_mode"] = "dark"  # выбор флейвора включает тёмную
        theme.set_theme(self._resolve_flavor())
        self._refresh_theme()
        self._set_status(
            "Флейвор: " + theme.FLAVOR_NAMES[flavor], "success", transient=True
        )

    def _pick_mode(self, mode: str) -> None:
        self.cfg["appearance_mode"] = mode
        theme.set_theme(self._resolve_flavor())
        self._refresh_theme()

    def _check_system_theme(self) -> None:
        """Тик авто-слежения: если режим «система» и тема сменилась — применяем."""
        if self.cfg.get("appearance_mode") != "system":
            return
        want_dark = _system_is_dark()
        if want_dark != (self.cfg.get("theme") == "dark"):
            theme.set_theme(self._resolve_flavor())
            self._refresh_theme()

    def _refresh_theme(self) -> None:
        self.setStyleSheet(build_qss(PALETTE, self.cfg.get("font_scale", 100)))
        for btn, name in self._icons:
            btn.setIcon(make_icon(name, theme.ICON))
        self._set_reading_icon(self.reading_btn.isChecked())
        self._set_live_icon(self._live is not None)
        self.settings_btn.setIcon(
            make_icon(
                "gear",
                PALETTE["accent"] if self.settings_btn.isChecked() else theme.ICON,
            )
        )
        self._sync_theme_controls()
        if self._subtitle is not None:
            self._subtitle.apply_style()
        if self._overlay is not None:
            self._overlay.update()

    def _sync_theme_controls(self) -> None:
        """Сегменты темы и список оттенков в настройках — по текущему режиму."""
        mode = self.cfg.get("appearance_mode", "dark")
        for btn in self.mode_group.buttons():
            btn.setChecked(btn.property("mode") == mode)
        self.flavor_box.setCurrentIndex(
            max(0, self.flavor_box.findData(self.cfg.get("flavor", "mocha")))
        )
        self.flavor_box.setEnabled(self._effective_dark())

    def _bump_font(self, delta: int) -> None:
        scale = max(
            FONT_MIN, min(FONT_MAX, int(self.cfg.get("font_scale", 100)) + delta)
        )
        self.cfg["font_scale"] = scale
        self.font_label.setText(f"{scale}%")
        self.setStyleSheet(build_qss(PALETTE, scale))
        if self._subtitle is not None:
            self._subtitle.set_font_scale(scale)
        self._set_status(f"Шрифт: {scale}%", "idle")
