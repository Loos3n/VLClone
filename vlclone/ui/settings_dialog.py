"""A short, flat settings page (instead of VLC's preferences tree)."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QDoubleSpinBox, QFileDialog,
                               QFrame, QHBoxLayout, QHeaderView, QKeySequenceEdit, QLabel,
                               QLineEdit, QPushButton, QScrollArea, QSpinBox, QStackedWidget,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import APP_NAME, __version__
from ..core.settings import Settings
from ..core.shortcuts import DEFAULT_SHORTCUTS, find_conflicts, resolve_shortcuts
from .dialogs import BaseDialog
from .widgets import ToggleSwitch


class _Form(QWidget):
    """Vertical list of labelled rows bound to Settings fields."""

    def __init__(self, binder: "SettingsDialog") -> None:
        super().__init__()
        self.binder = binder
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(4, 4, 12, 4)
        self.lay.setSpacing(14)

    def heading(self, text: str, note: str = "") -> None:
        lbl = QLabel(text.upper())
        lbl.setObjectName("SectionTitle")
        self.lay.addWidget(lbl)
        if note:
            n = QLabel(note)
            n.setObjectName("Muted")
            n.setWordWrap(True)
            self.lay.addWidget(n)

    def _row(self, label: str, widget: QWidget, hint: str = "") -> None:
        row = QHBoxLayout()
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(QLabel(label))
        if hint:
            h = QLabel(hint)
            h.setObjectName("Muted")
            h.setWordWrap(True)
            text.addWidget(h)
        row.addLayout(text, 1)
        row.addWidget(widget, 0, Qt.AlignRight | Qt.AlignVCenter)
        self.lay.addLayout(row)

    def toggle(self, field: str, label: str, hint: str = "") -> ToggleSwitch:
        box = ToggleSwitch()
        self.binder.bind(field, box.isChecked, box.setChecked)
        self._row(label, box, hint)
        return box

    def spin(self, field: str, label: str, lo: int, hi: int, suffix: str = "", hint: str = "",
             scale: float = 1.0) -> QWidget:
        if scale != 1.0:
            w = QDoubleSpinBox()
            w.setDecimals(1)
            w.setSingleStep(0.5)
            w.setRange(lo / scale, hi / scale)
            self.binder.bind(field, lambda: int(round(w.value() * scale)), lambda v: w.setValue(v / scale))
        else:
            w = QSpinBox()
            w.setRange(lo, hi)
            self.binder.bind(field, w.value, w.setValue)
        w.setSuffix(suffix)
        w.setMinimumWidth(110)
        w.setAlignment(Qt.AlignRight)
        self._row(label, w, hint)
        return w

    def combo(self, field: str, label: str, options: list[tuple[str, object]], hint: str = "") -> QComboBox:
        w = QComboBox()
        for text, value in options:
            w.addItem(text, value)
        w.setMinimumWidth(140)

        def setter(value) -> None:
            idx = w.findData(value)
            w.setCurrentIndex(max(0, idx))

        self.binder.bind(field, w.currentData, setter)
        self._row(label, w, hint)
        return w

    def text(self, field: str, label: str, placeholder: str = "", hint: str = "", width: int = 220) -> QLineEdit:
        w = QLineEdit()
        w.setPlaceholderText(placeholder)
        w.setMinimumWidth(width)
        self.binder.bind(field, lambda: w.text().strip(), w.setText)
        self._row(label, w, hint)
        return w

    def finish(self) -> None:
        self.lay.addStretch(1)


class ShortcutsPage(QWidget):
    def __init__(self, settings: Settings) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(10)
        note = QLabel("Defaults follow VLC. Click a shortcut and press new keys; Backspace clears it.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        lay.addWidget(note)
        self.table = QTableWidget(len(DEFAULT_SHORTCUTS), 3)
        self.table.setHorizontalHeaderLabels(["Action", "Shortcut", "Also"])
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        self.table.setFocusPolicy(Qt.NoFocus)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.setColumnWidth(1, 150)
        self.editors: dict[str, QKeySequenceEdit] = {}
        bindings = resolve_shortcuts(settings.shortcuts)
        for row, (action, (label, defaults)) in enumerate(DEFAULT_SHORTCUTS.items()):
            self.table.setItem(row, 0, QTableWidgetItem(label))
            keys = bindings[action]
            editor = QKeySequenceEdit(QKeySequence(keys[0]) if keys else QKeySequence())
            editor.setMaximumSequenceLength(1)
            editor.setClearButtonEnabled(True)
            editor.keySequenceChanged.connect(lambda *_: self._check())
            self.editors[action] = editor
            self.table.setCellWidget(row, 1, editor)
            extra = QTableWidgetItem(", ".join(keys[1:]))
            extra.setForeground(Qt.gray)
            self.table.setItem(row, 2, extra)
            self.table.setRowHeight(row, 38)
        lay.addWidget(self.table, 1)
        bottom = QHBoxLayout()
        self.warning = QLabel()
        self.warning.setStyleSheet("color: #ff5d5d;")
        self.warning.setWordWrap(True)
        reset = QPushButton("Reset to VLC defaults")
        reset.clicked.connect(self.reset)
        bottom.addWidget(self.warning, 1)
        bottom.addWidget(reset)
        lay.addLayout(bottom)
        self._check()

    def overrides(self) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for action, editor in self.editors.items():
            defaults = DEFAULT_SHORTCUTS[action][1]
            primary = editor.keySequence().toString(QKeySequence.PortableText)
            keys = ([primary] if primary else []) + defaults[1:]
            if keys != defaults:
                result[action] = keys
        return result

    def reset(self) -> None:
        for action, editor in self.editors.items():
            defaults = DEFAULT_SHORTCUTS[action][1]
            editor.setKeySequence(QKeySequence(defaults[0]) if defaults else QKeySequence())

    def _check(self) -> None:
        conflicts = find_conflicts(resolve_shortcuts(self.overrides()))
        if conflicts:
            parts = [f"{key}: " + ", ".join(DEFAULT_SHORTCUTS[a][0] for a in actions)
                     for key, actions in conflicts.items()]
            self.warning.setText("Conflicts — " + "; ".join(parts))
        else:
            self.warning.setText("")


class SettingsDialog(BaseDialog):
    def __init__(self, settings: Settings, on_apply: Callable[[], None], parent: QWidget | None = None) -> None:
        super().__init__("Settings", parent)
        self.settings = settings
        self.on_apply = on_apply
        self._bindings: list[tuple[str, Callable, Callable]] = []
        self.resize(780, 600)

        main = QHBoxLayout()
        main.setSpacing(18)
        nav = QVBoxLayout()
        nav.setSpacing(4)
        self.stack = QStackedWidget()
        group = QButtonGroup(self)

        def page(title: str, widget: QWidget) -> None:
            b = QPushButton(title.replace("&", "&&"))
            b.setObjectName("ListRow")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumWidth(150)
            group.addButton(b, self.stack.count())
            nav.addWidget(b)
            if isinstance(widget, _Form):
                scroll = QScrollArea()
                scroll.setWidgetResizable(True)
                scroll.setFrameShape(QFrame.NoFrame)
                scroll.setWidget(widget)
                widget = scroll
            self.stack.addWidget(widget)
            if self.stack.count() == 1:
                b.setChecked(True)

        f = _Form(self)
        f.heading("Opening")
        f.toggle("play_on_open", "Start playing when files are opened")
        f.toggle("recursive_folders", "Include sub-folders when opening a folder")
        f.toggle("restore_playlist", "Restore the playlist on launch",
                 "The last session's playlist comes back; press Space to continue.")
        f.heading("Resume")
        f.toggle("resume_playback", "Continue where I left off", "Remembers the position of each file.")
        f.spin("resume_min_seconds", "Only after watching at least", 0, 600, " s")
        f.heading("Seeking")
        f.spin("seek_short_s", "Very short jump (Shift+arrow)", 1, 60, " s")
        f.spin("seek_medium_s", "Short jump (arrow / Alt+arrow)", 1, 300, " s")
        f.spin("seek_long_s", "Medium jump (Ctrl+arrow)", 5, 1800, " s")
        f.spin("seek_very_long_s", "Long jump (Ctrl+Alt+arrow)", 10, 3600, " s")
        f.heading("Mouse")
        f.toggle("pause_on_click", "Click on the video to play / pause")
        f.finish()
        page("Playback", f)

        f = _Form(self)
        f.heading("Volume")
        f.toggle("remember_volume", "Remember volume between sessions")
        f.combo("max_volume", "Maximum volume", [("100 %", 100), ("125 %", 125), ("150 %", 150), ("200 %", 200)],
                "Above 100 % amplifies the sound (may distort).")
        f.spin("volume_step", "Volume step", 1, 25, " %")
        f.heading("Effects")
        f.combo("effects_scope", "Effect settings apply to",
                [("Every file", "global"), ("Each file separately", "per_file")],
                "Equalizer, normalization, stereo mode, image adjust, flip and sharpen. "
                "Per file: each file remembers its own; new files start from the global set.")
        f.heading("Engine", "These apply the next time VLClone starts.")
        f.toggle("hardware_decoding", "Hardware-accelerated decoding", "Uses the GPU when possible.")
        f.text("preferred_audio_language", "Preferred audio language", "e.g. en, ja", width=160)
        f.text("preferred_subtitle_language", "Preferred subtitle language", "e.g. en", width=160)
        f.spin("subtitle_text_scale", "Subtitle size", 25, 400, " %")
        f.text("extra_vlc_args", "Extra libVLC options", "--option=value …",
               "For advanced users: passed to libVLC as-is.", width=260)
        f.finish()
        page("Audio & video", f)

        f = _Form(self)
        f.heading("Controls")
        f.spin("controls_hide_delay_ms", "Hide controls after", 500, 30000, " s", scale=1000.0)
        f.toggle("show_osd", "Show on-screen messages", "Volume, speed, seek and track changes.")
        f.toggle("always_on_top", "Keep the window on top")
        f.heading("Snapshots")
        row_edit = f.text("snapshot_dir", "Folder", width=260)
        browse = QPushButton("Browse…")
        browse.clicked.connect(lambda: self._browse(row_edit))
        f.lay.addWidget(browse, 0, Qt.AlignRight)
        f.combo("snapshot_format", "Format", [("PNG", "png"), ("JPEG", "jpg")])
        f.finish()
        page("Interface", f)

        self.shortcuts = ShortcutsPage(settings)
        page("Shortcuts", self.shortcuts)

        about = QWidget()
        al = QVBoxLayout(about)
        title = QLabel(f"{APP_NAME} {__version__}")
        title.setObjectName("Heading")
        al.addWidget(title)
        text = QLabel("A modern interface for VLC's playback engine (libVLC).\n"
                      "Playback, codecs and formats are handled by your installed VLC.")
        text.setWordWrap(True)
        text.setObjectName("Muted")
        al.addWidget(text)
        self.engine_label = QLabel()
        al.addWidget(self.engine_label)
        al.addStretch(1)
        page("About", about)

        group.idClicked.connect(self.stack.setCurrentIndex)
        self.stack.currentChanged.connect(lambda i: group.button(i).setChecked(True))
        nav.addStretch(1)
        main.addLayout(nav)
        main.addWidget(self.stack, 1)
        self.body.addLayout(main, 1)
        self.add_buttons("Save")
        self.load()

    def set_engine_version(self, version: str) -> None:
        self.engine_label.setText(f"Engine: libVLC {version}")

    def bind(self, field: str, getter: Callable, setter: Callable) -> None:
        self._bindings.append((field, getter, setter))

    def load(self) -> None:
        for field, _, setter in self._bindings:
            setter(getattr(self.settings, field))

    def _browse(self, edit: QLineEdit) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Snapshot folder", edit.text())
        if folder:
            edit.setText(folder)

    def accept(self) -> None:
        for field, getter, _ in self._bindings:
            setattr(self.settings, field, getter())
        self.settings.shortcuts = self.shortcuts.overrides()
        self.settings.clamp()
        self.on_apply()
        super().accept()
