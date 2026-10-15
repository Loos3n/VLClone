"""First-run welcome screen, the crash notice, and the pickers they share with Settings."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QColorDialog, QFileDialog, QHBoxLayout, QLabel,
                               QPushButton, QVBoxLayout, QWidget)

from .. import APP_NAME
from ..core.associations import CATEGORY_LABELS
from ..core.settings import Settings
from . import theme
from .dialogs import BaseDialog
from .icons import icon


def _theme_preview(mode: str) -> QIcon:
    """A tiny window sketch in the palette of ``mode`` ("system" = half dark, half light)."""
    pm = QPixmap(QSize(120, 72))
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    halves = [theme.DARK, theme.LIGHT] if mode == "system" else [theme.PALETTES[mode]]
    for i, pal in enumerate(halves):
        p.save()
        w = 120 / len(halves)
        p.setClipRect(QRectF(i * w, 0, w, 72))
        p.setPen(QColor(pal.border))
        p.setBrush(QColor(pal.bg))
        p.drawRoundedRect(QRectF(0.5, 0.5, 119, 71), 8, 8)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(pal.surface))
        p.drawRoundedRect(QRectF(82, 6, 32, 60), 5, 5)
        p.setBrush(QColor("#000000"))
        p.drawRoundedRect(QRectF(6, 6, 72, 46), 5, 5)
        p.setBrush(QColor(theme.ACCENT))
        p.drawRoundedRect(QRectF(6, 58, 44, 6), 3, 3)
        p.setBrush(QColor(pal.text_faint))
        for y in (12, 22, 32):
            p.drawRoundedRect(QRectF(87, y, 22, 4), 2, 2)
        p.restore()
    p.end()
    return QIcon(pm)


class ThemePicker(QWidget):
    """Dark / Light / Follow system cards and accent swatches; applies live for a preview."""

    changed = Signal(str, str)  # mode, accent

    MODES = (("dark", "Dark"), ("light", "Light"), ("system", "Follow system"))

    def __init__(self, mode: str, accent: str, live: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.mode, self.accent, self.live = mode, accent, live
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        cards = QHBoxLayout()
        cards.setSpacing(10)
        self.cards = QButtonGroup(self)
        self.card_buttons: dict[str, QPushButton] = {}
        for key, label in self.MODES:
            b = QPushButton(label)
            b.setObjectName("ThemeCard")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setIconSize(QSize(120, 72))
            b.setMinimumHeight(118)
            b.setChecked(key == mode)
            b.clicked.connect(lambda _=False, k=key: self._pick_mode(k))
            self.cards.addButton(b)
            self.card_buttons[key] = b
            cards.addWidget(b)
        lay.addLayout(cards)
        row = QHBoxLayout()
        row.setSpacing(8)
        acc = QLabel("Accent colour")
        row.addWidget(acc)
        row.addSpacing(6)
        self.swatches = QButtonGroup(self)
        self.swatch_buttons: dict[str, QPushButton] = {}
        for name, value in theme.ACCENT_PRESETS:
            b = QPushButton()
            b.setObjectName("Swatch")
            b.setCheckable(True)
            b.setToolTip(name)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(f"QPushButton#Swatch {{ background: {value}; }}")
            b.clicked.connect(lambda _=False, v=value: self._pick_accent(v))
            self.swatches.addButton(b)
            self.swatch_buttons[value] = b
            row.addWidget(b)
        self.custom = QPushButton("Custom…")
        self.custom.setObjectName("Chip")
        self.custom.setCursor(Qt.PointingHandCursor)
        self.custom.clicked.connect(self._custom_accent)
        row.addWidget(self.custom)
        row.addStretch(1)
        lay.addLayout(row)
        self._refresh()
        theme.notifier().changed.connect(self._refresh)

    def _refresh(self) -> None:
        for key, b in self.card_buttons.items():
            b.setIcon(_theme_preview(key))
        preset = self.swatch_buttons.get(self.accent.lower())
        self.swatches.setExclusive(False)
        for b in self.swatch_buttons.values():
            b.setChecked(b is preset)
        self.swatches.setExclusive(True)
        self.custom.setCheckable(True)
        self.custom.setChecked(preset is None)

    def _pick_mode(self, mode: str) -> None:
        self.mode = mode
        self._emit()

    def _pick_accent(self, accent: str) -> None:
        self.accent = accent
        self._emit()

    def _custom_accent(self) -> None:
        color = QColorDialog.getColor(QColor(self.accent), self, "Accent colour")
        if color.isValid():
            self._pick_accent(color.name())
        else:
            self._refresh()

    def _emit(self) -> None:
        if self.live:
            from PySide6.QtWidgets import QApplication

            theme.apply_theme(QApplication.instance(), self.mode, self.accent)
        self._refresh()
        self.changed.emit(self.mode, self.accent)

    def set_value(self, mode: str, accent: str) -> None:
        self.mode, self.accent = mode, accent
        for key, b in self.card_buttons.items():
            b.setChecked(key == mode)
        self._refresh()


class FileTypesBox(QWidget):
    """Video / audio / playlist file types and the Explorer entries (Windows, packaged exe)."""

    def __init__(self, compact: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from ..integration import win_assoc

        self.win_assoc = win_assoc
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.boxes: dict[str, QCheckBox] = {}
        self.available = win_assoc.can_register()
        current, ctx = win_assoc.registered() if self.available else (set(), False)
        for cat, label in CATEGORY_LABELS.items():
            box = QCheckBox(f"Open {label.lower()} with {APP_NAME}")
            box.setChecked(cat in current)
            self.boxes[cat] = box
            lay.addWidget(box)
        self.context = QCheckBox(f"\"Play with {APP_NAME}\" and \"Add to {APP_NAME} playlist\" in Explorer")
        self.context.setChecked(ctx or not current)
        lay.addWidget(self.context)
        self.note = QLabel()
        self.note.setObjectName("Muted")
        self.note.setWordWrap(True)
        lay.addWidget(self.note)
        if not compact:
            row = QHBoxLayout()
            self.apply_button = QPushButton("Apply")
            self.apply_button.clicked.connect(self.apply)
            self.default_apps = QPushButton("Open Windows Default apps…")
            self.default_apps.clicked.connect(win_assoc.open_default_apps)
            row.addWidget(self.apply_button)
            row.addWidget(self.default_apps)
            row.addStretch(1)
            lay.addLayout(row)
            if not self.available:
                self.apply_button.setEnabled(False)
                self.default_apps.setEnabled(sys.platform == "win32")
        for w in [*self.boxes.values(), self.context]:
            w.setEnabled(self.available)
        if self.available:
            self.note.setText("Windows asks you to confirm the default player in Settings → Default apps; "
                              f"{APP_NAME} only becomes the default for types that have none yet.")
        elif sys.platform == "win32":
            self.note.setText("The installer sets up file types (or run the packaged VLClone.exe to do it "
                              "from here).")
        else:
            self.note.setText("File types are set by your desktop (right-click a file → Open With).")

    def categories(self) -> list[str]:
        return [c for c, b in self.boxes.items() if b.isChecked()]

    def apply(self) -> str:
        """Write the choice for the current user; returns a status message."""
        if not self.available:
            return ""
        try:
            cats = self.categories()
            if cats or self.context.isChecked():
                self.win_assoc.register(cats, self.context.isChecked())
                message = "File types registered."
            else:
                self.win_assoc.unregister()
                message = "File types removed."
        except OSError as exc:
            message = f"Could not change file types: {exc}"
        self.note.setText(message)
        return message


class WelcomeDialog(BaseDialog):
    """Shown once on a fresh install: theme, file types, library folders. Everything is skippable."""

    def __init__(self, settings: Settings, library, parent: QWidget | None = None) -> None:
        super().__init__(f"Welcome to {APP_NAME}", parent)
        self.settings = settings
        self.library = library
        self._start = (settings.theme, settings.accent_color)
        self.setMinimumWidth(620)
        intro = QLabel("A few choices to make it yours. All of them can be changed later in Settings.")
        intro.setObjectName("Muted")
        intro.setWordWrap(True)
        self.body.addWidget(intro)

        self.body.addWidget(self._section("Look"))
        self.picker = ThemePicker(settings.theme, settings.accent_color, live=True)
        self.body.addWidget(self.picker)

        self.body.addWidget(self._section("File types"))
        self.file_types = FileTypesBox(compact=True)
        self.body.addWidget(self.file_types)

        self.body.addWidget(self._section("Media library"))
        self.folders: list[tuple[str, str]] = []
        self.folder_boxes: list[tuple[QCheckBox, str, str]] = []
        known = {p for p, _ in library.folders()} if library is not None else set()
        for path, kind in ((Path.home() / "Videos", "video"), (Path.home() / "Music", "music")):
            if path.is_dir() and str(path) not in known:
                box = QCheckBox(f"Add {path} ({'videos' if kind == 'video' else 'music'})")
                box.setChecked(True)
                self.folder_boxes.append((box, str(path), kind))
                self.body.addWidget(box)
        add = QHBoxLayout()
        for kind, label in (("video", "Add a video folder…"), ("music", "Add a music folder…")):
            b = QPushButton(icon("film" if kind == "video" else "music"), f"  {label}")
            b.clicked.connect(lambda _=False, k=kind: self._add_folder(k))
            add.addWidget(b)
        add.addStretch(1)
        self.body.addLayout(add)
        self.added = QLabel()
        self.added.setObjectName("Muted")
        self.added.setWordWrap(True)
        self.body.addWidget(self.added)

        self.add_buttons("Get started")
        self.cancel_button.setText("Skip")

    @staticmethod
    def _section(text: str) -> QLabel:
        lbl = QLabel(text.upper())
        lbl.setObjectName("SectionTitle")
        return lbl

    def _add_folder(self, kind: str) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder", str(Path.home()))
        if folder:
            self.folders.append((folder, kind))
            self.added.setText("Will add: " + ", ".join(p for p, _ in self.folders))

    def accept(self) -> None:
        self.settings.theme, self.settings.accent_color = self.picker.mode, self.picker.accent
        if self.file_types.available:
            self.file_types.apply()
        chosen = [(p, k) for b, p, k in self.folder_boxes if b.isChecked()] + self.folders
        if self.library is not None:
            for path, kind in chosen:
                self.library.add_folder(path, kind)
        super().accept()

    def reject(self) -> None:
        from PySide6.QtWidgets import QApplication

        theme.apply_theme(QApplication.instance(), *self._start)  # undo the live preview
        super().reject()


class CrashNotice(BaseDialog):
    """On the launch after a crash: what happened and where the log is."""

    def __init__(self, reports, parent: QWidget | None = None) -> None:
        super().__init__(f"{APP_NAME} closed unexpectedly", parent)
        from ..core.diagnostics import logs_dir

        self.folder = logs_dir()
        text = QLabel(f"{APP_NAME} did not shut down properly last time. A report was saved; if it keeps "
                      "happening, please attach it when reporting the problem.")
        text.setWordWrap(True)
        self.body.addWidget(text)
        for report in list(reports)[:3]:
            name = QLabel(Path(report).name)
            name.setObjectName("Muted")
            name.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.body.addWidget(name)
        box = self.add_buttons("OK")
        self.cancel_button.hide()  # nothing to cancel
        open_folder = QPushButton(icon("folder"), "  Open log folder")
        open_folder.clicked.connect(self.open_folder)
        box.addButton(open_folder, box.ButtonRole.ActionRole)

    def open_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.folder)))
