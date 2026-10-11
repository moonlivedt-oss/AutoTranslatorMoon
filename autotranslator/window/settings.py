"""Страница настроек: построение и обработчики переключателей.

Часть главного окна: методы примешиваются к MainWindow (см. window/main.py)."""

from __future__ import annotations

import logging

from PyQt6.QtCore import (
    Qt,
)
from PyQt6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..ui import theme
from ..ui.widgets import Switch, section_label
from ..win import autostart
from .reading import DISPLAY_MODES

log = logging.getLogger(__name__)


class SettingsMixin:
    def _build_settings_page(self) -> QWidget:
        """Настройки — отдельная страница в той же карточке, по группам."""
        self.autocopy_chk = Switch("Сразу копировать перевод")
        self.autocopy_chk.setToolTip("Каждый перевод сразу кладётся в буфер обмена")
        self.fallback_chk = Switch("Нет сети — переводить офлайн")
        self.fallback_chk.setToolTip(
            "Нет интернета или Google ограничил запросы — перевод сделает Argos,\n"
            "если модель для этой пары языков уже скачана."
        )
        self.fallback_chk.toggled.connect(self._set_offline_fallback)
        self.tts_chk = Switch("Озвучивать каждый перевод")
        self.tts_chk.setToolTip("Читать вслух каждый новый перевод")
        self.tts_chk.toggled.connect(lambda on: self.cfg.__setitem__("tts_enabled", on))
        self.tts_chk.setVisible(self._speaker.available)

        self.display_box = QComboBox()
        for mode, label in DISPLAY_MODES:
            self.display_box.addItem(label.replace("Перевод: ", ""), mode)
        self.display_box.setToolTip(
            "Где показывать перевод в режиме «Чтение»:\n"
            "в окне — в этом окне;\n"
            "под областью — окно прячется, перевод висит плашкой у рамки;\n"
            "поверх текста — перевод закрывает оригинал (клики проходят в игру)."
        )
        self.display_box.currentIndexChanged.connect(self._on_display_mode)
        self.auto_chk = Switch("Авто-обновление")
        self.auto_chk.setToolTip(
            "Перечитывать область в режиме чтения самой.\n"
            "Для новелл — включить: новая реплика переведётся без нажатий.\n"
            "Текст, который «печатается» по буквам, переводится, когда допечатан."
        )
        self.auto_chk.toggled.connect(self._toggle_auto_refresh)
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(300, 5000)
        self.interval_spin.setSingleStep(100)
        self.interval_spin.setSuffix(" мс")
        self.interval_spin.setToolTip("Как часто перечитывать область")
        self.interval_spin.valueChanged.connect(self._on_interval_changed)
        self.auto_chk.toggled.connect(self.interval_spin.setEnabled)
        self.click_chk = Switch("Перечитывать по клику в игре")
        self.click_chk.setToolTip(
            "ЛКМ / Пробел / Enter в игре — сразу перечитать область,\n"
            "не дожидаясь следующего тика авто-обновления."
        )
        self.click_chk.toggled.connect(self._set_capture_on_click)
        self.live_target_box = QComboBox()
        self.live_target_box.addItem("Окно игры", "window")
        self.live_target_box.addItem("Весь экран под курсором", "screen")
        self.live_target_box.setToolTip(
            "Окно игры — переводится только оно: быстрее, а перевод следует за\n"
            "окном и прячется, когда игра свёрнута или закрыта другим окном.\n"
            "Весь экран — как раньше: монитор, на котором курсор."
        )
        self.live_target_box.currentIndexChanged.connect(
            lambda _i: self.cfg.__setitem__(
                "live_target", self.live_target_box.currentData()
            )
        )
        self.enhance_chk = Switch("Улучшать картинку для OCR")
        self.enhance_chk.setToolTip(
            "Увеличивать и нормализовать картинку перед распознаванием —\n"
            "мелкий текст узнаётся точнее."
        )
        self.merge_chk = Switch("Склеивать строки в абзацы")
        self.merge_chk.setToolTip(
            "Склеивать строки OCR в абзацы по вёрстке —\n"
            "переводчик получает целые предложения, а не обрывки строк."
        )
        self.merge_chk.toggled.connect(self._on_merge_toggled)

        # тема: сегменты «Тёмная / Светлая / Как в Windows» + флейвор тёмной
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        mode_row = QHBoxLayout()
        mode_row.setSpacing(0)
        for i, (mode, title) in enumerate(
            (("dark", "Тёмная"), ("light", "Светлая"), ("system", "Системная"))
        ):
            seg = QPushButton(title)
            seg.setObjectName("seg")
            seg.setProperty("pos", ("first", "mid", "last")[i])
            seg.setCheckable(True)
            seg.setCursor(Qt.CursorShape.PointingHandCursor)
            seg.setProperty("mode", mode)
            self.mode_group.addButton(seg)
            mode_row.addWidget(seg, 1)
        self.mode_group.buttonClicked.connect(
            lambda b: self._pick_mode(b.property("mode"))
        )
        mode_wrap = QWidget()
        mode_wrap.setLayout(mode_row)
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_wrap.setToolTip("«Системная» — следовать за темой Windows")
        self.flavor_box = QComboBox()
        for fl in theme.DARK_FLAVORS:
            self.flavor_box.addItem(theme.FLAVOR_NAMES[fl], fl)
        self.flavor_box.setToolTip("Оттенок тёмной темы (Catppuccin)")
        self.flavor_box.activated.connect(
            lambda i: self._pick_flavor(self.flavor_box.itemData(i))
        )
        font_minus = QToolButton()
        font_minus.setText("A−")
        font_minus.setObjectName("stepper")
        font_minus.setFixedSize(34, 30)
        font_minus.setCursor(Qt.CursorShape.PointingHandCursor)
        font_minus.setToolTip("Мельче шрифт (Ctrl+−)")
        font_minus.clicked.connect(lambda: self._bump_font(-10))
        self.font_label = QLabel("100%")
        self.font_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.font_label.setMinimumWidth(48)
        font_plus = QToolButton()
        font_plus.setText("A+")
        font_plus.setObjectName("stepper")
        font_plus.setFixedSize(34, 30)
        font_plus.setCursor(Qt.CursorShape.PointingHandCursor)
        font_plus.setToolTip("Крупнее шрифт (Ctrl++)")
        font_plus.clicked.connect(lambda: self._bump_font(+10))
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(35, 100)
        self.opacity.setToolTip("Непрозрачность окна")
        self.opacity_label = QLabel("100%")
        self.opacity_label.setObjectName("muted")
        self.opacity_label.setMinimumWidth(36)
        self.opacity_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        def on_opacity(v: int) -> None:
            self.setWindowOpacity(v / 100.0)
            self.opacity_label.setText(f"{v}%")

        self.opacity.valueChanged.connect(on_opacity)

        self.hotkeys_chk = Switch("Глобальные горячие клавиши")
        self.hotkeys_chk.setToolTip(
            "Сочетания Ctrl+Alt+… работают поверх любых окон.\n"
            "Снять галочку — пауза (например, если мешают в игре)."
        )
        self.hotkeys_chk.setEnabled(self._hotkeys.available)
        self.hotkeys_chk.toggled.connect(self._set_hotkeys_enabled)
        self.close_tray_chk = Switch("Крестик сворачивает в трей")
        self.close_tray_chk.toggled.connect(
            lambda on: self.cfg.__setitem__("close_to_tray", on)
        )
        self.autostart_chk = Switch("Запускать вместе с Windows")
        self.autostart_chk.setVisible(autostart.supported())
        self.autostart_chk.setChecked(autostart.is_enabled())
        self.autostart_chk.toggled.connect(self._set_autostart)
        help_btn = self._reg_text("help", "Справка")
        help_btn.setToolTip("Справка: все сочетания клавиш, папка настроек (F1)")
        help_btn.clicked.connect(self.show_help)
        hist_btn = self._reg_text("history", "Вся история")
        hist_btn.setToolTip("Поиск, избранное, экспорт (Ctrl+H)")
        hist_btn.clicked.connect(self.show_full_history)

        def row(text: str, widget) -> QHBoxLayout:
            """Подпись слева, небольшой контрол справа."""
            lay = QHBoxLayout()
            lay.setSpacing(10)
            lay.addWidget(QLabel(text))
            lay.addStretch(1)
            if isinstance(widget, QHBoxLayout):
                lay.addLayout(widget)
            else:
                lay.addWidget(widget)
            return lay

        def stacked(text: str, widget) -> QVBoxLayout:
            """Подпись сверху, список во всю ширину — длинные пункты не обрезаются."""
            lay = QVBoxLayout()
            lay.setSpacing(5)
            lbl = QLabel(text)
            lbl.setObjectName("muted")
            lay.addWidget(lbl)
            lay.addWidget(widget)
            return lay

        def group(title: str, *items) -> list:
            box = QFrame()
            box.setObjectName("panel")
            lay = QVBoxLayout(box)
            lay.setContentsMargins(12, 10, 12, 12)
            lay.setSpacing(9)
            for it in items:
                if isinstance(it, (QHBoxLayout, QVBoxLayout)):
                    lay.addLayout(it)
                else:
                    lay.addWidget(it)
            return [section_label(title), box]

        font_row = QHBoxLayout()
        font_row.setSpacing(4)
        font_row.addWidget(font_minus)
        font_row.addWidget(self.font_label)
        font_row.addWidget(font_plus)
        opacity_row = QHBoxLayout()
        opacity_row.setSpacing(8)
        opacity_row.addWidget(self.opacity, 1)
        opacity_row.addWidget(self.opacity_label)
        opacity_wrap = QHBoxLayout()
        opacity_wrap.setSpacing(10)
        opacity_wrap.addWidget(QLabel("Непрозрачность"))
        opacity_wrap.addLayout(opacity_row, 1)
        auto_row = QHBoxLayout()
        auto_row.setSpacing(10)
        auto_row.addWidget(self.auto_chk)
        auto_row.addStretch(1)
        auto_row.addWidget(self.interval_spin)
        links_row = QHBoxLayout()
        links_row.setSpacing(6)
        links_row.addWidget(help_btn, 1)
        links_row.addWidget(hist_btn, 1)

        sections = [
            *group(
                "Перевод",
                stacked("Движок", self.engine_box),
                self.autocopy_chk,
                self.fallback_chk,
                self.tts_chk,
            ),
            *group(
                "Чтение с экрана",
                stacked("Где показывать перевод при чтении", self.display_box),
                auto_row,
                self.click_chk,
                stacked("Живой перевод — что переводить", self.live_target_box),
                self.enhance_chk,
                self.merge_chk,
            ),
            *group(
                "Внешний вид",
                stacked("Тема", mode_wrap),
                stacked("Оттенок тёмной темы", self.flavor_box),
                row("Размер текста", font_row),
                opacity_wrap,
            ),
            *group(
                "Программа",
                self.hotkeys_chk,
                self.close_tray_chk,
                self.autostart_chk,
                links_row,
            ),
        ]
        settings_body = QWidget()
        settings_body.setObjectName("settingsBody")
        sb = QVBoxLayout(settings_body)
        sb.setContentsMargins(14, 4, 10, 14)
        sb.setSpacing(6)
        for w in sections:
            if w.objectName() == "section":
                sb.addSpacing(6)
            sb.addWidget(w)
        sb.addStretch(1)
        # списки не должны распирать страницу по самому длинному пункту
        for box in (
            self.engine_box,
            self.display_box,
            self.flavor_box,
            self.live_target_box,
        ):
            box.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
            )
            box.setMinimumContentsLength(8)
        scroll = QScrollArea()
        scroll.setObjectName("settingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(settings_body)

        back_btn = self._reg_tool("back", "Назад к переводу (Esc)", size=30)
        back_btn.clicked.connect(lambda: self.settings_btn.setChecked(False))
        page_title = QLabel("Настройки")
        page_title.setObjectName("pagetitle")
        head = QHBoxLayout()
        head.setContentsMargins(10, 8, 14, 2)
        head.setSpacing(6)
        head.addWidget(back_btn)
        head.addWidget(page_title)
        head.addStretch(1)
        ver = QLabel(f"v{__version__}")
        ver.setObjectName("muted")
        head.addWidget(ver)

        settings_page = QWidget()
        sp = QVBoxLayout(settings_page)
        sp.setContentsMargins(0, 0, 0, 0)
        sp.setSpacing(0)
        sp.addLayout(head)
        sp.addWidget(scroll, 1)
        return settings_page

    def open_settings(self) -> None:
        self._show_window()
        self.settings_btn.setChecked(True)

    def _set_offline_fallback(self, on: bool) -> None:
        self.cfg["offline_fallback"] = on
        self._worker.offline_fallback = on
        if self._live_threads is not None:
            self._live_threads.set_offline_fallback(on)

    def _set_autostart(self, on: bool) -> None:
        try:
            autostart.set_enabled(on)
        except OSError as e:
            log.warning("автозапуск не изменён: %s", e)
            self._set_status("Не удалось изменить автозапуск", "error")
            self.status.setToolTip(str(e))
            return
        self._set_status(
            "Автозапуск " + ("включён" if on else "выключен"), "success", transient=True
        )
