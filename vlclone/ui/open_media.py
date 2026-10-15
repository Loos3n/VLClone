"""Open Media dialog: File, Disc, Network and Capture device tabs (VLC's "Media" menu, tidied up).

Each tab turns its inputs into an :class:`OpenRequest` (MRLs + libVLC media
options + a title); the main window hands that to ``PlayerController.open``,
the same path as drag-and-drop and the file pickers.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QStringListModel, Qt
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QCompleter, QDialogButtonBox,
                               QFileDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMenu, QPushButton, QSpinBox, QStackedWidget, QVBoxLayout, QWidget)

from ..core.backend import MediaBackend
from ..core.capture import DEFAULT_CAPTURE_CACHING_MS, RESOLUTIONS, CaptureRequest, capture_mrl
from ..core.discs import DISC_KINDS, DiscRequest, detect_disc_kind, disc_mrl, disc_root, list_optical_drives
from ..core.network import UrlHistory, caching_option, normalize_url, stream_kind, validate_url
from ..core.settings import Settings
from ..core.timefmt import parse_time
from .dialogs import BaseDialog
from .icons import icon
from .quick_settings import chip, section

TABS = [("file", "File"), ("disc", "Disc"), ("network", "Network"), ("capture", "Capture device")]


@dataclass
class OpenRequest:
    inputs: list[str]
    options: list[str] = field(default_factory=list)
    title: str | None = None
    play: bool = True
    kind: str = "file"


def _hint(text: str = "") -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("Muted")
    lbl.setWordWrap(True)
    return lbl


def _spin(lo: int, hi: int, value: int, suffix: str = "", step: int = 1, special: str = "") -> QSpinBox:
    s = QSpinBox()
    s.setRange(lo, hi)
    s.setValue(value)
    s.setSingleStep(step)
    s.setSuffix(suffix)
    if special:
        s.setSpecialValueText(special)
    s.setMinimumWidth(110)
    return s


class _Tab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 4, 0, 0)
        self.lay.setSpacing(8)

    def request(self) -> OpenRequest:  # raises ValueError with a user-facing message
        raise NotImplementedError

    def on_shown(self) -> None:
        pass


# ---------------------------------------------------------------------- file
class FileTab(_Tab):
    def __init__(self, start_dir: str) -> None:
        super().__init__()
        self.start_dir = start_dir
        self.lay.addWidget(section("Files"))
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.setMinimumHeight(150)
        self.lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        add = chip("Add files…", checkable=False)
        add.clicked.connect(self._add_files)
        folder = chip("Add folder…", checkable=False)
        folder.clicked.connect(self._add_folder)
        remove = chip("Remove", checkable=False)
        remove.clicked.connect(lambda: [self.list.takeItem(self.list.row(i)) for i in self.list.selectedItems()])
        for b in (add, folder, remove):
            row.addWidget(b)
        row.addStretch(1)
        self.lay.addLayout(row)

        self.use_sub = QCheckBox("Use a subtitle file")
        self.sub_edit = QLineEdit()
        self.sub_edit.setPlaceholderText("Subtitle file (.srt, .ass, …)")
        self.sub_edit.setEnabled(False)
        sub_browse = chip("Browse…", checkable=False)
        sub_browse.setEnabled(False)
        sub_browse.clicked.connect(self._pick_subtitle)
        self.use_sub.toggled.connect(self.sub_edit.setEnabled)
        self.use_sub.toggled.connect(sub_browse.setEnabled)
        sub_row = QHBoxLayout()
        sub_row.addWidget(self.sub_edit, 1)
        sub_row.addWidget(sub_browse)
        self.lay.addWidget(self.use_sub)
        self.lay.addLayout(sub_row)

    def add_paths(self, paths: list[str]) -> None:
        existing = {self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())}
        for p in paths:
            if p and p not in existing:
                existing.add(p)
                item = QListWidgetItem(icon("folder" if Path(p).is_dir() else "file"), Path(p).name or p)
                item.setData(Qt.UserRole, p)
                item.setToolTip(p)
                self.list.addItem(item)

    def _add_files(self) -> None:
        from .main_window import MEDIA_FILTER

        files, _ = QFileDialog.getOpenFileNames(self, "Add files", self.start_dir, MEDIA_FILTER)
        self.add_paths(files)

    def _add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Add folder", self.start_dir)
        self.add_paths([folder] if folder else [])

    def _pick_subtitle(self) -> None:
        from .main_window import SUBTITLE_FILTER

        path, _ = QFileDialog.getOpenFileName(self, "Subtitle file", self.start_dir, SUBTITLE_FILTER)
        if path:
            self.sub_edit.setText(path)

    def request(self) -> OpenRequest:
        paths = [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]
        if not paths:
            raise ValueError("Add at least one file or folder")
        options = []
        if self.use_sub.isChecked():
            sub = self.sub_edit.text().strip()
            if not sub or not Path(sub).is_file():
                raise ValueError("Choose an existing subtitle file")
            options.append(f":sub-file={sub}")
        return OpenRequest(paths, options, kind="file")


# ---------------------------------------------------------------------- disc
class DiscTab(_Tab):
    def __init__(self, start_dir: str) -> None:
        super().__init__()
        self.start_dir = start_dir
        self.lay.addWidget(section("Disc type"))
        kinds = QHBoxLayout()
        self.kind_group = QButtonGroup(self)
        self.kind_buttons: dict[str, QPushButton] = {}
        for key, label in DISC_KINDS.items():
            b = chip(label)
            self.kind_group.addButton(b)
            b.clicked.connect(lambda _=False, k=key: self.set_kind(k))
            kinds.addWidget(b)
            self.kind_buttons[key] = b
        kinds.addStretch(1)
        self.lay.addLayout(kinds)

        self.lay.addWidget(section("Disc device"))
        dev_row = QHBoxLayout()
        self.device = QComboBox()
        self.device.setEditable(True)
        self.device.setMinimumWidth(280)
        self.device.lineEdit().setPlaceholderText("Drive, folder or ISO image")
        self.device.currentIndexChanged.connect(self._device_picked)
        refresh = chip("↻", checkable=False)
        refresh.setToolTip("Look for discs again")
        refresh.clicked.connect(self.refresh_drives)
        folder = chip("Folder…", checkable=False)
        folder.setToolTip("A DVD/Blu-ray folder (with VIDEO_TS or BDMV)")
        folder.clicked.connect(self._pick_folder)
        iso = chip("ISO…", checkable=False)
        iso.clicked.connect(self._pick_iso)
        dev_row.addWidget(self.device, 1)
        for b in (refresh, folder, iso):
            dev_row.addWidget(b)
        self.lay.addLayout(dev_row)
        self.drive_hint = _hint()
        self.lay.addWidget(self.drive_hint)

        self.menus = QCheckBox("Disc menus")
        self.menus.setChecked(True)
        self.menus.setToolTip("Start in the disc's menu. Off: play titles directly (VLC's \"No disc menus\").")
        self.lay.addWidget(self.menus)

        self.lay.addWidget(section("Starting position"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        self.title_label = QLabel("Title")
        self.title = _spin(0, 999, 0, special="Default")
        self.chapter_label = QLabel("Chapter")
        self.chapter = _spin(0, 999, 0, special="Default")
        self.track_label = QLabel("Track")
        self.track = _spin(0, 99, 0, special="Whole disc")
        for col, (lbl, spin) in enumerate(((self.title_label, self.title), (self.chapter_label, self.chapter),
                                            (self.track_label, self.track))):
            grid.addWidget(lbl, 0, col * 2)
            grid.addWidget(spin, 0, col * 2 + 1)
        grid.setColumnStretch(6, 1)
        self.lay.addLayout(grid)
        self.kind_hint = _hint()
        self.lay.addWidget(self.kind_hint)
        self.lay.addStretch(1)
        self._drives_loaded = False
        self.set_kind("dvd")

    def on_shown(self) -> None:
        if not self._drives_loaded:
            self.refresh_drives()

    def refresh_drives(self) -> None:
        self._drives_loaded = True
        text = self.device.currentText()
        self.device.blockSignals(True)
        self.device.clear()
        drives = list_optical_drives()
        for d in drives:
            self.device.addItem(icon("drop"), d.display, (d.path, d.kind))
        self.device.blockSignals(False)
        if drives:
            self.drive_hint.setText(f"{len(drives)} optical drive{'s' if len(drives) != 1 else ''} found.")
            loaded = next((i for i, d in enumerate(drives) if d.kind), 0)
            self.device.setCurrentIndex(loaded)
            self._device_picked(loaded)
        else:
            self.drive_hint.setText("No optical drive found. You can open a disc folder or an ISO image.")
            self.device.setEditText(text)

    def _device_picked(self, index: int) -> None:
        data = self.device.itemData(index) if index >= 0 else None
        if data and data[1]:
            self.set_kind(data[1])

    def _use_path(self, path: str) -> None:
        if not path:
            return
        kind = detect_disc_kind(path)
        self.device.setEditText(disc_root(path))
        if kind:
            self.set_kind(kind)
            self.drive_hint.setText(f"Looks like a {DISC_KINDS[kind]}.")
        else:
            self.drive_hint.setText("Could not tell the disc type; pick it above.")

    def _pick_folder(self) -> None:
        self._use_path(QFileDialog.getExistingDirectory(self, "Disc folder", self.start_dir))

    def _pick_iso(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Disc image", self.start_dir,
                                              "Disc images (*.iso *.img *.udf);;All files (*)")
        self._use_path(path)

    def set_kind(self, kind: str) -> None:
        self.kind = kind
        self.kind_buttons[kind].setChecked(True)
        cd = kind == "cd"
        for w in (self.title_label, self.title, self.chapter_label, self.chapter, self.menus):
            w.setVisible(not cd)
        for w in (self.track_label, self.track):
            w.setVisible(cd)
        self.kind_hint.setText({
            "dvd": "DVD menus, titles and chapters are supported (libdvdnav). Navigate menus with the arrow "
                   "keys and Enter, or Quick settings → Chapters.",
            "bluray": "Unencrypted Blu-ray discs and folders only. Interactive (BD-J) menus need Java; "
                      "turn menus off to play titles directly.",
            "cd": "Audio CDs play track by track. Pick a track or play the whole disc.",
        }[kind])

    def location(self) -> str:
        index = self.device.currentIndex()
        data = self.device.itemData(index) if index >= 0 else None
        text = self.device.currentText().strip()
        if data and text == self.device.itemText(index):
            return data[0]
        return text

    def request(self) -> OpenRequest:
        req = DiscRequest(self.kind, self.location(), self.menus.isChecked(), self.title.value(),
                          self.chapter.value(), self.track.value())
        mrl, options, title = disc_mrl(req)
        return OpenRequest([mrl], options, title, kind="disc")


# ------------------------------------------------------------------- network
class NetworkTab(_Tab):
    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        self.history = UrlHistory(settings.recent_urls)
        self.lay.addWidget(section("Network URL"))
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("https://example.com/live.m3u8  ·  rtsp://camera.local/stream  ·  udp://@:1234")
        self.edit.setClearButtonEnabled(True)
        self._completer_model = QStringListModel(self.history.entries)
        completer = QCompleter(self._completer_model, self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        self.edit.setCompleter(completer)
        self.edit.textChanged.connect(self._update_hint)
        self.lay.addWidget(self.edit)
        self.kind = _hint()
        self.lay.addWidget(self.kind)

        head = QHBoxLayout()
        head.addWidget(section("Recent"))
        head.addStretch(1)
        self.clear = QPushButton("Clear history")
        self.clear.setFlat(True)
        self.clear.setCursor(Qt.PointingHandCursor)
        self.clear.setStyleSheet("QPushButton { background: transparent; border: none; color: #9aa2b1; }")
        self.clear.clicked.connect(self._clear_history)
        head.addWidget(self.clear)
        self.lay.addLayout(head)
        self.recent = QListWidget()
        self.recent.setMinimumHeight(120)
        self.recent.itemClicked.connect(lambda it: self.edit.setText(it.data(Qt.UserRole)))
        self.recent.setContextMenuPolicy(Qt.CustomContextMenu)
        self.recent.customContextMenuRequested.connect(self._recent_menu)
        self.lay.addWidget(self.recent, 1)
        self.empty_recent = _hint("Streams you open show up here.")
        self.lay.addWidget(self.empty_recent)

        row = QHBoxLayout()
        row.addWidget(QLabel("Network caching"))
        self.caching = _spin(0, 60000, settings.network_caching_ms, " ms", 100)
        self.caching.setToolTip("How much VLC buffers before playing. Raise it for unstable connections; "
                                "lower it for less delay on live streams.")
        row.addWidget(self.caching)
        row.addStretch(1)
        self.lay.addLayout(row)
        self.lay.addWidget(_hint("HTTP(S), HLS (.m3u8), DASH, RTSP, RTMP, UDP/RTP, MMS, FTP… YouTube and similar "
                                 "pages are resolved by VLC's Lua playlist scripts."))
        self._fill_recent()
        self._update_hint(self.edit.text())

    def _fill_recent(self) -> None:
        self.recent.clear()
        for url in self.history.entries:
            item = QListWidgetItem(icon("link"), url)
            item.setData(Qt.UserRole, url)
            item.setToolTip(f"{url}\n{stream_kind(url)}")
            self.recent.addItem(item)
        self._completer_model.setStringList(self.history.entries)
        has = bool(self.history.entries)
        self.recent.setVisible(has)
        self.clear.setVisible(has)
        self.empty_recent.setVisible(not has)

    def _recent_menu(self, pos) -> None:
        item = self.recent.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        menu.addAction(icon("trash"), "Remove from history", lambda: self._remove(item.data(Qt.UserRole)))
        menu.exec(self.recent.viewport().mapToGlobal(pos))

    def _remove(self, url: str) -> None:
        self.history.remove(url)
        self.settings.recent_urls = list(self.history.entries)
        self._fill_recent()

    def _clear_history(self) -> None:
        self.history.clear()
        self.settings.recent_urls = []
        self._fill_recent()

    def _update_hint(self, text: str) -> None:
        if not text.strip():
            self.kind.setText("")
            return
        error = validate_url(text)
        self.kind.setStyleSheet("color: #ff5d5d;" if error else "")
        self.kind.setText(error or f"Detected: {stream_kind(text)}")

    def on_shown(self) -> None:
        self.edit.setFocus()

    def request(self) -> OpenRequest:
        error = validate_url(self.edit.text())
        if error:
            raise ValueError(error)
        url = normalize_url(self.edit.text())
        options = []
        if self.caching.value() != 1000:
            options.append(caching_option(self.caching.value()))
        return OpenRequest([url], options, kind="network")

    def remember(self, url: str) -> None:
        self.history.add(url)
        self.settings.recent_urls = list(self.history.entries)


# ------------------------------------------------------------------- capture
class CaptureTab(_Tab):
    def __init__(self, backend: MediaBackend, settings: Settings) -> None:
        super().__init__()
        self.backend = backend
        self.settings = settings
        self.platform = sys.platform
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        self.video = QComboBox()
        self.audio = QComboBox()
        self.size = QComboBox()
        for w in (self.video, self.audio, self.size):
            w.setMinimumWidth(300)
        for width, height, label in RESOLUTIONS:
            self.size.addItem(label, (width, height))
        size_index = self.size.findText(settings.capture_size)
        self.size.setCurrentIndex(max(0, size_index))
        self.fps = QComboBox()
        for fps in (0, 15, 24, 25, 30, 50, 60):
            self.fps.addItem("Default" if not fps else f"{fps} fps", fps)
        self.caching = _spin(0, 10000, DEFAULT_CAPTURE_CACHING_MS, " ms", 50)
        refresh = chip("↻ Refresh", checkable=False)
        refresh.clicked.connect(self.refresh)
        rows = [("Video device", self.video), ("Audio device", self.audio), ("Resolution", self.size),
                ("Frame rate", self.fps), ("Caching", self.caching)]
        for r, (label, widget) in enumerate(rows):
            grid.addWidget(QLabel(label), r, 0)
            grid.addWidget(widget, r, 1)
        grid.addWidget(refresh, 0, 2)
        grid.setColumnStretch(3, 1)
        self.lay.addWidget(section("Capture device"))
        self.lay.addLayout(grid)
        self.status = _hint()
        self.lay.addWidget(self.status)
        self.lay.addWidget(_hint("Webcams and microphones open through DirectShow on Windows (V4L2/ALSA on "
                                 "Linux). Resolutions the camera does not support fall back to its default."))
        self.lay.addStretch(1)
        self._loaded = False

    def on_shown(self) -> None:
        if not self._loaded:
            self.refresh()

    def refresh(self) -> None:
        self._loaded = True
        try:
            devices = self.backend.capture_devices()
        except Exception as exc:  # enumeration must never break the dialog
            devices = {"video": [], "audio": []}
            self.status.setText(f"Could not list devices: {exc}")
        for combo, kind, none_label, remembered in (
                (self.video, "video", "No video", self.settings.capture_video_device),
                (self.audio, "audio", "No audio", self.settings.capture_audio_device)):
            combo.clear()
            for d in devices.get(kind, []):
                combo.addItem(d.name, d.id)
            combo.addItem(none_label, None)
            index = combo.findData(remembered) if remembered else -1
            combo.setCurrentIndex(index if index >= 0 else (0 if kind == "video" else combo.count() - 1))
        n_video = len(devices.get("video", []))
        n_audio = len(devices.get("audio", []))
        if not self.status.text().startswith("Could not"):
            self.status.setText(f"{n_video} video and {n_audio} audio device{'s' if n_audio != 1 else ''} found."
                                if n_video or n_audio else "No capture devices found.")

    def request(self) -> OpenRequest:
        width, height = self.size.currentData() or (0, 0)
        req = CaptureRequest(self.video.currentData(), self.audio.currentData(), width, height,
                             float(self.fps.currentData() or 0), self.caching.value())
        mrl, options, title = capture_mrl(req, self.platform)
        names = [c.currentText() for c in (self.video, self.audio) if c.currentData() is not None]
        if names:
            title = "Capture: " + " + ".join(names)
        self.settings.capture_video_device = self.video.currentData() or ""
        self.settings.capture_audio_device = self.audio.currentData() or ""
        self.settings.capture_size = self.size.currentText()
        return OpenRequest([mrl], options, title, kind="capture")


# -------------------------------------------------------------------- dialog
class OpenMediaDialog(BaseDialog):
    def __init__(self, backend: MediaBackend, settings: Settings, tab: str = "file", start_dir: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__("Open media", parent)
        self.settings = settings
        self.setMinimumSize(640, 540)
        self.request: OpenRequest | None = None
        tabs_row = QHBoxLayout()
        tabs_row.setSpacing(4)
        self.tab_group = QButtonGroup(self)
        self.tab_buttons: dict[str, QPushButton] = {}
        self.stack = QStackedWidget()
        self.tabs: dict[str, _Tab] = {
            "file": FileTab(start_dir),
            "disc": DiscTab(start_dir),
            "network": NetworkTab(settings),
            "capture": CaptureTab(backend, settings),
        }
        for key, label in TABS:
            b = QPushButton(label)
            b.setObjectName("Tab")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.show_tab(k))
            self.tab_group.addButton(b)
            tabs_row.addWidget(b)
            self.tab_buttons[key] = b
            self.stack.addWidget(self.tabs[key])
        tabs_row.addStretch(1)
        self.body.addLayout(tabs_row)
        self.body.addWidget(self.stack, 1)

        more = QHBoxLayout()
        more.addWidget(QLabel("Start at"))
        self.start_at = QLineEdit()
        self.start_at.setPlaceholderText("0:00")
        self.start_at.setMaximumWidth(90)
        more.addWidget(self.start_at)
        more.addSpacing(12)
        more.addWidget(QLabel("Extra options"))
        self.extra = QLineEdit()
        self.extra.setPlaceholderText(":option=value …")
        self.extra.setToolTip("libVLC media options, as in VLC's \"Show more options\" field")
        more.addWidget(self.extra, 1)
        self.body.addLayout(more)

        self.error = QLabel()
        self.error.setStyleSheet("color: #ff5d5d;")
        self.error.setWordWrap(True)
        self.body.addWidget(self.error)

        box = QDialogButtonBox()
        cancel = box.addButton(QDialogButtonBox.Cancel)
        self.enqueue_button = box.addButton("Enqueue", QDialogButtonBox.ActionRole)
        self.play_button = box.addButton("Play", QDialogButtonBox.AcceptRole)
        self.play_button.setObjectName("Primary")
        self.play_button.setDefault(True)
        for b in (cancel, self.enqueue_button, self.play_button):
            b.setCursor(Qt.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        self.enqueue_button.clicked.connect(lambda: self._finish(play=False))
        self.play_button.clicked.connect(lambda: self._finish(play=True))
        self.body.addWidget(box)
        self.show_tab(tab if tab in self.tabs else "file")

    @property
    def current(self) -> str:
        return next(k for k, t in self.tabs.items() if t is self.stack.currentWidget())

    def show_tab(self, key: str) -> None:
        self.tab_buttons[key].setChecked(True)
        self.stack.setCurrentWidget(self.tabs[key])
        self.error.setText("")
        self.tabs[key].on_shown()

    def accept(self) -> None:  # Enter in a field = Play
        self._finish(play=True)

    def build_request(self, play: bool = True) -> OpenRequest:
        req = self.tabs[self.current].request()
        req.play = play
        start = self.start_at.text().strip()
        if start:
            ms = parse_time(start)
            if ms is None:
                raise ValueError("Start time: use seconds, m:ss or h:mm:ss")
            req.options.append(f":start-time={ms / 1000:g}")
        for opt in self.extra.text().split():
            req.options.append(opt if opt.startswith(":") else ":" + opt.lstrip("-"))
        return req

    def _finish(self, play: bool) -> None:
        try:
            self.request = self.build_request(play)
        except ValueError as exc:
            self.error.setText(str(exc))
            return
        if self.request.kind == "network":
            self.tabs["network"].remember(self.request.inputs[0])
        super().accept()
