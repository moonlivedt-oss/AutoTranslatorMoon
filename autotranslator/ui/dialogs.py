"""Диалоги главного окна: справка, полная история (поиск/экспорт), глоссарий игр."""

from __future__ import annotations

import csv
from collections.abc import Callable

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..paths import DATA_DIR, LOG_FILE, asset

# Действия глобальных горячих клавиш — человеческими словами (для справки).
HOTKEY_TITLES = {
    "lens": "Перевести весь экран",
    "lens_area": "Перевести кусок экрана",
    "live": "Вкл/выкл живой перевод экрана",
    "region": "Выбрать область для чтения",
    "reading": "Вкл/выкл режим чтения",
    "recapture": "Перечитать область",
    "clipboard": "Перевести буфер обмена",
    "speak": "Озвучить перевод",
    "selection": "Перевести выделенный текст",
}

WINDOW_SHORTCUTS = (
    ("Ctrl+Enter", "Перевести сейчас"),
    ("Ctrl+Shift+V", "Вставить и перевести (текст или картинку)"),
    ("Ctrl+Shift+C", "Копировать перевод"),
    ("Ctrl+D", "Поменять языки местами"),
    ("Ctrl+L", "Очистить"),
    ("Ctrl+H", "Вся история: поиск и экспорт"),
    ("Ctrl+Shift+S", "Озвучить перевод"),
    ("Esc", "Спрятать в трей"),
    ("F1", "Эта справка"),
)


def _dialog(parent: QWidget, title: str, w: int, h: int) -> QDialog:
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    dlg.resize(w, h)
    return dlg


def _open(path) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


# ---------- справка ----------
def show_help(
    parent: QWidget,
    hotkeys: dict[str, str],
    *,
    hotkeys_on: bool,
    failed: list[str],
) -> None:
    dlg = _dialog(parent, "AutoTranslator — справка", 520, 560)
    lay = QVBoxLayout(dlg)

    head = QHBoxLayout()
    pic = QLabel()
    pix = QPixmap(asset("mascot.png"))
    if not pix.isNull():
        dpr = dlg.devicePixelRatioF()
        pix = pix.scaled(
            round(96 * dpr),
            round(96 * dpr),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        pix.setDevicePixelRatio(dpr)
        pic.setPixmap(pix)
    head.addWidget(pic)
    about = QLabel(
        f"<b style='font-size:15px'>AutoTranslator {__version__}</b><br>"
        "Плавающий переводчик: текст, буфер, картинки, экран (OCR)<br>"
        "и мост для Ren'Py-игр.<br><br>"
        "Картинку можно <b>вставить</b> (Ctrl+Shift+V) или <b>перетащить</b><br>"
        "в окно — текст с неё распознается и переведётся."
    )
    about.setWordWrap(True)
    head.addWidget(about, 1)
    lay.addLayout(head)

    grid = QGridLayout()
    grid.setHorizontalSpacing(16)
    row = 0
    title = "Глобальные горячие клавиши" + ("" if hotkeys_on else " (на паузе)")
    grid.addWidget(QLabel(f"<b>{title}</b>"), row, 0, 1, 2)
    row += 1
    for action, label in HOTKEY_TITLES.items():
        combo = hotkeys.get(action, "")
        key = QLabel("+".join(p.capitalize() for p in combo.split("+")) or "—")
        key.setObjectName("chip")
        grid.addWidget(key, row, 0)
        grid.addWidget(QLabel(label), row, 1)
        row += 1
    grid.addWidget(QLabel("<b>В окне</b>"), row, 0, 1, 2)
    row += 1
    for combo, label in WINDOW_SHORTCUTS:
        key = QLabel(combo)
        key.setObjectName("chip")
        grid.addWidget(key, row, 0)
        grid.addWidget(QLabel(label), row, 1)
        row += 1
    lay.addLayout(grid)

    if failed:
        warn = QLabel(
            "Не удалось занять сочетания: "
            + ", ".join(failed)
            + '\nПроверь ключ "hotkeys" в config.json (синтаксис пакета keyboard).'
        )
        warn.setWordWrap(True)
        warn.setStyleSheet("color: #f38ba8;")
        lay.addWidget(warn)
    note = QLabel(
        'Свои сочетания — в config.json, ключ "hotkeys", например '
        '{"lens": "f9"}. Пауза клавиш — в меню значка в трее.'
    )
    note.setObjectName("muted")
    note.setWordWrap(True)
    lay.addWidget(note)
    lay.addStretch(1)

    buttons = QHBoxLayout()
    folder = QPushButton("Папка настроек")
    folder.setToolTip(str(DATA_DIR))
    folder.clicked.connect(lambda: _open(DATA_DIR))
    logb = QPushButton("Журнал ошибок")
    logb.setToolTip(str(LOG_FILE))
    logb.setEnabled(LOG_FILE.exists())
    logb.clicked.connect(lambda: _open(LOG_FILE))
    close = QPushButton("Закрыть")
    close.clicked.connect(dlg.accept)
    buttons.addWidget(folder)
    buttons.addWidget(logb)
    buttons.addStretch(1)
    buttons.addWidget(close)
    lay.addLayout(buttons)
    dlg.exec()


# ---------- полная история ----------
class HistoryDialog(QDialog):
    """Вся история и избранное: поиск по оригиналу и переводу, экспорт в файл."""

    def __init__(
        self,
        parent: QWidget,
        cfg: dict,
        on_recall: Callable[[dict], None],
        on_toggle_fav: Callable[[dict], None],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("История переводов")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.resize(560, 520)
        self._cfg = cfg
        self._recall = on_recall
        self._toggle_fav = on_toggle_fav

        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по оригиналу и переводу…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._fill)
        self.only_fav = QCheckBox("Только ★")
        self.only_fav.toggled.connect(self._fill)
        top = QHBoxLayout()
        top.addWidget(self.search, 1)
        top.addWidget(self.only_fav)

        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._preview)
        self.list.itemDoubleClicked.connect(lambda _it: self._use())
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMaximumHeight(150)
        self.count = QLabel("")
        self.count.setObjectName("muted")

        use = QPushButton("Вставить")
        use.clicked.connect(self._use)
        copy = QPushButton("Копировать")
        copy.clicked.connect(self._copy)
        self.star = QPushButton("★")
        self.star.setToolTip("В избранное / убрать")
        self.star.clicked.connect(self._star)
        delete = QPushButton("Удалить")
        delete.clicked.connect(self._delete)
        export = QPushButton("Экспорт…")
        export.setToolTip("Сохранить найденное в .txt или .csv")
        export.clicked.connect(self._export)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        for b in (use, copy, self.star, delete, export):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(close)

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.list, 1)
        lay.addWidget(self.view)
        lay.addWidget(self.count)
        lay.addLayout(row)
        self._fill()

    def _is_fav(self, item: dict) -> bool:
        key = (item.get("src"), item.get("dst"))
        return any(
            (f.get("src"), f.get("dst")) == key for f in self._cfg.get("favorites", [])
        )

    def _entries(self) -> list[dict]:
        """Избранное сверху, затем история от новых к старым, без дублей."""
        seen: set[tuple] = set()
        out: list[dict] = []
        for it in [
            *reversed(self._cfg.get("favorites", [])),
            *reversed(self._cfg.get("history", [])),
        ]:
            key = (it.get("src"), it.get("dst"))
            if key not in seen:
                seen.add(key)
                out.append(it)
        return out

    def _matches(self) -> list[dict]:
        q = self.search.text().strip().lower()
        res = []
        for it in self._entries():
            if self.only_fav.isChecked() and not self._is_fav(it):
                continue
            if q and q not in (it.get("src", "") + "\n" + it.get("dst", "")).lower():
                continue
            res.append(it)
        return res

    def _fill(self, *_args) -> None:
        self.list.clear()
        matches = self._matches()
        for it in matches:
            src = " ".join((it.get("src") or "").split())
            label = ("★ " if self._is_fav(it) else "") + (
                src[:80] + ("…" if len(src) > 80 else "")
            )
            li = QListWidgetItem(label or "—")
            li.setData(Qt.ItemDataRole.UserRole, it)
            li.setToolTip(it.get("dst", ""))
            self.list.addItem(li)
        total = len(self._entries())
        self.count.setText(f"Показано {len(matches)} из {total}")
        if matches:
            self.list.setCurrentRow(0)
        else:
            self.view.clear()

    def _current(self) -> dict | None:
        li = self.list.currentItem()
        return li.data(Qt.ItemDataRole.UserRole) if li else None

    def _preview(self, *_args) -> None:
        it = self._current()
        self.view.setPlainText(
            f"{it.get('src', '')}\n\n— — —\n\n{it.get('dst', '')}" if it else ""
        )

    def _use(self) -> None:
        it = self._current()
        if it:
            self._recall(it)
            self.accept()

    def _copy(self) -> None:
        it = self._current()
        if it:
            QApplication.clipboard().setText(it.get("dst", ""))

    def _star(self) -> None:
        it = self._current()
        if it:
            row = self.list.currentRow()
            self._toggle_fav(it)
            self._fill()
            self.list.setCurrentRow(min(row, self.list.count() - 1))

    def _delete(self) -> None:
        it = self._current()
        if not it:
            return
        key = (it.get("src"), it.get("dst"))
        for name in ("history", "favorites"):
            self._cfg[name] = [
                x
                for x in self._cfg.get(name, [])
                if (x.get("src"), x.get("dst")) != key
            ]
        row = self.list.currentRow()
        self._fill()
        self.list.setCurrentRow(min(row, self.list.count() - 1))

    def _export(self) -> None:
        items = self._matches()
        if not items:
            return
        path, _flt = QFileDialog.getSaveFileName(
            self,
            "Экспорт истории",
            str(DATA_DIR / "history.txt"),
            "Текст (*.txt);;CSV для Excel (*.csv)",
        )
        if not path:
            return
        try:
            if path.lower().endswith(".csv"):
                # utf-8-sig — чтобы Excel сразу открыл кириллицу правильно
                with open(path, "w", encoding="utf-8-sig", newline="") as f:
                    w = csv.writer(f, delimiter=";")
                    w.writerow(["Оригинал", "Перевод", "Избранное"])
                    for it in items:
                        w.writerow(
                            [
                                it.get("src", ""),
                                it.get("dst", ""),
                                "★" if self._is_fav(it) else "",
                            ]
                        )
            else:
                with open(path, "w", encoding="utf-8") as f:
                    for it in items:
                        f.write(f"{it.get('src', '')}\n→ {it.get('dst', '')}\n\n")
        except OSError as e:
            self.count.setText(f"Не удалось сохранить: {e}")
            return
        self.count.setText(f"Сохранено записей: {len(items)} → {path}")


# ---------- глоссарий игр ----------
def edit_glossary(
    parent: QWidget, *, keep_names: bool, glossary: str, game: str, names: list[str]
) -> tuple[bool, str] | None:
    """Диалог глоссария. None — отмена, иначе (не переводить имена, текст глоссария)."""
    dlg = _dialog(parent, "Глоссарий для игр", 460, 420)
    lay = QVBoxLayout(dlg)
    keep = QCheckBox("Не переводить имена персонажей (игра присылает их сама)")
    keep.setChecked(keep_names)
    lay.addWidget(keep)
    if names:
        shown = ", ".join(names[:30]) + (" …" if len(names) > 30 else "")
        hint = QLabel(f"Имена из «{game}»: {shown}")
    else:
        hint = QLabel("Имена появятся, когда подключится игра.")
    hint.setObjectName("muted")
    hint.setWordWrap(True)
    lay.addWidget(hint)
    lay.addWidget(QLabel("Свои термины — по одному в строке:"))
    edit = QPlainTextEdit()
    edit.setPlaceholderText(
        "Lucky = Лаки\nMistress = Госпожа\nSCP 682\n"
        "# строка без «=» — не переводить совсем"
    )
    edit.setPlainText(glossary)
    lay.addWidget(edit, 1)
    note = QLabel(
        "Действует на новые переводы. Уже переведённое лежит в словаре игры —\n"
        "перевести заново: в игре тест-меню → «Настройки» → «Очистить словарь»."
    )
    note.setObjectName("muted")
    note.setWordWrap(True)
    lay.addWidget(note)
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
    )
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    lay.addWidget(buttons)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    return keep.isChecked(), edit.toPlainText()
