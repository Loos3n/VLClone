"""Quick-settings popover: speed, loop, tracks and video in one place.

Pages are small widgets with a ``refresh()`` method; new feature areas
(equalizer, effects, ...) plug in with :meth:`QuickSettings.add_page`.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRect, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (QButtonGroup, QFrame, QGraphicsDropShadowEffect, QGridLayout,
                               QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSlider, QStackedWidget,
                               QVBoxLayout, QWidget)

from ..core.abloop import ABState
from ..core.controller import PlayerController
from ..core.playlist import RepeatMode
from ..core.stepping import RATE_PRESETS
from ..core.timefmt import format_ms


def chip(text: str, checkable: bool = True) -> QPushButton:
    b = QPushButton(text)
    b.setObjectName("Chip")
    b.setCheckable(checkable)
    b.setCursor(Qt.PointingHandCursor)
    b.setFocusPolicy(Qt.NoFocus)
    return b


def section(text: str) -> QLabel:
    lbl = QLabel(text.upper())
    lbl.setObjectName("SectionTitle")
    return lbl


def badge(kind: str = "reload") -> QLabel:
    """Small pill that tells whether a control applies live or reloads the media."""
    if kind == "live":
        lbl = QLabel("● live")
        lbl.setObjectName("LiveBadge")
        lbl.setToolTip("Applies instantly while playing")
    else:
        lbl = QLabel("↻ reloads")
        lbl.setObjectName("ReloadBadge")
        lbl.setToolTip("libVLC applies this filter when the media loads, so the video reloads at "
                       "the same position about half a second after your last change")
    return lbl


def section_row(text: str, kind: str = "reload") -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(8)
    row.addWidget(section(text))
    row.addWidget(badge(kind), 0, Qt.AlignVCenter)
    row.addStretch(1)
    return row


class ValueSlider(QWidget):
    """Horizontal slider for a float value, with a live value label."""

    def __init__(self, lo: float, hi: float, scale: float, on_change: Callable[[float], None],
                 fmt: Callable[[float], str] = lambda v: f"{v:.2f}") -> None:
        super().__init__()
        self.scale = scale
        self.fmt = fmt
        self.on_change = on_change
        self._updating = False
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(int(round(lo * scale)), int(round(hi * scale)))
        self.slider.setFocusPolicy(Qt.NoFocus)
        self.slider.valueChanged.connect(self._moved)
        self.label = QLabel()
        self.label.setObjectName("ValueLabel")
        self.label.setMinimumWidth(44)
        self.label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.label)

    def _moved(self, raw: int) -> None:
        value = raw / self.scale
        self.label.setText(self.fmt(value))
        if not self._updating:
            self.on_change(value)

    def set_value(self, value: float) -> None:
        self._updating = True
        self.slider.setValue(int(round(value * self.scale)))
        self.label.setText(self.fmt(value))
        self._updating = False


def chip_grid(options: list[tuple[str, object]], on_pick: Callable[[object], None],
              columns: int = 5) -> tuple[QWidget, dict]:
    """A grid of exclusive chips; returns (widget, value -> button)."""
    w = QWidget()
    grid = QGridLayout(w)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setSpacing(6)
    group = QButtonGroup(w)
    group.setExclusive(True)
    buttons = {}
    for i, (label, value) in enumerate(options):
        b = chip(label)
        group.addButton(b)
        b.clicked.connect(lambda _=False, v=value: on_pick(v))
        grid.addWidget(b, i // columns, i % columns)
        buttons[value] = b
    return w, buttons


class Page(QWidget):
    def __init__(self, ctl: PlayerController) -> None:
        super().__init__()
        self.ctl = ctl
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(8)

    def refresh(self) -> None:  # pragma: no cover - overridden
        pass


class PlaybackPage(Page):
    def __init__(self, ctl: PlayerController) -> None:
        super().__init__(ctl)
        self.lay.addWidget(section("Speed"))
        presets = [(f"{r:g}×", r) for r in RATE_PRESETS]
        grid, self.rate_buttons = chip_grid(presets, lambda r: ctl.set_rate(r), columns=5)
        self.lay.addWidget(grid)
        row = QHBoxLayout()
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(25, 400)  # hundredths
        self.slider.setSingleStep(5)
        self.slider.setPageStep(25)
        self.slider.setFocusPolicy(Qt.NoFocus)
        self.slider.valueChanged.connect(self._slider_moved)
        self.rate_label = QLabel("1×")
        self.rate_label.setMinimumWidth(48)
        self.rate_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self.slider, 1)
        row.addWidget(self.rate_label)
        self.lay.addLayout(row)

        self.lay.addWidget(section("Loop"))
        loop_row = QHBoxLayout()
        loop_row.setSpacing(6)
        self.ab_button = chip("A-B: set A", checkable=False)
        self.ab_button.clicked.connect(ctl.toggle_ab)
        self.ab_clear = chip("Clear", checkable=False)
        self.ab_clear.clicked.connect(ctl.clear_ab)
        loop_row.addWidget(self.ab_button, 1)
        loop_row.addWidget(self.ab_clear)
        self.lay.addLayout(loop_row)

        grid, self.repeat_buttons = chip_grid(
            [("No repeat", RepeatMode.NONE), ("Repeat all", RepeatMode.ALL), ("Repeat one", RepeatMode.ONE)],
            lambda m: ctl.set_repeat(m), columns=3)
        self.lay.addWidget(grid)
        self.shuffle = chip("Shuffle")
        self.shuffle.clicked.connect(lambda checked: ctl.set_shuffle(checked))
        self.lay.addWidget(self.shuffle)
        self.lay.addStretch(1)
        self._updating = False

    def _slider_moved(self, value: int) -> None:
        if not self._updating:
            self.ctl.set_rate(value / 100)

    def refresh(self) -> None:
        self._updating = True
        rate = self.ctl.rate
        for value, b in self.rate_buttons.items():
            b.setChecked(abs(value - rate) < 1e-3)
        if not any(b.isChecked() for b in self.rate_buttons.values()):
            group = next(iter(self.rate_buttons.values())).group()
            group.setExclusive(False)
            for b in self.rate_buttons.values():
                b.setChecked(False)
            group.setExclusive(True)
        self.slider.setValue(int(round(rate * 100)))
        self.rate_label.setText(f"{rate:g}×")
        ab = self.ctl.ab
        self.ab_button.setText({
            ABState.OFF: "A-B loop: set A",
            ABState.A_SET: f"A = {format_ms(ab.a)} · set B",
            ABState.ACTIVE: f"Looping {format_ms(ab.a)} → {format_ms(ab.b)}",
        }[ab.state])
        self.ab_clear.setEnabled(ab.state is not ABState.OFF)
        self.repeat_buttons[self.ctl.playlist.repeat].setChecked(True)
        self.shuffle.setChecked(self.ctl.playlist.shuffle)
        self._updating = False


class QuickSettings(QWidget):
    closed = Signal()

    def __init__(self, ctl: PlayerController, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.ctl = ctl
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)
        self.frame = QFrame()
        self.frame.setObjectName("Popover")
        shadow = QGraphicsDropShadowEffect(self.frame, blurRadius=36, offset=QPoint(0, 8))
        shadow.setColor(QColor(0, 0, 0, 150))
        self.frame.setGraphicsEffect(shadow)
        outer.addWidget(self.frame)
        lay = QVBoxLayout(self.frame)
        lay.setContentsMargins(14, 12, 14, 14)
        lay.setSpacing(10)
        self.tabs_row = QHBoxLayout()
        self.tabs_row.setSpacing(4)
        self.tabs_row.addStretch(1)
        lay.addLayout(self.tabs_row)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack)
        self.tab_group = QButtonGroup(self)
        self.pages: dict[str, Page] = {}
        self._anchor = QRect()
        self.setFixedWidth(430)

        self._anim = QPropertyAnimation(self, b"windowOpacity", self, duration=160)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._slide = QPropertyAnimation(self, b"pos", self, duration=180)
        self._slide.setEasingCurve(QEasingCurve.OutCubic)

        self.add_page("playback", "Playback", PlaybackPage(ctl))
        for sig in (ctl.rate_changed, ctl.ab_changed, ctl.repeat_changed, ctl.shuffle_changed,
                    ctl.tracks_changed, ctl.delays_changed, ctl.video_changed, ctl.media_changed,
                    ctl.effects_changed):
            sig.connect(lambda *_: self._refresh_if_visible())

    def add_page(self, key: str, title: str, page: Page) -> None:
        b = QPushButton(title)
        b.setObjectName("Tab")
        b.setCheckable(True)
        b.setCursor(Qt.PointingHandCursor)
        b.setFocusPolicy(Qt.NoFocus)
        self.tab_group.addButton(b)
        b.clicked.connect(lambda _=False, k=key: self.show_page(k))
        self.tabs_row.insertWidget(self.tabs_row.count() - 1, b)
        self.stack.addWidget(page)
        self.pages[key] = page
        page.setProperty("tab_button", b)
        if len(self.pages) == 1:
            b.setChecked(True)
        else:
            page.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)

    def show_page(self, key: str) -> None:
        page = self.pages.get(key)
        if page is None:
            return
        page.property("tab_button").setChecked(True)
        # Size the popover to the visible page only, not the tallest one.
        for other in self.pages.values():
            other.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        page.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.stack.setCurrentWidget(page)
        page.refresh()
        if self.isVisible():
            # Grow upwards so the popover never covers the control bar.
            self._fit()
            self._slide.stop()
            self.move(self._target_pos())

    def _refresh_if_visible(self) -> None:
        if self.isVisible():
            self.stack.currentWidget().refresh()

    def _fit(self) -> None:
        self.frame.layout().activate()
        self.layout().activate()
        self.resize(self.width(), self.sizeHint().height())

    def _target_pos(self) -> QPoint:
        """Bottom-right aligned just above the anchor, kept on screen."""
        anchor = self._anchor
        screen = QGuiApplication.screenAt(anchor.center()) or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()
        x = anchor.right() - self.width() + 18
        y = anchor.top() - self.height() + 10
        x = max(avail.left(), min(x, avail.right() - self.width()))
        y = max(avail.top(), y)
        return QPoint(x, y)

    def popup(self, anchor: QRect, page: str | None = None) -> None:
        self._anchor = anchor
        if page:
            self.show_page(page)
        else:
            self.stack.currentWidget().refresh()
        self._fit()
        target = self._target_pos()
        self.setWindowOpacity(0.0)
        self.move(target + QPoint(0, 12))
        self.show()
        self._anim.stop()
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()
        self._slide.stop()
        self._slide.setStartValue(target + QPoint(0, 12))
        self._slide.setEndValue(target)
        self._slide.start()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        # Qt may grow the window after layout settles; keep the bottom anchored.
        if self.isVisible() and not self._anchor.isNull():
            self._slide.stop()
            self.move(self._target_pos())

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        self.closed.emit()


class DelayControl(QWidget):
    """− value + reset, for audio/subtitle offsets."""

    def __init__(self, on_change: Callable[[int], None], step: int = 50) -> None:
        super().__init__()
        self.on_change = on_change
        self.step = step
        self.value = 0
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        minus = chip("−", checkable=False)
        plus = chip("+", checkable=False)
        minus.setFixedWidth(44)
        plus.setFixedWidth(44)
        minus.setAutoRepeat(True)
        plus.setAutoRepeat(True)
        minus.clicked.connect(lambda: self.on_change(self.value - self.step))
        plus.clicked.connect(lambda: self.on_change(self.value + self.step))
        self.label = QLabel("0 ms")
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setMinimumWidth(90)
        self.reset = chip("Reset", checkable=False)
        self.reset.clicked.connect(lambda: self.on_change(0))
        lay.addWidget(minus)
        lay.addWidget(self.label, 1)
        lay.addWidget(plus)
        lay.addWidget(self.reset)

    def set_value(self, ms: int) -> None:
        from ..core.timefmt import format_delay

        self.value = ms
        self.label.setText(format_delay(ms))
        self.reset.setEnabled(ms != 0)


class TrackList(QWidget):
    """Exclusive list of tracks, rebuilt when the track set changes."""

    def __init__(self, on_pick: Callable[[int], None], empty_text: str) -> None:
        super().__init__()
        self.on_pick = on_pick
        self.empty_text = empty_text
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(2)
        self.group = QButtonGroup(self)
        self._signature: tuple = ()
        self.buttons: dict[int, QPushButton] = {}

    def set_tracks(self, tracks, current: int) -> None:
        signature = tuple((t.id, t.name) for t in tracks)
        if signature != self._signature:
            self._signature = signature
            while self.lay.count():
                w = self.lay.takeAt(0).widget()
                if w:
                    w.deleteLater()
            self.buttons = {}
            if not tracks:
                lbl = QLabel(self.empty_text)
                lbl.setObjectName("Muted")
                self.lay.addWidget(lbl)
            for t in tracks:
                name = "Off" if t.id == -1 else t.name
                b = QPushButton(name)
                b.setObjectName("ListRow")
                b.setCheckable(True)
                b.setCursor(Qt.PointingHandCursor)
                b.setFocusPolicy(Qt.NoFocus)
                b.clicked.connect(lambda _=False, i=t.id: self.on_pick(i))
                self.group.addButton(b)
                self.lay.addWidget(b)
                self.buttons[t.id] = b
        if current in self.buttons:
            self.buttons[current].setChecked(True)


class AudioPage(Page):
    def __init__(self, ctl: PlayerController, open_effects: Callable[[], None] | None = None) -> None:
        super().__init__(ctl)
        self.lay.addWidget(section("Audio track"))
        self.tracks = TrackList(ctl.set_audio_track, "No audio tracks")
        self.lay.addWidget(self.tracks)
        self.lay.addWidget(section("Audio delay"))
        self.delay = DelayControl(ctl.set_audio_delay)
        self.lay.addWidget(self.delay)
        hint = QLabel("Positive values delay the sound (J / K).")
        hint.setObjectName("Muted")
        self.lay.addWidget(hint)
        if open_effects:
            effects = chip("Equalizer & audio effects…", checkable=False)
            effects.setToolTip("Ctrl+Shift+E")
            effects.clicked.connect(open_effects)
            self.lay.addWidget(effects)
        self.lay.addStretch(1)

    def refresh(self) -> None:
        self.tracks.set_tracks(self.ctl.audio_tracks(), self.ctl.audio_track())
        self.delay.set_value(self.ctl.audio_delay)


class SubtitlesPage(Page):
    def __init__(self, ctl: PlayerController, load_file: Callable[[], None]) -> None:
        super().__init__(ctl)
        header = QHBoxLayout()
        header.addWidget(section("Subtitle track"))
        header.addStretch(1)
        load = chip("Load file…", checkable=False)
        load.clicked.connect(load_file)
        header.addWidget(load)
        self.lay.addLayout(header)
        self.tracks = TrackList(ctl.set_subtitle_track, "No subtitles in this media")
        self.lay.addWidget(self.tracks)
        self.lay.addWidget(section("Subtitle delay"))
        self.delay = DelayControl(ctl.set_subtitle_delay)
        self.lay.addWidget(self.delay)
        hint = QLabel("Positive values show subtitles later (G / H).")
        hint.setObjectName("Muted")
        self.lay.addWidget(hint)
        self.lay.addStretch(1)

    def refresh(self) -> None:
        self.tracks.set_tracks(self.ctl.subtitle_tracks(), self.ctl.subtitle_track())
        self.delay.set_value(self.ctl.subtitle_delay)


class VideoPage(Page):
    def __init__(self, ctl: PlayerController, snapshot: Callable[[], None],
                 open_effects: Callable[[], None] | None = None) -> None:
        super().__init__(ctl)
        from ..core.effects import SHARPEN_MAX, SHARPEN_MIN
        from ..core.controller import (ASPECT_RATIOS, CROP_RATIOS, DEINTERLACE_MODES, ROTATIONS,
                                       ZOOM_LEVELS, zoom_label)

        self.lay.addWidget(section("Aspect ratio"))
        grid, self.aspect = chip_grid([(r or "Auto", r) for r in ASPECT_RATIOS], ctl.set_aspect_ratio, 5)
        self.lay.addWidget(grid)
        self.lay.addWidget(section("Crop"))
        grid, self.crop = chip_grid([(r or "None", r) for r in CROP_RATIOS], ctl.set_crop, 5)
        self.lay.addWidget(grid)

        self.lay.addWidget(section("Zoom"))
        grid, self.zoom = chip_grid([(zoom_label(z), z) for z in ZOOM_LEVELS], ctl.set_zoom, 5)
        self.lay.addWidget(grid)

        self.lay.addLayout(section_row("Rotate & flip"))
        transform = QHBoxLayout()
        transform.setSpacing(6)
        grid, self.rotation = chip_grid([(f"{r}°", r) for r in ROTATIONS], ctl.set_rotation, 4)
        transform.addWidget(grid, 4)
        self.flip_h = chip("⇆ Flip")
        self.flip_h.setToolTip("Mirror horizontally")
        self.flip_h.clicked.connect(lambda on: ctl.set_flip(horizontal=on))
        self.flip_v = chip("⇅ Flip")
        self.flip_v.setToolTip("Mirror vertically")
        self.flip_v.clicked.connect(lambda on: ctl.set_flip(vertical=on))
        transform.addWidget(self.flip_h, 1)
        transform.addWidget(self.flip_v, 1)
        self.lay.addLayout(transform)

        self.lay.addLayout(section_row("Sharpen"))
        sharpen = QHBoxLayout()
        sharpen.setSpacing(8)
        self.sharpen = chip("Off")
        self.sharpen.setFixedWidth(56)
        self.sharpen.clicked.connect(ctl.set_sharpen)
        self.sharpen_strength = ValueSlider(SHARPEN_MIN, SHARPEN_MAX, 100, ctl.set_sharpen_strength)
        self.sharpen_strength.setToolTip("Sharpen strength (libVLC sigma)")
        sharpen.addWidget(self.sharpen)
        sharpen.addWidget(self.sharpen_strength, 1)
        self.lay.addLayout(sharpen)

        self.lay.addWidget(section("Deinterlace"))
        labels = {None: "Off", "x": "X", "yadif2x": "Yadif 2×", "ivtc": "IVTC"}
        grid, self.deinterlace = chip_grid(
            [(labels.get(m, (m or "").capitalize()), m) for m in DEINTERLACE_MODES], ctl.set_deinterlace, 4)
        self.lay.addWidget(grid)

        actions = QHBoxLayout()
        shot = chip("Take snapshot", checkable=False)
        shot.clicked.connect(snapshot)
        reset = chip("Reset video", checkable=False)
        reset.clicked.connect(ctl.reset_video)
        actions.addWidget(shot, 1)
        actions.addWidget(reset, 1)
        if open_effects:
            effects = chip("Effects…", checkable=False)
            effects.setToolTip("Image adjust, equalizer and audio effects (Ctrl+Shift+E)")
            effects.clicked.connect(open_effects)
            actions.addWidget(effects, 1)
        self.lay.addLayout(actions)
        self.pending = QLabel("↻ Applying filter changes…")
        self.pending.setObjectName("ReloadBadge")
        self.pending.setAlignment(Qt.AlignCenter)
        self.pending.setVisible(False)
        ctl.reload_pending.connect(self.pending.setVisible)
        self.lay.addWidget(self.pending)
        self.lay.addStretch(1)

    def refresh(self) -> None:
        v = self.ctl.video
        for buttons, value in ((self.aspect, v.aspect_ratio), (self.crop, v.crop), (self.zoom, v.zoom),
                               (self.rotation, v.rotation), (self.deinterlace, v.deinterlace)):
            if value in buttons:
                buttons[value].setChecked(True)
        fx = self.ctl.effects.video
        self.flip_h.setChecked(fx.flip_h)
        self.flip_v.setChecked(fx.flip_v)
        self.sharpen.setChecked(fx.sharpen)
        self.sharpen.setText("On" if fx.sharpen else "Off")
        self.sharpen_strength.set_value(fx.sharpen_strength)
