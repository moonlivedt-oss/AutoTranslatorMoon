"""Главное окно AutoTranslator: каркас, поля перевода, статус, настройки окна."""

from __future__ import annotations

import logging
import time

from PyQt6.QtCore import (
    QByteArray,
    QEasingCurve,
    QPoint,
    QSize,
    Qt,
    QThread,
    QTimer,
    QVariantAnimation,
    pyqtSignal,
    pyqtSlot,
)
from PyQt6.QtGui import (
    QColor,
    QGuiApplication,
    QIcon,
    QKeySequence,
    QShortcut,
)
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizeGrip,
    QSizePolicy,
    QStackedWidget,
    QSystemTrayIcon,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core import config
from ..core.langs import (
    AUTO,
    LANGUAGES,
    detect_lang,
    tts_prefix,
)
from ..core.langs import (
    name as lang_name,
)
from ..core.translate import CACHE, clear_cache
from ..live import LiveSession, LiveWorkers
from ..paths import CACHE_FILE, asset
from ..services import advance
from ..services.hotkeys import DEFAULTS as HOTKEY_DEFAULTS
from ..services.hotkeys import HotkeyManager
from ..services.tts import Speaker
from ..ui import anim, theme
from ..ui.icons import make_icon
from ..ui.lens import LensOverlay
from ..ui.overlay import ReadingOverlay, SubtitleOverlay
from ..ui.theme import PALETTE, build_qss
from ..ui.tile import Tile
from ..ui.widgets import BusyLine, Field, TitleBar, text_button, tool_button
from ..win.winutil import exclude_from_capture
from ..worker import Worker
from .appearance import AppearanceMixin
from .game import GameMixin
from .history import HistoryMixin
from .inputs import InputMixin
from .reading import ReadingMixin
from .screen import ScreenMixin
from .settings import SettingsMixin
from .tray import TrayMixin

log = logging.getLogger(__name__)

SHUTDOWN_WAIT_S = 2.0  # сколько при выходе ждать фоновые потоки (сеть, OCR)
AUTOSAVE_MS = 20000  # периодическое сохранение настроек (на случай сбоя/выключения)


def _hk_label(combo: str) -> str:
    return "+".join(part.capitalize() for part in combo.split("+")) if combo else ""


class MainWindow(
    TrayMixin,
    AppearanceMixin,
    HistoryMixin,
    InputMixin,
    ReadingMixin,
    GameMixin,
    ScreenMixin,
    SettingsMixin,
    QWidget,
):
    """Плавающее окно переводчика. Части поведения — в примесях (см. импорты)."""

    # запросы к фоновому воркеру (ответы приходят его сигналами)
    request_translate = pyqtSignal(int, str, str, str, str)
    request_ocr = pyqtSignal(int, int, int, int, int, str, int)
    request_lens = pyqtSignal(int, object, str, int, str, str, str)
    request_lens_tr = pyqtSignal(int, object, str, str, str)
    request_ocr_image = pyqtSignal(int, object, str, int)

    def __init__(self) -> None:
        super().__init__()
        self.cfg = config.load()
        theme.set_theme(self._resolve_flavor())
        if self.cfg.get("persist_cache", True):
            n = CACHE.load(CACHE_FILE)
            log.info("кэш переводов: %d записей с диска", n)
        self._quitting = False  # «Выход» из трея — закрыть по-настоящему
        self._hk_failed: list[str] = []  # сочетания, которые не удалось занять
        self._clip_before: str | None = None  # буфер до Ctrl+Alt+T — вернуть после
        # Раздельные счётчики: OCR-тик в режиме чтения не должен «устаревлять»
        # ещё не вернувшийся перевод (иначе результат отбрасывается как stale).
        self._tr_req = 0
        self._ocr_req = 0
        self._last_ocr_text = ""
        self._ocr_busy = False  # OCR-запрос ушёл в воркер и ещё не вернулся
        self._force_ocr = False  # следующий авто-тик распознаёт даже тот же кадр
        self._ocr_tr_req = -1  # id перевода, запущенного по кадру OCR
        self._overlay: ReadingOverlay | None = None
        self._subtitle: SubtitleOverlay | None = None
        self._hidden_for_reading = False  # окно спрятано ради субтитра
        self._ocr_auto = False  # последний OCR-запрос — авто-тик (а не ручной)
        self._pending_text = ""  # «допечатка»: текст изменился, ждём, пока устоится
        self._pending_since = 0.0
        self._lens: LensOverlay | None = None
        self._live: LiveSession | None = None
        self._live_hid_window = False  # окно спрятали ради живого перевода — вернуть
        self._live_paused_reading = False  # чтение выключили ради живого — вернуть
        self._live_threads: LiveWorkers | None = None  # потоки живого перевода
        self._lens_req = 0
        self._lens_data: list = []  # блоки последнего экранного перевода (для смены языка)
        self._icons: list[tuple] = []  # (кнопка, имя-иконки) для смены темы
        self._tray: QSystemTrayIcon | None = None
        self._loading = False

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("AutoTranslator")
        self.setWindowIcon(QIcon(asset("app.ico")))
        self.setAcceptDrops(True)  # картинку/текстовый файл можно бросить в окно
        self.resize(460, 600)
        self.setMinimumSize(400, 460)

        self._speaker = Speaker()
        self._hotkeys = HotkeyManager()

        self._build_ui()
        self._build_tray()
        self._build_shortcuts()
        self._start_worker()
        self._apply_config()
        self._wire_hotkeys()
        self._start_bridge()

    def _reg_tool(self, icon_name, tooltip, *, checkable=False, size=30) -> QToolButton:
        btn = tool_button(icon_name, tooltip, checkable=checkable, size=size)
        self._icons.append((btn, icon_name))
        return btn

    def _reg_text(self, icon_name, text, tooltip="") -> QPushButton:
        btn = text_button(icon_name, text, tooltip)
        self._icons.append((btn, icon_name))
        return btn

    def _tile(self, icon_name, text, tooltip="") -> QToolButton:
        """Кнопка панели «С экрана»: иконка над подписью, делит ширину поровну."""
        btn = Tile()
        btn.setObjectName("tile")
        btn.setText(text)
        btn.setIcon(make_icon(icon_name, theme.ICON))
        btn.setIconSize(QSize(18, 18))
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip(tooltip)
        self._icons.append((btn, icon_name))
        return btn

    def _hotkey_combo(self, action: str) -> str:
        custom = self.cfg.get("hotkeys") or {}
        return custom.get(action, HOTKEY_DEFAULTS.get(action, ""))

    def _hk_hint(self, action: str) -> str:
        if not self._hotkeys.available or not self.cfg.get("hotkeys_enabled", True):
            return ""
        combo = self._hotkey_combo(action)
        return f"\nГорячая клавиша: {_hk_label(combo)}" if combo else ""

    def _build_ui(self) -> None:
        # карточка с тенью — единственный видимый фон (окно прозрачное)
        self.card = QFrame()
        self.card.setObjectName("card")
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 170))
        self.card.setGraphicsEffect(shadow)

        self.title_bar = TitleBar(self)

        # ---- языки: главное, что меняют, — крупно и во всю ширину ----
        self.source_box = QComboBox()
        self.source_box.addItem("Определить язык", AUTO)
        for code in LANGUAGES:
            self.source_box.addItem(lang_name(code), code)
        self.source_box.setToolTip("С какого языка переводить")
        self.target_box = QComboBox()
        for code in LANGUAGES:
            self.target_box.addItem(lang_name(code), code)
        self.target_box.setToolTip("На какой язык переводить")
        for box in (self.source_box, self.target_box):
            box.setMaxVisibleItems(12)
            box.setCursor(Qt.CursorShape.PointingHandCursor)
        self.swap_btn = self._reg_tool("swap", "Поменять языки местами (Ctrl+D)")
        self.swap_btn.clicked.connect(self._swap_direction)

        lang_row = QHBoxLayout()
        lang_row.setSpacing(6)
        lang_row.addWidget(self.source_box, 1)
        lang_row.addWidget(self.swap_btn)
        lang_row.addWidget(self.target_box, 1)

        # ---- поле ввода: текст + подвал (язык, счётчик, вставить, очистить) ----
        self.input = QTextEdit()
        self.input.setPlaceholderText(
            "Введите или вставьте текст\n\n"
            "Сюда же можно бросить картинку или .txt —\n"
            "текст с неё распознается и переведётся."
        )
        self.input.setAcceptRichText(False)  # из браузера — чистый текст, без вёрстки
        self.input_field = Field(self.input)
        self.detected = QLabel("")
        self.detected.setObjectName("chip")
        self.detected.setVisible(False)
        self.counter = QLabel("")
        self.counter.setObjectName("muted")
        paste_btn = self._reg_tool(
            "paste", "Вставить из буфера и перевести (Ctrl+Shift+V)", size=28
        )
        paste_btn.clicked.connect(self.paste_and_translate)
        self.clear_btn = self._reg_tool("clear", "Очистить (Ctrl+L)", size=28)
        self.clear_btn.clicked.connect(self.clear_all)
        f = self.input_field.footer
        f.addWidget(self.detected)
        f.addStretch(1)
        f.addWidget(self.counter)
        f.addSpacing(4)
        f.addWidget(paste_btn)
        f.addWidget(self.clear_btn)

        # ---- поле перевода: текст + подвал (движок, чтение, озвучка, копия) ----
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setPlaceholderText(
            "Здесь появится перевод\n\n"
            "Ctrl+Enter — перевести сразу | F1 — все горячие клавиши"
        )
        self.output_field = Field(self.output, "fieldOut")
        # бросок файла в поле обрабатывает окно (OCR картинки / чтение файла),
        # иначе QTextEdit вставил бы просто путь file:///…
        for edit in (self.input, self.output):
            edit.setAcceptDrops(False)
        self.engine_chip = QToolButton()
        self.engine_chip.setObjectName("enginechip")
        self.engine_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.engine_chip.clicked.connect(self._toggle_engine)
        self.reading_chip = QLabel("● чтение")
        self.reading_chip.setObjectName("chipLive")
        self.reading_chip.setVisible(False)
        self.speak_btn = self._reg_tool("speak", "Озвучить (Ctrl+Shift+S)", size=28)
        self.speak_btn.clicked.connect(self.speak_result)
        self.speak_btn.setVisible(self._speaker.available)
        self.copy_btn = self._reg_tool(
            "copy", "Копировать перевод (Ctrl+Shift+C)", size=28
        )
        self.copy_btn.clicked.connect(self.copy_result)
        f = self.output_field.footer
        f.addWidget(self.engine_chip)
        f.addWidget(self.reading_chip)
        f.addStretch(1)
        f.addWidget(self.speak_btn)
        f.addWidget(self.copy_btn)
        # движок выбирается и здесь (кликом по значку), и в настройках
        self.engine_box = QComboBox()
        self.engine_box.addItem("Онлайн — Google", "online")
        self.engine_box.addItem("Офлайн — Argos", "offline")
        self.engine_box.setToolTip(
            "Онлайн — Google: лучшее качество, нужен интернет, текст уходит в Google\n"
            "Офлайн — Argos: локально и приватно"
        )
        self.engine_box.currentIndexChanged.connect(self._update_engine_dot)

        # ---- строка игры (мост): появляется, когда подключилась Ren'Py-игра ----
        self.game_chip = QLabel("")
        self.game_chip.setObjectName("chip")
        self.game_chip.setToolTip(
            "Игра с модулем перевода из RenpyTestMenu: реплики приходят\n"
            "прямо из движка, перевод показывается в самой игре."
        )
        self.game_fix_btn = self._reg_tool(
            "edit",
            "Исправить перевод этой реплики: правь текст выше и нажми ещё раз\n"
            "(или Ctrl+Enter) — правка уйдёт в словарь игры. Esc — отмена.",
            checkable=True,
            size=26,
        )
        self.game_fix_btn.toggled.connect(self._toggle_game_fix)
        self.game_fix_btn.setEnabled(False)
        self.game_gloss_btn = self._reg_tool(
            "book",
            "Глоссарий для игр: имена персонажей и свои термины\n"
            "переводятся всегда одинаково (Lucky не станет «Счастливчиком»).",
            size=26,
        )
        self.game_gloss_btn.clicked.connect(self.edit_glossary)
        self.game_tts_chk = QCheckBox("Озвучивать")
        self.game_tts_chk.setToolTip("Читать вслух перевод каждой реплики игры")
        self.game_tts_chk.setVisible(self._speaker.available)
        game_row = QHBoxLayout()
        game_row.setContentsMargins(8, 4, 6, 4)
        game_row.setSpacing(6)
        self.game_icon = self._reg_tool("game", "Мост для игр подключён", size=24)
        self.game_icon.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        game_row.addWidget(self.game_icon)
        game_row.addWidget(self.game_chip)
        game_row.addStretch(1)
        game_row.addWidget(self.game_tts_chk)
        game_row.addWidget(self.game_gloss_btn)
        game_row.addWidget(self.game_fix_btn)
        self.game_box = QFrame()
        self.game_box.setObjectName("panel")
        self.game_box.setLayout(game_row)
        self.game_box.setVisible(False)

        # ---- с экрана: одна компактная строка-панель, чтобы место осталось тексту ----
        self.lens_btn = self._tile(
            "screen",
            "Экран",
            "Перевести всё, что сейчас на экране, — перевод ляжет\n"
            "прямо поверх текста. Esc — закрыть, Пробел — оригинал."
            + self._hk_hint("lens"),
        )
        self.lens_btn.clicked.connect(lambda: self.open_lens())
        self.live_btn = self._tile(
            "live",
            "Живой",
            "Живой перевод экрана — для новелл и игр: перевод лежит прямо\n"
            "поверх текста и обновляется сам, когда вы листаете реплики\n"
            "(клик / Пробел / Enter). Клики проходят сквозь перевод в игру."
            + self._hk_hint("live"),
        )
        self.live_btn.setCheckable(True)
        self.live_btn.toggled.connect(self._toggle_live)
        self.lens_area_btn = self._tile(
            "crop",
            "Кусок",
            "Выделить мышкой кусок экрана — перевод появится прямо на нём."
            + self._hk_hint("lens_area"),
        )
        self.lens_area_btn.clicked.connect(self.pick_lens_area)
        self.region_btn = self._tile(
            "scan",
            "Область",
            "Выбрать область для режима «Чтение»: рамка остаётся на экране,\n"
            "а перевод обновляется сам (удобно для окна диалогов в новелле)."
            + self._hk_hint("region"),
        )
        self.region_btn.clicked.connect(self.pick_region)
        self.reading_btn = self._tile(
            "eye",
            "Чтение",
            "Держать рамку на области и переводить её вживую.\n"
            "Рамку можно двигать мышью и тянуть за углы, ПКМ по рамке — меню."
            + self._hk_hint("reading"),
        )
        self.reading_btn.setCheckable(True)
        self.reading_btn.toggled.connect(self._toggle_reading)
        self.recapture_btn = self._reg_tool(
            "refresh",
            "Перечитать область сейчас (нет области — выбрать)"
            + self._hk_hint("recapture"),
        )
        self.recapture_btn.setObjectName("tileSmall")
        self.recapture_btn.clicked.connect(self._recapture_clicked)
        screen_row = QHBoxLayout()
        screen_row.setContentsMargins(4, 4, 4, 4)
        screen_row.setSpacing(2)
        for btn in (
            self.lens_btn,
            self.live_btn,
            self.lens_area_btn,
            self.region_btn,
            self.reading_btn,
        ):
            screen_row.addWidget(btn, 1)
        screen_row.addWidget(self.recapture_btn)
        self.screen_bar = QFrame()
        self.screen_bar.setObjectName("toolbar")
        self.screen_bar.setLayout(screen_row)

        settings_page = self._build_settings_page()

        # ---- низ: статус + ручка размера ----
        self.status = QLabel("Готово")
        self.status.setObjectName("status")
        self.status.setProperty("state", "idle")
        self.settings_btn = self.title_bar.settings_btn
        self.settings_btn.toggled.connect(self._toggle_settings)
        grip = QSizeGrip(self.card)
        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(14, 2, 2, 4)
        bottom_row.addWidget(self.status)
        bottom_row.addStretch(1)
        bottom_row.addWidget(
            grip, 0, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight
        )

        main_page = QWidget()
        body = QVBoxLayout(main_page)
        body.setContentsMargins(14, 10, 14, 6)
        body.setSpacing(8)
        body.addLayout(lang_row)
        body.addWidget(self.input_field, 1)
        body.addWidget(self.output_field, 1)
        body.addWidget(self.game_box)
        body.addWidget(self.screen_bar)

        self.pages = QStackedWidget()
        self.pages.addWidget(main_page)
        self.pages.addWidget(settings_page)

        card_lay = QVBoxLayout(self.card)
        card_lay.setContentsMargins(0, 0, 0, 0)
        card_lay.setSpacing(0)
        card_lay.addWidget(self.title_bar)
        # полоса «идёт работа» прямо под шапкой: видно, даже не глядя на статус
        self.busy_line = BusyLine()
        card_lay.addWidget(self.busy_line)
        card_lay.addWidget(self.pages, 1)
        card_lay.addLayout(bottom_row)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 18)  # место под тень
        outer.addWidget(self.card)

        self.setStyleSheet(build_qss(PALETTE, self.cfg.get("font_scale", 100)))

        self._build_timers()

    def _build_timers(self) -> None:
        """Таймеры окна: дебаунс перевода, авто-OCR, статус, тема, автосохранение."""
        # живой перевод с дебаунсом
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(450)
        self._debounce.timeout.connect(self.translate_now)
        self.input.textChanged.connect(self._debounce.start)
        self.input.textChanged.connect(self._update_meta)
        self.engine_box.currentIndexChanged.connect(self.translate_now)
        self.source_box.currentIndexChanged.connect(self.translate_now)
        self.source_box.currentIndexChanged.connect(self._update_meta)
        self.target_box.currentIndexChanged.connect(self.translate_now)
        for box in (self.engine_box, self.source_box, self.target_box):
            box.currentIndexChanged.connect(self._sync_bridge)
            box.currentIndexChanged.connect(self._refresh_live)

        # «перечитать по клику»: игрок перелистнул реплику — снимаем кадр сразу,
        # не дожидаясь тика таймера (общий опрос кнопок, без хуков)
        advance.watcher().advanced.connect(self._on_advance)

        # таймер авто-OCR
        self._auto_timer = QTimer(self)
        self._auto_timer.timeout.connect(lambda: self.capture_region(auto=True))

        # дебаунс перечитывания после перетаскивания/ресайза рамки
        self._region_timer = QTimer(self)
        self._region_timer.setSingleShot(True)
        self._region_timer.setInterval(300)
        self._region_timer.timeout.connect(self.capture_region)

        # анимация «работает…» в статус-плашке (бегущие точки)
        self._status_kind = "idle"
        self._status_base = "Готово"
        self._status_dots = 0
        self._status_anim = QTimer(self)
        self._status_anim.setInterval(350)
        self._status_anim.timeout.connect(self._tick_status_dots)
        self._status_revert = QTimer(self)  # авто-возврат после transient-успеха
        self._status_revert.setSingleShot(True)
        self._status_revert.timeout.connect(lambda: self._set_status("Готово", "idle"))
        # авто-слежение за системной темой (для режима «как в системе»): Qt 6.5+
        # сам сообщает о смене темы Windows; опрос реестра — только запасной путь
        self._sys_theme_timer = QTimer(self)
        self._sys_theme_timer.setInterval(4000)
        self._sys_theme_timer.timeout.connect(self._check_system_theme)
        hints = QGuiApplication.styleHints()
        if hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(lambda _s: self._check_system_theme())
        else:
            self._sys_theme_timer.start()
        # автосохранение: настройки/история не теряются при сбое или выключении ПК
        self._saved_snapshot = ""
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(AUTOSAVE_MS)
        self._autosave_timer.timeout.connect(self._autosave)
        self._autosave_timer.start()
        self._update_engine_dot()

    def bring_to_front(self) -> None:
        """Повторный запуск программы — показать уже открытое окно."""
        if self._hidden_for_reading:
            self._set_display_mode("window")
        self.appear()

    def appear(self) -> None:
        """Показать окно: из трея оно проявляется и мягко съезжает на место."""
        hidden = not self.isVisible() or self.isMinimized()
        self.showNormal()
        self.raise_()
        self.activateWindow()
        if hidden:
            anim.window_drop_in(self, self.opacity.value() / 100.0, dy=10)

    def disappear(self) -> None:
        """Спрятать окно в трей с коротким угасанием."""
        if not self.isVisible():
            return
        target = self.opacity.value() / 100.0

        def done() -> None:
            self.hide()
            self.setWindowOpacity(target)  # следующий показ — с прежней прозрачностью

        anim.window_opacity(self, self.windowOpacity(), 0.0, 140, done)

    def quit_app(self) -> None:
        self._quitting = True
        self.close()

    def _build_shortcuts(self) -> None:
        def sc(seq, slot):
            s = QShortcut(QKeySequence(seq), self)
            s.activated.connect(slot)

        sc("Ctrl+Return", self.translate_now)
        sc("Ctrl+Enter", self.translate_now)
        sc("Ctrl+Shift+C", self.copy_result)
        sc("Ctrl+Shift+V", self.paste_and_translate)
        sc("Ctrl+L", self.clear_all)
        sc("Ctrl+D", self._swap_direction)
        sc("Ctrl+H", self.show_full_history)
        sc("Ctrl+Shift+S", self.speak_result)
        sc("F1", self.show_help)
        sc("Ctrl+,", self.settings_btn.toggle)
        sc("Ctrl+=", lambda: self._bump_font(+10))
        sc("Ctrl++", lambda: self._bump_font(+10))
        sc("Ctrl+-", lambda: self._bump_font(-10))
        sc("Esc", self._on_escape)

    def _on_escape(self) -> None:
        if self.game_fix_btn.isChecked():
            self.game_fix_btn.setChecked(False)  # Esc в правке — просто выйти из неё
            return
        if self.settings_btn.isChecked():
            self.settings_btn.setChecked(False)  # Esc в настройках — назад к переводу
            return
        self.hide_to_tray()

    def _show_window(self) -> None:
        if self._hidden_for_reading:
            return  # в режиме субтитра перевод покажет плашка
        self.appear()

    def _start_worker(self) -> None:
        self._thread = QThread(self)
        self._worker = Worker()
        self._worker.moveToThread(self._thread)
        self.request_translate.connect(self._worker.do_translate)
        self.request_ocr.connect(self._worker.do_ocr)
        self.request_lens.connect(self._worker.do_lens)
        self.request_lens_tr.connect(self._worker.do_lens_translate)
        self._worker.lens_partial.connect(self._on_lens_partial)
        self.request_ocr_image.connect(self._worker.do_ocr_image)
        self._worker.offline_fallback = bool(self.cfg.get("offline_fallback", True))
        self._worker.lens_blocks.connect(self._on_lens_blocks)
        self._worker.lens_translated.connect(self._on_lens_translated)
        self._worker.lens_failed.connect(self._on_lens_failed)
        self._worker.translated.connect(self._on_translated)
        self._worker.ocr_done.connect(self._on_ocr)
        self._worker.ocr_same.connect(self._on_ocr_same)
        self._worker.failed.connect(self._on_failed)
        self._thread.start()

    def _apply_config(self) -> None:
        c = self.cfg
        self.engine_box.setCurrentIndex(max(0, self.engine_box.findData(c["engine"])))
        self.source_box.setCurrentIndex(max(0, self.source_box.findData(c["source"])))
        self.target_box.setCurrentIndex(max(0, self.target_box.findData(c["target"])))
        self.title_bar.pin_btn.setChecked(c["always_on_top"])
        self.set_always_on_top(c["always_on_top"])
        self.opacity.setValue(int(c["opacity"]))
        self.autocopy_chk.setChecked(bool(c.get("autocopy", False)))
        mode = c.get("subtitle_mode", "below") if c.get("subtitle", True) else "window"
        self.display_box.blockSignals(True)
        self.display_box.setCurrentIndex(max(0, self.display_box.findData(mode)))
        self.display_box.blockSignals(False)
        self.auto_chk.setChecked(bool(c.get("auto_refresh", True)))
        self.enhance_chk.setChecked(bool(c.get("ocr_enhance", True)))
        self.merge_chk.setChecked(bool(c.get("ocr_merge_lines", True)))
        self.interval_spin.setValue(int(c.get("auto_interval_ms", 1200)))
        self.game_tts_chk.setChecked(bool(c.get("game_tts", False)))
        self.font_label.setText(f"{int(c.get('font_scale', 100))}%")
        self.fallback_chk.setChecked(bool(c.get("offline_fallback", True)))
        self.close_tray_chk.setChecked(bool(c.get("close_to_tray", False)))
        self.tts_chk.setChecked(bool(c.get("tts_enabled", False)))
        self.click_chk.setChecked(bool(c.get("capture_on_click", True)))
        self.live_target_box.setCurrentIndex(
            max(0, self.live_target_box.findData(c.get("live_target", "window")))
        )
        self.interval_spin.setEnabled(self.auto_chk.isChecked())
        self._sync_theme_controls()
        self._auto_timer.setInterval(int(c["auto_interval_ms"]))
        if c.get("geometry"):
            self.restoreGeometry(QByteArray.fromBase64(c["geometry"].encode()))
            self._clamp_to_screen()
        self._update_meta()
        # область могла остаться на отключённом мониторе — такую забываем
        if c.get("region") and not self._region_on_screen(c["region"]):
            c["region"] = None
        # восстановить режим чтения, если он был включён и область сохранена
        if c.get("reading") and c.get("region"):
            self.reading_btn.setChecked(True)  # -> _toggle_reading -> _start_reading

    @staticmethod
    def _region_on_screen(region) -> bool:
        try:
            left, top, width, height = (int(v) for v in region)
        except (TypeError, ValueError):
            return False
        if width < 5 or height < 5:
            return False
        center = QPoint(left + width // 2, top + height // 2)
        return QGuiApplication.screenAt(center) is not None

    def _clamp_to_screen(self) -> None:
        """Не даём окну восстановиться за пределами видимых экранов."""
        frame = self.frameGeometry()
        screen = (
            QGuiApplication.screenAt(frame.center()) or QGuiApplication.primaryScreen()
        )
        area = screen.availableGeometry()
        g = self.geometry()
        w = min(g.width(), area.width())
        h = min(g.height(), area.height())
        x = max(area.left(), min(g.left(), area.right() - w))
        y = max(area.top(), min(g.top(), area.bottom() - h))
        self.setGeometry(x, y, w, h)

    def _save_config(self) -> None:
        self.cfg.update(
            {
                "engine": self.engine_box.currentData(),
                "source": self.source_box.currentData(),
                "target": self.target_box.currentData(),
                "always_on_top": self.title_bar.pin_btn.isChecked(),
                "opacity": self.opacity.value(),
                "autocopy": self.autocopy_chk.isChecked(),
                "subtitle": self._display_mode() != "window",
                "subtitle_mode": (
                    self._display_mode()
                    if self._display_mode() != "window"
                    else self.cfg.get("subtitle_mode", "below")
                ),
                "ocr_enhance": self.enhance_chk.isChecked(),
                "ocr_merge_lines": self.merge_chk.isChecked(),
                "reading": self.reading_btn.isChecked() or self._live_paused_reading,
                "auto_refresh": self.auto_chk.isChecked(),
                "auto_interval_ms": self.interval_spin.value(),
                "game_tts": self.game_tts_chk.isChecked(),
                "region": self.cfg.get("region"),
                "geometry": bytes(self.saveGeometry().toBase64()).decode(),  # type: ignore[arg-type]
            }
        )
        snapshot = config.dumps(self.cfg)
        if snapshot == self._saved_snapshot:
            return  # ничего не изменилось — не трогаем диск
        try:
            config.save(self.cfg)
            self._saved_snapshot = snapshot
        except OSError as e:
            self._set_status("Не удалось сохранить настройки", "error")
            self.status.setToolTip(str(e))

    def _autosave(self) -> None:
        self._save_config()
        if self.cfg.get("persist_cache", True):
            CACHE.save(CACHE_FILE)  # пишет, только если кэш пополнился

    def showEvent(self, event) -> None:  # noqa: N802
        # своё окно не должно попадать в скриншоты для OCR (и в экранный перевод)
        exclude_from_capture(self)
        super().showEvent(event)

    def set_always_on_top(self, on: bool) -> None:
        flags = self.windowFlags()
        if on:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowType.WindowStaysOnTopHint
        visible = self.isVisible()
        self.setWindowFlags(flags)
        # смена флагов прячет окно — вернуть, но не показывать спрятанное
        # (при запуске в режиме субтитра окно не должно мелькать)
        if visible:
            self.show()

    def _animate_swap(self) -> None:
        """Плавно проворачивает иконку ⇄ на 180° — приятный микро-отклик."""
        anim = QVariantAnimation(self)
        anim.setDuration(280)
        anim.setStartValue(0.0)
        anim.setEndValue(180.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.valueChanged.connect(
            lambda v: self.swap_btn.setIcon(make_icon("swap", theme.ICON, angle=v))
        )
        anim.finished.connect(
            lambda: self.swap_btn.setIcon(make_icon("swap", theme.ICON))
        )
        anim.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)
        self._swap_anim = anim  # держим ссылку, чтобы не собрал GC

    def _swap_direction(self) -> None:
        self._animate_swap()
        src = self.source_box.currentData()
        tgt = self.target_box.currentData()
        if src == AUTO:
            # для «Авто» источник определяем по введённому тексту (иначе — цель как есть)
            text = self.input.toPlainText().strip()
            src = detect_lang(text) if text else tgt
        self.source_box.setCurrentIndex(max(0, self.source_box.findData(tgt)))
        self.target_box.setCurrentIndex(max(0, self.target_box.findData(src)))
        # меняем и сам текст местами, чтобы перевод пошёл «в обратную»
        in_text, out_text = self.input.toPlainText(), self.output.toPlainText()
        if out_text.strip():
            self.input.setPlainText(out_text)
            self.output.setPlainText(in_text)

    def _update_meta(self) -> None:
        text = self.input.toPlainText()
        words = len(text.split())
        self.counter.setText(
            f"{len(text)} симв.{self._reading_time(words)}" if text else ""
        )
        self.counter.setToolTip(f"Слов: {words}, символов: {len(text)}")
        self.clear_btn.setVisible(bool(text))
        source = self.source_box.currentData()
        if source == AUTO and text.strip():
            self.detected.setText(lang_name(detect_lang(text)))
            self.detected.setToolTip("Язык определён автоматически")
            self.detected.setVisible(True)
        else:
            self.detected.setVisible(False)

    @staticmethod
    def _reading_time(words: int) -> str:
        """Оценка времени чтения (~200 слов/мин). Пусто для коротких фрагментов."""
        if words < 10:
            return ""
        secs = round(words / 200 * 60)
        if secs < 60:
            return f" | ~{secs} c"
        return f" | ~{secs // 60} мин {secs % 60:02d} c"

    def _set_status(
        self, text: str, kind: str = "idle", *, transient: bool = False
    ) -> None:
        """Показывает статус с семантическим цветом (idle/working/success/error/offline).

        kind="working" включает бегущие точки; transient=True вернёт «Готово» через ~1.6 c.
        """
        self.busy_line.set_busy(kind == "working")
        if kind != self._status_kind or text != self._status_base:
            anim.fade_in(self.status, 220, 0.25)  # новый статус проявляется
        self._status_base = text
        self._status_kind = kind
        self._status_dots = 0
        self.status.setText(text)
        if kind not in ("error", "offline"):
            self.status.setToolTip("")
        if self.status.property("state") != kind:
            self.status.setProperty("state", kind)
            self._repolish(self.status)
        if kind == "working":
            self._status_anim.start()
        else:
            self._status_anim.stop()
        self._status_revert.stop()
        if transient:
            self._status_revert.start(1600)

    def _tick_status_dots(self) -> None:
        self._status_dots = (self._status_dots + 1) % 4
        self.status.setText(self._status_base + "." * self._status_dots)

    def _update_engine_dot(self) -> None:
        online = self.engine_box.currentData() == "online"
        chip = self.engine_chip
        chip.setText("●  Google" if online else "●  Офлайн")
        chip.setProperty("mode", "online" if online else "offline")
        about = (
            "Онлайн-перевод Google: лучшее качество, текст уходит в Google."
            if online
            else "Офлайн-перевод Argos: локально и приватно, без интернета."
        )
        chip.setToolTip(about + "\nКлик — переключить движок")
        self._repolish(chip)

    def _toggle_engine(self) -> None:
        self.engine_box.setCurrentIndex(1 - self.engine_box.currentIndex())
        online = self.engine_box.currentData() == "online"
        self._set_status(
            "Движок: Google (онлайн)" if online else "Движок: Argos (офлайн)",
            "success",
            transient=True,
        )

    def _toggle_settings(self, on: bool) -> None:
        """Настройки — отдельная страница в той же карточке: размер окна не меняется.

        Страница въезжает с той стороны, куда «идём»: настройки — справа, назад —
        слева.
        """
        index = 1 if on else 0
        if self.pages.currentIndex() != index:
            self.pages.setCurrentIndex(index)
            anim.slide_in(self.pages.currentWidget(), 36 if on else -36)
        self.settings_btn.setIcon(
            make_icon("gear", PALETTE["accent"] if on else theme.ICON)
        )
        if not on:
            self.input.setFocus()

    @staticmethod
    def _repolish(widget) -> None:
        """Переприменяет QSS к виджету после смены динамического свойства."""
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def translate_now(self) -> None:
        if self.game_fix_btn.isChecked():
            self._finish_game_fix()  # Ctrl+Enter во время правки — отправить правку
            return
        self._game_src = ""  # свой текст — это уже не реплика игры
        self.game_fix_btn.setEnabled(False)
        text = self.input.toPlainText().strip()
        if not text:
            self.output.clear()
            return
        source = self.source_box.currentData()
        target = self.target_box.currentData()
        if source != AUTO and source == target:
            self.output.setPlainText(text)  # тот же язык — переводить нечего
            return
        self._tr_req += 1
        self._loading = True
        self._set_status("Перевод", "working")
        self.request_translate.emit(
            self._tr_req, text, self.engine_box.currentData(), source, target
        )

    @pyqtSlot(int, str, str, str)
    def _on_translated(self, req_id, result, detected="", via="") -> None:
        if req_id != self._tr_req:
            return  # устаревший ответ
        self._loading = False
        self.output.setPlainText(result)
        anim.fade_in(self.output, 260, 0.2)
        if detected and self.source_box.currentData() == AUTO:
            # язык, определённый переводчиком, точнее нашей эвристики по словам
            self.detected.setText(lang_name(detected))
            self.detected.setToolTip("Язык определил переводчик")
            self.detected.setVisible(True)
        if via == "offline":
            self._set_status("Онлайн недоступен — переведено офлайн", "offline")
            self.status.setToolTip(
                "Онлайн-перевод недоступен, сработала подстраховка Argos.\n"
                "Выключить: меню значка в трее."
            )
            self._status_revert.start(4000)
        else:
            self._set_status("Готово", "success", transient=True)
        if self._subtitle is not None and self._subtitle.isVisible():
            self._subtitle.set_busy(False)
            self._subtitle.set_text(result)
        if result:
            # кадры режима чтения в историю не пишем — иначе она забьётся за минуту
            if not (req_id == self._ocr_tr_req and self.reading_btn.isChecked()):
                self._push_history(self.input.toPlainText().strip(), result)
            if self.autocopy_chk.isChecked():
                QApplication.clipboard().setText(result)
            if self.cfg.get("tts_enabled") and self._speaker.available:
                self.speak_result()

    @pyqtSlot(int, str, str)
    def _on_failed(self, req_id, kind, error) -> None:
        # ошибка давно устаревшего запроса не должна перебивать свежий статус
        if req_id != (self._tr_req if kind == "translate" else self._ocr_req):
            return
        self._loading = False
        if kind == "ocr":
            self._ocr_busy = False
        else:
            # кадр не переведён — на следующем тике распознать и перевести заново
            self._last_ocr_text = ""
            self._force_ocr = True
        if self._subtitle is not None:
            self._subtitle.set_busy(False)
        low = error.lower()
        offline = any(
            w in low
            for w in (
                "network",
                "connection",
                "internet",
                "resolve",
                "timed out",
                "getaddrinfo",
                "сет",
                "интернет",
            )
        )
        self._set_status(
            "Нет сети" if offline else "Ошибка: " + error,
            "offline" if offline else "error",
        )
        self.status.setToolTip(error)

    def copy_result(self) -> None:
        text = self.output.toPlainText()
        if not text.strip():
            self._set_status("Нечего копировать", "idle")
            return
        QApplication.clipboard().setText(text)
        self._set_status("Скопировано", "success", transient=True)
        # галочка на кнопке — видно, что сработало, даже не глядя на статус
        self.copy_btn.setIcon(make_icon("check", PALETTE["success"]))
        QTimer.singleShot(
            1200, lambda: self.copy_btn.setIcon(make_icon("copy", theme.ICON))
        )

    def clear_all(self) -> None:
        self.input.clear()
        self.output.clear()
        self.input.setFocus()
        self._set_status("Очищено", "idle")

    def speak_result(self) -> None:
        if not self._speaker.available:
            return
        text = self.output.toPlainText().strip()
        if text:
            self._speaker.speak(text, tts_prefix(self.target_box.currentData()))

    def background_threads(self) -> list:
        """Фоновые потоки окна: воркер и, если включали, потоки живого перевода."""
        threads = [self._thread]
        if self._live_threads is not None:
            threads += self._live_threads.threads
        return threads

    def closeEvent(self, event) -> None:  # noqa: N802
        if (
            self.cfg.get("close_to_tray")
            and self._tray is not None
            and not self._quitting
        ):
            event.ignore()  # крестик — в трей; выйти: меню трея → «Выход»
            self.hide()
            self._tray.showMessage(
                "AutoTranslator",
                "Работаю в трее. Выход — правый клик по значку.",
                QSystemTrayIcon.MessageIcon.Information,
                2500,
            )
            return
        self._save_config()
        if self.cfg.get("persist_cache", True):
            CACHE.save(CACHE_FILE)
        self._hotkeys.unregister()
        if self._bridge is not None:
            self._bridge.stop()
        self._speaker.shutdown()
        if self._tray is not None:
            self._tray.hide()
        if self._overlay is not None:
            self._overlay.close()
        if self._subtitle is not None:
            self._subtitle.close()
        self._close_lens()
        if self._live is not None:
            self._live_hid_window = self._live_paused_reading = False
            self._live.stop()
        self._auto_timer.stop()
        # все потоки просим закончить сразу, ждём — общим сроком, а не каждый по очереди
        threads = self.background_threads()
        for th in threads:
            th.quit()
        deadline = time.monotonic() + SHUTDOWN_WAIT_S
        for th in threads:
            th.wait(max(0, int((deadline - time.monotonic()) * 1000)))
        clear_cache()
        super().closeEvent(event)
        # окно типа Tool не считается «последним окном» для Qt — без явного quit
        # процесс остался бы жить невидимым (с рамкой/хуками клавиш)
        QApplication.quit()
