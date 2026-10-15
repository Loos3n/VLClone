"""Stream dialog: serve the current media over HTTP on the local network."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QComboBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QWidget

from ..core.settings import Settings
from ..core.streamer import STREAM_MODES, HttpStreamer
from .dialogs import BaseDialog
from .quick_settings import chip


class StreamDialog(BaseDialog):
    def __init__(self, streamer: HttpStreamer, settings: Settings,
                 current: Callable[[], tuple[str, list[str], str, int] | None],
                 parent: QWidget | None = None) -> None:
        """``current()`` returns (mrl, options, title, position ms) of what is playing, or None."""
        super().__init__("Stream", parent)
        self.setModal(False)
        self.streamer = streamer
        self.settings = settings
        self.current = current
        self.setMinimumWidth(520)
        intro = QLabel("Serve what is playing to other devices on your network. Open the address in VLC "
                       "(Media → Open Network Stream) or any player that reads MPEG-TS over HTTP.")
        intro.setObjectName("Muted")
        intro.setWordWrap(True)
        self.body.addWidget(intro)
        self.media = QLabel()
        self.body.addWidget(self.media)
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        self.mode = QComboBox()
        for key, label in STREAM_MODES.items():
            self.mode.addItem(label, key)
        self.mode.setCurrentIndex(max(0, self.mode.findData(settings.stream_mode)))
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(settings.stream_port)
        grid.addWidget(QLabel("Format"), 0, 0)
        grid.addWidget(self.mode, 0, 1)
        grid.addWidget(QLabel("Port"), 1, 0)
        grid.addWidget(self.port, 1, 1, Qt.AlignLeft)
        grid.setColumnStretch(1, 1)
        self.body.addLayout(grid)

        url_row = QHBoxLayout()
        self.url = QLineEdit()
        self.url.setReadOnly(True)
        self.url.setPlaceholderText("The address appears here while streaming")
        self.copy = chip("Copy", checkable=False)
        self.copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.url.text()))
        url_row.addWidget(self.url, 1)
        url_row.addWidget(self.copy)
        self.body.addLayout(url_row)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.body.addWidget(self.status)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.hide)
        self.toggle = QPushButton()
        self.toggle.setObjectName("Primary")
        self.toggle.clicked.connect(self._toggle)
        buttons.addWidget(close)
        buttons.addWidget(self.toggle)
        self.body.addLayout(buttons)
        self._timer = QTimer(self, interval=500)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()

    def _toggle(self) -> None:
        if self.streamer.active:
            self.streamer.stop()
            self.refresh()
            return
        cur = self.current()
        if cur is None:
            self.status.setText("Play something first, then stream it.")
            return
        mrl, options, title, position = cur
        mode = self.mode.currentData()
        error = self.streamer.start(mrl, options, title, position, mode, self.port.value())
        if error:
            self.status.setStyleSheet("color: #ff5d5d;")
            self.status.setText(error)
            return
        self.settings.stream_mode, self.settings.stream_port = mode, self.port.value()
        self.refresh()

    def refresh(self) -> None:
        state = self.streamer.poll()
        active = self.streamer.active
        cur = self.current()
        if active:
            self.media.setText(f"Streaming: {self.streamer.title}")
        else:
            self.media.setText(f"Media: {cur[2]}" if cur else "Nothing is playing.")
        self.toggle.setText("Stop streaming" if active else "Start streaming")
        self.toggle.setEnabled(active or cur is not None)
        for w in (self.mode, self.port):
            w.setEnabled(not active)
        self.url.setText(self.streamer.url() if active else "")
        self.copy.setEnabled(active)
        if state in ("starting", "streaming"):
            self.status.setStyleSheet("")
            self.status.setText("● Streaming. Firewalls may ask to allow VLClone on your network."
                                if state == "streaming" else "Starting…")
        elif state == "ended":
            self.status.setStyleSheet("")
            self.status.setText("The stream ended (the media finished).")
        elif state == "error":
            self.status.setStyleSheet("color: #ff5d5d;")
            self.status.setText(self.streamer.error)
