"""Settings: a few clear sections with search and per-section "Reset to defaults"
(instead of VLC's preferences tree)."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QDoubleSpinBox, QFileDialog,
                               QFrame, QHBoxLayout, QHeaderView, QKeySequenceEdit, QLabel,
                               QLineEdit, QPushButton, QScrollArea, QSpinBox, QStackedWidget,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import APP_NAME, GITHUB_REPO, __version__
from ..core.settings import SECTIONS, Settings
from ..core.shortcuts import DEFAULT_SHORTCUTS, find_conflicts, resolve_shortcuts
from .dialogs import BaseDialog
from .widgets import ToggleSwitch


class _Form(QWidget):
    """Vertical list of labelled rows bound to Settings fields, for one settings section.

    Every row is a widget with its search text, so the dialog's search box can
    hide rows (and headings left without rows) across all sections.
    """

    def __init__(self, binder: "SettingsDialog", section: str) -> None:
        super().__init__()
        self.binder = binder
        self.section = section
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(4, 4, 12, 4)
        self.lay.setSpacing(14)
        self.rows: list[tuple[QWidget, str, QWidget | None]] = []  # (row, search text, heading)
        self._heading: QWidget | None = None

    def heading(self, text: str, note: str = "") -> None:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lbl = QLabel(text.upper())
        lbl.setObjectName("SectionTitle")
        lay.addWidget(lbl)
        if note:
            n = QLabel(note)
            n.setObjectName("Muted")
            n.setWordWrap(True)
            lay.addWidget(n)
        box.setProperty("search", f"{text} {note}".lower())
        self.lay.addWidget(box)
        self._heading = box

    def add_row_widget(self, widget: QWidget, search: str) -> None:
        self.lay.addWidget(widget)
        self.rows.append((widget, search.lower(), self._heading))

    def _row(self, label: str, widget: QWidget, hint: str = "", field: str = "") -> QWidget:
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
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
        self.add_row_widget(holder, f"{label} {hint} {field.replace('_', ' ')}")
        return holder

    def toggle(self, field: str, label: str, hint: str = "") -> ToggleSwitch:
        box = ToggleSwitch()
        self.binder.bind(field, box.isChecked, box.setChecked)
        self._row(label, box, hint, field)
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
        self._row(label, w, hint, field)
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
        self._row(label, w, hint, field)
        return w

    def text(self, field: str, label: str, placeholder: str = "", hint: str = "", width: int = 220) -> QLineEdit:
        w = QLineEdit()
        w.setPlaceholderText(placeholder)
        w.setMinimumWidth(width)
        self.binder.bind(field, lambda: w.text().strip(), w.setText)
        self._row(label, w, hint, field)
        return w

    def folder(self, field: str, label: str, placeholder: str = "", hint: str = "") -> QLineEdit:
        """A path field with its Browse… button on the same row."""
        holder = QWidget()
        lay = QHBoxLayout(holder)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        edit.setMinimumWidth(240)
        browse = QPushButton("Browse…")
        browse.clicked.connect(lambda: self.binder._browse(edit))
        lay.addWidget(edit)
        lay.addWidget(browse)
        self.binder.bind(field, lambda: edit.text().strip(), edit.setText)
        self._row(label, holder, hint, field)
        return edit

    def finish(self) -> None:
        title, fields = SECTIONS[self.section]
        reset = QPushButton(f"Reset {title} to defaults")
        reset.setObjectName("Flat")
        reset.setCursor(Qt.PointingHandCursor)
        reset.clicked.connect(lambda: self.binder.reset_section(self.section))
        self.reset_button = reset
        self.lay.addStretch(1)
        self.lay.addWidget(reset, 0, Qt.AlignLeft)

    def filter(self, needle: str) -> int:
        """Show rows matching ``needle`` (all when empty); returns how many are visible."""
        title = SECTIONS[self.section][0].lower()
        shown = 0
        headings: dict[int, bool] = {}
        for row, text, heading in self.rows:
            hit = not needle or needle in text or needle in title or (
                heading is not None and needle in heading.property("search"))
            row.setVisible(hit)
            shown += hit
            if heading is not None:
                headings[id(heading)] = headings.get(id(heading), False) or hit
        for row, _, heading in self.rows:
            if heading is not None:
                heading.setVisible(headings[id(heading)])
        return shown


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
        self.warning.setObjectName("Error")
        self.warning.setWordWrap(True)
        reset = QPushButton("Reset to VLC defaults")
        reset.clicked.connect(self.reset)
        bottom.addWidget(self.warning, 1)
        bottom.addWidget(reset)
        lay.addLayout(bottom)
        self._check()

    def filter(self, needle: str) -> int:
        shown = 0
        for row, (action, (label, _)) in enumerate(DEFAULT_SHORTCUTS.items()):
            keys = " ".join(resolve_shortcuts(self.overrides()).get(action, []))
            hit = not needle or needle in label.lower() or needle in keys.lower() or "shortcut" in needle
            self.table.setRowHidden(row, not hit)
            shown += hit
        return shown

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
    PAGES = ("playback", "audio_video", "appearance", "library", "system", "shortcuts", "about")

    def __init__(self, settings: Settings, on_apply: Callable[[], None], parent: QWidget | None = None,
                 host=None) -> None:
        super().__init__("Settings", parent)
        self.settings = settings
        self.on_apply = on_apply
        self.host = host  # the main window: update check, file types
        self._bindings: list[tuple[str, Callable, Callable]] = []
        self.forms: dict[str, _Form] = {}
        self.resize(860, 640)

        main = QHBoxLayout()
        main.setSpacing(18)
        nav = QVBoxLayout()
        nav.setSpacing(4)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search settings…")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(170)
        self.search.textChanged.connect(self.apply_search)
        nav.addWidget(self.search)
        nav.addSpacing(6)
        self.stack = QStackedWidget()
        self.group = group = QButtonGroup(self)
        self.nav_buttons: dict[str, QPushButton] = {}
        self._page_keys: list[str] = []

        def page(key: str, title: str, widget: QWidget) -> None:
            b = QPushButton(title.replace("&", "&&"))
            b.setObjectName("ListRow")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumWidth(170)
            group.addButton(b, self.stack.count())
            self.nav_buttons[key] = b
            self._page_keys.append(key)
            nav.addWidget(b)
            if isinstance(widget, _Form):
                self.forms[key] = widget
                scroll = QScrollArea()
                scroll.setWidgetResizable(True)
                scroll.setFrameShape(QFrame.NoFrame)
                scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
                scroll.setWidget(widget)
                widget = scroll
            self.stack.addWidget(widget)
            if self.stack.count() == 1:
                b.setChecked(True)

        f = _Form(self, "playback")
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
        f.heading("Network streams")
        f.spin("network_caching_ms", "Network caching", 0, 60000, " ms",
               "VLC's buffer for streams (default 1000 ms). The Open Media dialog can override it per stream.")
        f.spin("network_timeout_s", "Give up on streams after", 0, 600, " s",
               "Streams that never start show an error after this long (0 = wait forever).")
        f.heading("Mouse")
        f.toggle("pause_on_click", "Click on the video to play / pause")
        f.finish()
        page("playback", "Playback", f)

        f = _Form(self, "audio_video")
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
        page("audio_video", "Audio & video", f)

        f = _Form(self, "appearance")
        f.heading("Theme")
        from .welcome import ThemePicker

        self.theme_picker = ThemePicker(settings.theme, settings.accent_color, live=False)
        f.add_row_widget(self.theme_picker, "theme dark light system follow accent colour color appearance")
        self.bind("theme", lambda: self.theme_picker.mode,
                  lambda v: self.theme_picker.set_value(v, self.theme_picker.accent))
        self.bind("accent_color", lambda: self.theme_picker.accent,
                  lambda v: self.theme_picker.set_value(self.theme_picker.mode, v))
        f.heading("Controls")
        f.spin("controls_hide_delay_ms", "Hide controls after", 500, 30000, " s", scale=1000.0)
        f.toggle("show_osd", "Show on-screen messages", "Volume, speed, seek and track changes.")
        f.toggle("seek_previews", "Thumbnails when hovering the seek bar",
                 "Local video files; made in the background and cached with the library thumbnails.")
        f.heading("Window")
        f.toggle("always_on_top", "Keep the window on top (picture in picture)",
                 "Also Ctrl+Shift+T or the pin above the video. The mini player (Ctrl+Shift+M) is always on top.")
        f.finish()
        page("appearance", "Appearance", f)

        f = _Form(self, "library")
        f.heading("Media library")
        f.toggle("library_watch", "Rescan when library folders change")
        f.toggle("library_thumbnails", "Make video thumbnails")
        f.heading("Snapshots")
        f.folder("snapshot_dir", "Snapshot folder")
        f.combo("snapshot_format", "Snapshot format", [("PNG", "png"), ("JPEG", "jpg")])
        f.heading("Recordings", "Record (Shift+R) and Capture → Record to file save here.")
        from ..core.paths import default_videos_dir

        f.folder("record_dir", "Recordings folder", default_videos_dir())
        f.finish()
        page("library", "Library & files", f)

        f = _Form(self, "system")
        f.heading("Opening files from Explorer")
        f.toggle("single_instance", "Use one window",
                 "Files opened from Explorer or a second launch go to the running window.")
        f.combo("instance_open_mode", "Files opened that way",
                [("Play now", "play"), ("Add to the playlist", "enqueue")],
                "\"Play with\" and \"Add to playlist\" in Explorer always do what they say.")
        f.heading("File types")
        from .welcome import FileTypesBox

        self.file_types = FileTypesBox()
        f.add_row_widget(self.file_types, "file types associations explorer context menu open with default apps")
        f.heading("Desktop")
        f.toggle("prevent_sleep", "Keep the screen on while a video plays",
                 "Not for music, and not while paused.")
        f.toggle("media_keys", "Media keys and the Windows media overlay",
                 "Play/pause, next and previous from the keyboard and the volume flyout.")
        f.toggle("taskbar_controls", "Taskbar buttons and progress",
                 "Previous, play/pause and next under the taskbar thumbnail.")
        f.heading("Updates")
        f.toggle("check_updates", "Check for updates",
                 "At most once a day, on launch. VLClone only tells you; nothing is installed.")
        f.finish()
        page("system", "System", f)

        self.shortcuts = ShortcutsPage(settings)
        page("shortcuts", "Shortcuts", self.shortcuts)
        page("about", "About", self._about_page())

        group.idClicked.connect(self.stack.setCurrentIndex)
        self.stack.currentChanged.connect(lambda i: group.button(i).setChecked(True))
        nav.addStretch(1)
        main.addLayout(nav)
        main.addWidget(self.stack, 1)
        self.body.addLayout(main, 1)
        self.add_buttons("Save")
        self.load()

    def _about_page(self) -> QWidget:
        about = QWidget()
        al = QVBoxLayout(about)
        al.setSpacing(10)
        title = QLabel(f"{APP_NAME} {__version__}")
        title.setObjectName("Heading")
        al.addWidget(title)
        text = QLabel("A modern interface for VLC's playback engine (libVLC).\n"
                      "Playback, codecs and formats are handled by libVLC.")
        text.setWordWrap(True)
        text.setObjectName("Muted")
        al.addWidget(text)
        self.engine_label = QLabel()
        al.addWidget(self.engine_label)
        row = QHBoxLayout()
        self.update_button = QPushButton("Check for updates")
        self.update_button.clicked.connect(self._check_updates)
        self.update_button.setEnabled(self.host is not None)
        logs = QPushButton("Open log folder")
        logs.clicked.connect(self._open_logs)
        releases = QPushButton("Release notes")
        releases.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl(f"https://github.com/{GITHUB_REPO}/releases")))
        for b in (self.update_button, logs, releases):
            row.addWidget(b)
        row.addStretch(1)
        al.addLayout(row)
        self.update_status = QLabel()
        self.update_status.setObjectName("Muted")
        self.update_status.setWordWrap(True)
        self.update_status.setOpenExternalLinks(True)
        al.addWidget(self.update_status)
        al.addStretch(1)
        return about

    def _check_updates(self) -> None:
        self.update_status.setText("Checking…")
        self.update_button.setEnabled(False)
        self.host.check_updates_now(self._update_done)

    def _update_done(self, result) -> None:
        self.update_button.setEnabled(True)
        if result.release is not None:
            r = result.release
            self.update_status.setText(f'{APP_NAME} {r.version} is available: <a href="{r.url}">download</a>.')
        elif result.checked:
            self.update_status.setText(f"You have the latest version ({__version__}).")
        else:
            self.update_status.setText(f"Could not check: {result.error}")

    def _open_logs(self) -> None:
        from ..core.diagnostics import logs_dir

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(logs_dir())))

    def set_engine_version(self, version: str) -> None:
        self.engine_label.setText(f"Engine: libVLC {version}")

    def show_section(self, key: str) -> None:
        self.stack.setCurrentIndex(self._page_keys.index(key))

    def current_section(self) -> str:
        return self._page_keys[self.stack.currentIndex()]

    def bind(self, field: str, getter: Callable, setter: Callable) -> None:
        self._bindings.append((field, getter, setter))

    def load(self) -> None:
        for field, _, setter in self._bindings:
            setter(getattr(self.settings, field))

    def reset_section(self, section: str) -> None:
        """Put a section's controls back to the defaults (saved with Save, undone with Cancel)."""
        defaults = Settings()
        fields = set(SECTIONS[section][1])
        for field, _, setter in self._bindings:
            if field in fields:
                setter(getattr(defaults, field))

    def apply_search(self, text: str) -> None:
        needle = text.strip().lower()
        first = None
        for key in self._page_keys:
            if key in self.forms:
                hits = self.forms[key].filter(needle)
            elif key == "shortcuts":
                hits = self.shortcuts.filter(needle)
            else:
                hits = 1 if not needle or needle in "about version update log engine" else 0
            self.nav_buttons[key].setEnabled(hits > 0)
            if hits and first is None:
                first = key
        if needle and first is not None and not self.nav_buttons[self.current_section()].isEnabled():
            self.show_section(first)

    def _browse(self, edit: QLineEdit) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder", edit.text())
        if folder:
            edit.setText(folder)

    def accept(self) -> None:
        for field, getter, _ in self._bindings:
            setattr(self.settings, field, getter())
        self.settings.shortcuts = self.shortcuts.overrides()
        self.settings.clamp()
        self.on_apply()
        super().accept()
