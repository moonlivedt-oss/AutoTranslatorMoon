"""Значок в трее, глобальные горячие клавиши и «перевести выделенное».

Часть главного окна: методы примешиваются к MainWindow (см. window/main.py)."""

from __future__ import annotations

import logging

from PyQt6.QtCore import (
    QMimeData,
    QTimer,
)
from PyQt6.QtGui import (
    QAction,
    QIcon,
)
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QMenu,
    QSystemTrayIcon,
)

from .. import __version__
from ..paths import asset
from ..ui import theme
from ..ui.icons import make_icon
from ..win import autostart

log = logging.getLogger(__name__)


class TrayMixin:
    def _build_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self._tray = QSystemTrayIcon(self)
        self._tray.setIcon(self._app_icon())
        self._tray.setToolTip(f"AutoTranslator {__version__}")
        menu = QMenu(self)
        act_show = QAction("Показать / скрыть", self)
        act_show.triggered.connect(self._toggle_visible)
        act_lens = QAction("Перевести весь экран", self)
        act_lens.triggered.connect(lambda: self.open_lens())
        act_lens_area = QAction("Перевести кусок экрана", self)
        act_live = QAction("Живой перевод экрана", self)
        act_live.triggered.connect(lambda: self.live_btn.toggle())
        act_lens_area.triggered.connect(self.pick_lens_area)
        act_region = QAction("Выбрать область для чтения", self)
        act_region.triggered.connect(self.pick_region)
        act_read = QAction("Режим чтения", self)
        act_read.triggered.connect(lambda: self.reading_btn.toggle())
        act_quit = QAction("Выход", self)
        act_quit.triggered.connect(self.quit_app)
        for a in (act_show, act_lens, act_live, act_lens_area, act_region, act_read):
            menu.addAction(a)
        menu.addSeparator()
        # те же переключатели, что на странице настроек, — всегда в одном состоянии
        self._act_hotkeys = self._mirror_action("Горячие клавиши", self.hotkeys_chk)
        self._act_fallback = self._mirror_action(
            "Без сети — переводить офлайн", self.fallback_chk
        )
        self._act_close_tray = self._mirror_action(
            "Крестик сворачивает в трей", self.close_tray_chk
        )
        self._act_autostart = self._mirror_action(
            "Запускать вместе с Windows", self.autostart_chk
        )
        self._act_autostart.setVisible(autostart.supported())
        for a in (
            self._act_hotkeys,
            self._act_fallback,
            self._act_close_tray,
            self._act_autostart,
        ):
            menu.addAction(a)
        menu.addSeparator()
        act_settings = QAction("Настройки", self)
        act_settings.triggered.connect(self.open_settings)
        menu.addAction(act_settings)
        act_help = QAction("Справка", self)
        act_help.triggered.connect(self.show_help)
        menu.addAction(act_help)
        menu.addAction(act_quit)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _mirror_action(self, text: str, chk: QCheckBox) -> QAction:
        """Пункт меню трея — зеркало галочки настроек: состояние у них общее."""
        act = QAction(text, self, checkable=True)
        act.setChecked(chk.isChecked())
        act.setEnabled(chk.isEnabled())
        act.toggled.connect(chk.setChecked)  # галочка сама вызовет обработчик

        def sync(on: bool) -> None:
            act.blockSignals(True)
            act.setChecked(on)
            act.blockSignals(False)

        chk.toggled.connect(sync)
        return act

    @staticmethod
    def _app_icon() -> QIcon:
        icon = QIcon(asset("app.ico"))
        return icon if not icon.isNull() else make_icon("scan", theme.ICON_ACCENT, 32)

    def _set_hotkeys_enabled(self, on: bool) -> None:
        self.cfg["hotkeys_enabled"] = on
        if on:
            self._hk_failed = self._hotkeys.register(self.cfg.get("hotkeys") or None)
            self._report_hotkeys()
        else:
            self._hotkeys.unregister()
            self._set_status("Горячие клавиши на паузе", "idle")

    def _report_hotkeys(self) -> None:
        if self._hk_failed:
            self._set_status("Часть горячих клавиш не занята (F1)", "error")
            self.status.setToolTip("Не заняты: " + ", ".join(self._hk_failed))

    def _on_tray_activated(self, reason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._toggle_visible()

    def _toggle_visible(self) -> None:
        if self.isVisible() and not self.isMinimized():
            self.disappear()
        else:
            self.appear()

    def hide_to_tray(self) -> None:
        if self._tray is not None:
            self.disappear()
        else:
            self.showMinimized()

    def _wire_hotkeys(self) -> None:
        h = self._hotkeys
        if not h.available:
            return
        h.lens_pressed.connect(lambda: self.open_lens())
        h.lens_area_pressed.connect(self.pick_lens_area)
        h.live_pressed.connect(lambda: self.live_btn.toggle())
        h.region_pressed.connect(self.pick_region)
        h.reading_pressed.connect(lambda: self.reading_btn.toggle())
        h.recapture_pressed.connect(self.capture_region)
        h.clipboard_pressed.connect(self._hotkey_clipboard)
        h.speak_pressed.connect(self.speak_result)
        h.selection_pressed.connect(self._hotkey_selection)
        on = bool(self.cfg.get("hotkeys_enabled", True))
        if on:
            self._hk_failed = h.register(self.cfg.get("hotkeys") or None)
            self._report_hotkeys()
        self.hotkeys_chk.blockSignals(True)  # уже зарегистрированы — не повторять
        self.hotkeys_chk.setChecked(on)
        self.hotkeys_chk.blockSignals(False)
        if self._tray is not None:
            self._act_hotkeys.blockSignals(True)
            self._act_hotkeys.setChecked(on)
            self._act_hotkeys.blockSignals(False)

    def _hotkey_selection(self, waited: int = 0) -> None:
        """Ctrl+Alt+T: скопировать выделение в активном окне и перевести его.

        Ждём, пока пользователь отпустит модификаторы, иначе наш Ctrl+C смешается
        с зажатым Alt и превратится в другое сочетание.
        """
        if self._hotkeys.modifiers_held() and waited < 1500:
            QTimer.singleShot(40, lambda: self._hotkey_selection(waited + 40))
            return
        clip = QApplication.clipboard()
        before = self._clip_snapshot()  # весь буфер (и картинку), не только текст
        marker = "⁠autotranslator⁠"  # отличить «ничего не скопировалось»
        clip.setText(marker)
        if not self._hotkeys.send_copy():
            clip.setMimeData(before)
            return
        QTimer.singleShot(180, lambda: self._finish_selection(before, marker))

    @staticmethod
    def _clip_snapshot() -> QMimeData:
        """Копия содержимого буфера обмена во всех форматах — чтобы вернуть как было."""
        copy = QMimeData()
        src = QApplication.clipboard().mimeData()
        if src is not None:
            for fmt in src.formats():
                copy.setData(fmt, src.data(fmt))
        return copy

    def _finish_selection(self, before: QMimeData, marker: str, tries: int = 0) -> None:
        clip = QApplication.clipboard()
        text = clip.text()
        if text == marker and tries < 4:  # приложение копирует не мгновенно
            QTimer.singleShot(
                120, lambda: self._finish_selection(before, marker, tries + 1)
            )
            return
        if text == marker or not text.strip():
            clip.setMimeData(before)  # выделения не было — вернуть буфер как был
            self._set_status("Нет выделенного текста", "error")
            return
        if self.cfg.get("restore_clipboard", True):
            clip.setMimeData(before)  # наш Ctrl+C не должен затирать буфер пользователя
        self._show_window()
        self.input.setPlainText(text)
        self._debounce.stop()
        self.translate_now()

    def _hotkey_clipboard(self) -> None:
        self._show_window()
        self.paste_and_translate()
