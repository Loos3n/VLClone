"""Media information: metadata, codecs and live statistics."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QButtonGroup, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QStackedWidget, QVBoxLayout, QWidget)

from ..core.backend import MediaInfo, StreamInfo
from ..core.controller import PlayerController
from ..core.media_sources import mrl_to_path
from ..core.timefmt import format_ms
from .dialogs import BaseDialog


def _kv_grid(rows: list[tuple[str, str]]) -> QWidget:
    w = QWidget()
    grid = QGridLayout(w)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(18)
    grid.setVerticalSpacing(8)
    for r, (key, value) in enumerate(rows):
        k = QLabel(key)
        k.setObjectName("Muted")
        k.setAlignment(Qt.AlignRight | Qt.AlignTop)
        v = QLabel(value)
        v.setWordWrap(True)
        v.setTextInteractionFlags(Qt.TextSelectableByMouse)
        grid.addWidget(k, r, 0)
        grid.addWidget(v, r, 1)
    grid.setColumnStretch(1, 1)
    return w


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()


def stream_rows(s: StreamInfo) -> list[tuple[str, str]]:
    rows = [("Codec", s.codec or "Unknown")]
    if s.language and s.language.lower() not in ("und", "unknown"):
        rows.append(("Language", s.language))
    if s.description:
        rows.append(("Description", s.description))
    if s.kind == "video":
        if s.width:
            rows.append(("Resolution", f"{s.width} × {s.height}"))
        if s.fps:
            rows.append(("Frame rate", f"{s.fps:.3f}".rstrip("0").rstrip(".") + " fps"))
    if s.kind == "audio":
        if s.channels:
            names = {1: "Mono", 2: "Stereo", 6: "5.1", 8: "7.1"}
            rows.append(("Channels", names.get(s.channels, str(s.channels))))
        if s.sample_rate:
            rows.append(("Sample rate", f"{s.sample_rate:,} Hz"))
    if s.bitrate:
        rows.append(("Bitrate", f"{s.bitrate / 1000:.0f} kb/s"))
    return rows


class MediaInfoDialog(BaseDialog):
    def __init__(self, ctl: PlayerController, parent: QWidget | None = None) -> None:
        super().__init__("Media information", parent)
        self.setModal(False)
        self.ctl = ctl
        self.resize(560, 520)

        tabs = QHBoxLayout()
        tabs.setSpacing(4)
        self.stack = QStackedWidget()
        group = QButtonGroup(self)
        self.pages: dict[str, QVBoxLayout] = {}
        for i, title in enumerate(("General", "Codecs", "Statistics")):
            b = QPushButton(title)
            b.setObjectName("Tab")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            group.addButton(b, i)
            tabs.addWidget(b)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            inner = QWidget()
            lay = QVBoxLayout(inner)
            lay.setContentsMargins(4, 8, 8, 8)
            lay.setSpacing(12)
            scroll.setWidget(inner)
            self.stack.addWidget(scroll)
            self.pages[title] = lay
            if i == 0:
                b.setChecked(True)
        group.idClicked.connect(self.stack.setCurrentIndex)
        self.stack.currentChanged.connect(lambda i: group.button(i).setChecked(True))
        tabs.addStretch(1)
        self.body.addLayout(tabs)
        self.body.addWidget(self.stack, 1)

        close = QPushButton("Close")
        close.clicked.connect(self.close)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close)
        self.body.addLayout(row)

        self._timer = QTimer(self, interval=1000)
        self._timer.timeout.connect(self._refresh_stats)
        self.refresh()
        ctl.media_changed.connect(self._on_media_changed)
        ctl.tracks_changed.connect(self._on_media_changed)

    def _on_media_changed(self, *_):
        if self.isVisible():
            self.refresh()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._timer.start()

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        self._timer.stop()

    def refresh(self) -> None:
        info = self.ctl.backend.media_info() if self.ctl.has_media else MediaInfo()
        self._fill_general(info)
        self._fill_codecs(info)
        self._fill_stats(info)

    def _fill_general(self, info: MediaInfo) -> None:
        lay = self.pages["General"]
        _clear(lay)
        if not info.mrl:
            lay.addWidget(QLabel("Nothing is playing."))
            lay.addStretch(1)
            return
        location = mrl_to_path(info.mrl) or info.mrl
        rows = [("Title", info.meta.get("Title") or (self.ctl.playlist.current.title
                                                     if self.ctl.playlist.current else ""))]
        for key in ("Artist", "Album", "AlbumArtist", "Genre", "Date", "TrackNumber", "ShowName",
                    "Season", "Episode", "Director", "Publisher", "Copyright", "Language", "EncodedBy",
                    "NowPlaying", "Description", "URL"):
            if info.meta.get(key):
                label = {"AlbumArtist": "Album artist", "TrackNumber": "Track", "ShowName": "Show",
                         "EncodedBy": "Encoded by", "NowPlaying": "Now playing"}.get(key, key)
                rows.append((label, info.meta[key]))
        rows.append(("Duration", format_ms(info.duration_ms) if info.duration_ms > 0 else "Live / unknown"))
        rows.append(("Location", location))
        lay.addWidget(_kv_grid(rows))
        lay.addStretch(1)

    def _fill_codecs(self, info: MediaInfo) -> None:
        lay = self.pages["Codecs"]
        _clear(lay)
        if not info.streams:
            lay.addWidget(QLabel("No stream information yet."))
        counters: dict[str, int] = {}
        for s in info.streams:
            counters[s.kind] = counters.get(s.kind, 0) + 1
            card = QFrame()
            card.setObjectName("Card")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(14, 10, 14, 12)
            title = QLabel(f"{s.kind.capitalize()} stream {counters[s.kind]}")
            title.setObjectName("SectionTitle")
            cl.addWidget(title)
            cl.addWidget(_kv_grid(stream_rows(s)))
            lay.addWidget(card)
        lay.addStretch(1)

    def _fill_stats(self, info: MediaInfo) -> None:
        lay = self.pages["Statistics"]
        _clear(lay)
        if not info.mrl:
            lay.addWidget(QLabel("Nothing is playing."))
            lay.addStretch(1)
            return
        rate = lambda v: f"{v:,.0f} kb/s" if v > 0 else "—"  # noqa: E731
        rows = [
            ("Input bitrate", rate(info.input_bitrate_kbps)),
            ("Content bitrate", rate(info.demux_bitrate_kbps)),
            ("Decoded video blocks", f"{info.decoded_video:,}"),
            ("Displayed frames", f"{info.displayed_pictures:,}"),
            ("Lost frames", f"{info.lost_pictures:,}"),
            ("Decoded audio blocks", f"{info.decoded_audio:,}"),
            ("Lost audio buffers", f"{info.lost_audio_buffers:,}"),
            ("Engine", f"libVLC {self.ctl.backend.engine_version()}"),
        ]
        lay.addWidget(_kv_grid(rows))
        lay.addStretch(1)

    def _refresh_stats(self) -> None:
        if self.stack.currentIndex() == 2:
            self._fill_stats(self.ctl.backend.media_info() if self.ctl.has_media else MediaInfo())
