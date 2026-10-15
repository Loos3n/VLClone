"""The overlay window that floats above the native video surface.

libVLC renders into a native child window, which Qt cannot draw on top
of. So the controls live in a frameless, translucent tool window that
tracks the video area. Its background is almost (not fully) transparent
so it also receives mouse input over the video: clicks, double-clicks,
wheel, drag-and-drop and cursor movement for auto-hide.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QCursor, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QSizePolicy, QStackedWidget,
                               QVBoxLayout, QWidget)

from ..core.abloop import ABState
from ..core.backend import PlaybackState
from ..core.playlist import RepeatMode
from ..core.timefmt import format_ms
from . import theme
from .icons import icon
from .widgets import Fader, FadingLabel, IconButton, SeekSlider, VolumeSlider


class WelcomeCard(QWidget):
    def __init__(self, trigger: Callable[[str], None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        lay.setSpacing(14)
        logo = QLabel()
        logo.setPixmap(icon("cone").pixmap(72, 72))
        logo.setAlignment(Qt.AlignCenter)
        title = QLabel("Drop media here")
        title.setObjectName("Heading")
        title.setAlignment(Qt.AlignCenter)
        hint = QLabel("Files, folders, playlists or links — or use the buttons below")
        hint.setObjectName("Muted")
        hint.setAlignment(Qt.AlignCenter)
        row = QHBoxLayout()
        row.setSpacing(10)
        row.setAlignment(Qt.AlignCenter)
        for label, icon_name, action, primary in (
            ("Open file", "file", "open_file", True),
            ("Open folder", "folder", "open_folder", False),
            ("Open URL", "link", "open_url", False),
            ("Library", "library", "media_library", False),
        ):
            b = QPushButton(icon(icon_name, "#1a0f05" if primary else None), f"  {label}")
            if primary:
                b.setObjectName("Primary")
            b.setCursor(Qt.PointingHandCursor)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(lambda _=False, a=action: trigger(a))
            row.addWidget(b)
        keys = QLabel("Ctrl+O  ·  Ctrl+F  ·  Ctrl+N  ·  Ctrl+Shift+L   —   discs and webcams: Ctrl+Shift+O")
        keys.setObjectName("Muted")
        keys.setAlignment(Qt.AlignCenter)
        lay.addWidget(logo)
        lay.addWidget(title)
        lay.addWidget(hint)
        lay.addSpacing(6)
        lay.addLayout(row)
        lay.addWidget(keys)


class AudioCard(QWidget):
    """Shown for audio-only media, where the video surface stays empty."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        lay.setSpacing(10)
        art = QLabel()
        art.setPixmap(icon("cone").pixmap(120, 120))
        art.setAlignment(Qt.AlignCenter)
        self.title = QLabel()
        self.title.setObjectName("Heading")
        self.title.setAlignment(Qt.AlignCenter)
        self.subtitle = QLabel()
        self.subtitle.setObjectName("Muted")
        self.subtitle.setAlignment(Qt.AlignCenter)
        lay.addWidget(art)
        lay.addWidget(self.title)
        lay.addWidget(self.subtitle)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)


class Toast(QWidget):
    """Small actionable notification, e.g. "Resumed at 12:34 · Start over"."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setAttribute(Qt.WA_StyledBackground)
        self.setStyleSheet(f"#Toast {{ background: {theme.OVERLAY_BG}; border: 1px solid {theme.BORDER};"
                           " border-radius: 12px; }")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 6, 6, 6)
        lay.setSpacing(8)
        self.label = QLabel()
        self.button = QPushButton()
        self.button.setObjectName("Chip")
        self.button.setCursor(Qt.PointingHandCursor)
        self.button.setFocusPolicy(Qt.NoFocus)
        close = IconButton("close", "Dismiss", size=26, icon_size=14)
        close.clicked.connect(self.dismiss)
        lay.addWidget(self.label)
        lay.addWidget(self.button)
        lay.addWidget(close)
        self._fader = Fader(self, 200)
        self._timer = QTimer(self, singleShot=True)
        self._timer.timeout.connect(self.dismiss)
        self._callback: Callable[[], None] | None = None
        self.button.clicked.connect(self._clicked)
        self.hide()

    def show_message(self, text: str, button: str, callback: Callable[[], None], ms: int = 7000) -> None:
        self.label.setText(text)
        self.button.setText(button)
        self._callback = callback
        self.adjustSize()
        self._fader.effect.setOpacity(0.0)
        self._fader.fade_to(1.0)
        self.raise_()
        self._timer.start(ms)

    def _clicked(self) -> None:
        if self._callback:
            self._callback()
        self.dismiss()

    def dismiss(self) -> None:
        self._timer.stop()
        if self.isVisible():
            self._fader.fade_to(0.0)


class ControlsPanel(QWidget):
    """Seek row + transport/tool buttons."""

    def __init__(self, trigger: Callable[[str], None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.trigger = trigger
        self._show_remaining = False
        self._length = -1
        self._time = 0
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 0, 18, 14)
        outer.setSpacing(0)

        seek_row = QHBoxLayout()
        seek_row.setSpacing(10)
        self.time_label = QLabel("0:00")
        self.length_label = QPushButton("--:--")
        self.length_label.setFlat(True)
        self.length_label.setCursor(Qt.PointingHandCursor)
        self.length_label.setFocusPolicy(Qt.NoFocus)
        self.length_label.setToolTip("Click to toggle remaining time")
        self.length_label.setStyleSheet("QPushButton { background: transparent; border: none; padding: 0;"
                                        " min-height: 0; }")
        self.length_label.clicked.connect(self._toggle_remaining)
        for lbl in (self.time_label, self.length_label):
            lbl.setFont(theme.app_font(10))
            lbl.setMinimumWidth(56)
        self.time_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.seek = SeekSlider()
        seek_row.addWidget(self.time_label, 0, Qt.AlignBottom)
        seek_row.addWidget(self.seek, 1)
        seek_row.addWidget(self.length_label, 0, Qt.AlignBottom)
        outer.addLayout(seek_row)

        row = QHBoxLayout()
        row.setSpacing(2)
        self.buttons: dict[str, IconButton] = {}

        def add(name: str, icon_name: str, tip: str, size: int = 38, isz: int = 22) -> IconButton:
            b = IconButton(icon_name, tip, size, isz)
            b.clicked.connect(lambda _=False, a=name: self.trigger(a))
            self.buttons[name] = b
            row.addWidget(b)
            return b

        add("play_pause", "play", "Play / pause (Space)", 46, 28)
        add("stop", "stop", "Stop (S)")
        add("previous", "previous", "Previous (P)")
        add("next", "next", "Next (N)")
        add("frame_step", "frame", "Next frame (E)", 34, 18)
        row.addSpacing(8)
        add("mute", "volume-high", "Mute (M)", 34, 20)
        self.volume = VolumeSlider()
        row.addWidget(self.volume)
        self.volume_label = QLabel("100%")
        self.volume_label.setObjectName("Muted")
        self.volume_label.setMinimumWidth(40)
        row.addWidget(self.volume_label)
        row.addSpacing(6)
        self.speed_chip = QPushButton("1×")
        self.speed_chip.setObjectName("Chip")
        self.speed_chip.setCursor(Qt.PointingHandCursor)
        self.speed_chip.setFocusPolicy(Qt.NoFocus)
        self.speed_chip.setToolTip("Playback speed")
        self.speed_chip.clicked.connect(lambda: self.trigger("quick_settings_speed"))
        row.addWidget(self.speed_chip)
        self.chapter_chip = QPushButton()
        self.chapter_chip.setObjectName("Chip")
        self.chapter_chip.setCursor(Qt.PointingHandCursor)
        self.chapter_chip.setFocusPolicy(Qt.NoFocus)
        self.chapter_chip.setToolTip("Chapters (Shift+P / Shift+N)")
        self.chapter_chip.clicked.connect(lambda: self.trigger("quick_settings_chapters"))
        self.chapter_chip.hide()
        row.addWidget(self.chapter_chip)
        row.addStretch(1)
        add("ab_loop", "ab", "A-B loop (Shift+L)", 38, 24)
        add("cycle_repeat", "repeat", "Repeat (L)", 34, 20)
        add("toggle_shuffle", "shuffle", "Shuffle (R)", 34, 20)
        row.addSpacing(6)
        add("quick_settings", "sliders", "Tracks, speed & video (Ctrl+E)", 36, 20)
        add("toggle_playlist", "playlist", "Playlist (Ctrl+L)", 36, 20)
        add("fullscreen", "fullscreen", "Fullscreen (F)", 36, 20)
        add("main_menu", "more", "More", 36, 20)
        outer.addLayout(row)

    def _toggle_remaining(self) -> None:
        self._show_remaining = not self._show_remaining
        self.set_time(self._time, self._length)

    def set_time(self, ms: int, length: int) -> None:
        self._time, self._length = ms, length
        hours = length >= 3_600_000
        self.time_label.setText(format_ms(max(0, ms), hours) if length != -1 or ms > 0 else "0:00")
        if self._show_remaining and length > 0:
            self.length_label.setText("-" + format_ms(max(0, length - ms), hours))
        else:
            self.length_label.setText(format_ms(length, hours) if length > 0 else "--:--")
        self.seek.set_length(length)
        self.seek.set_position(ms)


class OverlayWindow(QWidget):
    clicked = Signal()
    pressed = Signal()
    double_clicked = Signal()
    wheel = Signal(int)
    context_requested = Signal(QPoint)
    files_dropped = Signal(list)

    def __init__(self, host: QWidget, trigger: Callable[[str], None], hide_delay_ms: int = 2200) -> None:
        flags = (Qt.Tool | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus
                 | Qt.NoDropShadowWindowHint)
        super().__init__(host, flags)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.trigger = trigger
        self.hide_delay_ms = hide_delay_ms
        self._controls_visible = True
        self._pinned_reasons: set[str] = set()
        self._playing = False
        self._drag_active = False
        self._last_cursor = QCursor.pos()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.top_bar = QWidget()
        top = QHBoxLayout(self.top_bar)
        top.setContentsMargins(22, 14, 22, 8)
        self.title = QLabel()
        tf = theme.app_font(12)
        tf.setWeight(tf.Weight.DemiBold)
        self.title.setFont(tf)
        self.subtitle = QLabel()
        self.subtitle.setObjectName("Muted")
        top.addWidget(self.title)
        top.addSpacing(10)
        top.addWidget(self.subtitle)
        top.addStretch(1)
        root.addWidget(self.top_bar)

        self.center = QStackedWidget()
        self.center.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.empty_page = QWidget()
        self.empty_page.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.welcome = WelcomeCard(trigger)
        self.audio_card = AudioCard()
        for w in (self.empty_page, self.welcome, self.audio_card):
            self.center.addWidget(w)
        root.addWidget(self.center, 1)

        self.controls = ControlsPanel(trigger)
        root.addWidget(self.controls)

        self.osd = FadingLabel(self)
        self.toast = Toast(self)

        self._top_fader = Fader(self.top_bar)
        self._controls_fader = Fader(self.controls)
        self._controls_fader.anim.valueChanged.connect(lambda _: self.update())

        self._hide_timer = QTimer(self, singleShot=True)
        self._hide_timer.timeout.connect(self._auto_hide)
        self._cursor_timer = QTimer(self, interval=120)
        self._cursor_timer.timeout.connect(self._poll_cursor)
        self._cursor_timer.start()
        self._click_timer = QTimer(self, singleShot=True)
        self._click_timer.timeout.connect(self.clicked.emit)
        self.show_welcome(True)

    # ------------------------------------------------------------ visibility
    def pin(self, reason: str, on: bool) -> None:
        """Keep controls visible while ``reason`` holds (popover open, ...)."""
        if on:
            self._pinned_reasons.add(reason)
            self.reveal()
        else:
            self._pinned_reasons.discard(reason)
            self._restart_hide_timer()

    def set_playing(self, playing: bool) -> None:
        self._playing = playing
        if not playing:
            self.reveal()
        else:
            self._restart_hide_timer()

    def _should_stay_visible(self) -> bool:
        if not self._playing or self._pinned_reasons or self.controls.seek.is_dragging:
            return True
        pos = self.mapFromGlobal(QCursor.pos())
        return self.controls.isVisible() and self.controls.geometry().contains(pos)

    def reveal(self) -> None:
        self.unsetCursor()
        if not self._controls_visible:
            self._controls_visible = True
            self._top_fader.fade_to(1.0)
            self._controls_fader.fade_to(1.0)
        self._restart_hide_timer()

    def _restart_hide_timer(self) -> None:
        self._hide_timer.start(self.hide_delay_ms)

    def _auto_hide(self) -> None:
        if self._should_stay_visible():
            self._restart_hide_timer()
            return
        if self._controls_visible:
            self._controls_visible = False
            self._top_fader.fade_to(0.0)
            self._controls_fader.fade_to(0.0)
        if self.rect().contains(self.mapFromGlobal(QCursor.pos())):
            self.setCursor(Qt.BlankCursor)

    def _poll_cursor(self) -> None:
        pos = QCursor.pos()
        if pos != self._last_cursor:
            self._last_cursor = pos
            if self.isVisible() and self.geometry().contains(pos):
                self.reveal()

    @property
    def controls_visible(self) -> bool:
        return self._controls_visible

    # ---------------------------------------------------------------- content
    def show_welcome(self, on: bool) -> None:
        self.center.setCurrentWidget(self.welcome if on else self.empty_page)
        self.update()

    def show_audio_card(self, title: str, subtitle: str = "") -> None:
        self.audio_card.title.setText(title)
        self.audio_card.subtitle.setText(subtitle)
        self.center.setCurrentWidget(self.audio_card)
        self.update()

    def set_title(self, title: str, subtitle: str = "") -> None:
        fm = self.title.fontMetrics()
        self.title.setText(fm.elidedText(title, Qt.ElideMiddle, max(200, self.width() - 300)))
        self.title.setToolTip(title)
        self.subtitle.setText(subtitle)

    def flash(self, text: str) -> None:
        self.osd.flash(text)
        self._place_floating()

    def update_state(self, state: PlaybackState) -> None:
        playing = state is PlaybackState.PLAYING
        self.controls.buttons["play_pause"].set_icon_name("pause" if playing else "play")
        self.set_playing(playing or state in (PlaybackState.OPENING, PlaybackState.BUFFERING))

    def update_volume(self, volume: int, muted: bool) -> None:
        self.controls.volume.set_value(volume, muted)
        self.controls.volume_label.setText("Muted" if muted else f"{volume}%")
        name = "volume-muted" if muted or volume == 0 else ("volume-low" if volume < 50 else "volume-high")
        self.controls.buttons["mute"].set_icon_name(name)

    def update_rate(self, rate: float) -> None:
        self.controls.speed_chip.setText(f"{rate:g}×")

    def update_repeat(self, mode: RepeatMode) -> None:
        b = self.controls.buttons["cycle_repeat"]
        b.set_icon_name("repeat-one" if mode is RepeatMode.ONE else "repeat")
        b.set_accent(mode is not RepeatMode.NONE)
        b.setToolTip({RepeatMode.NONE: "Repeat: off (L)", RepeatMode.ALL: "Repeat: all (L)",
                      RepeatMode.ONE: "Repeat: one (L)"}[mode])

    def update_shuffle(self, on: bool) -> None:
        self.controls.buttons["toggle_shuffle"].set_accent(on)

    def update_ab(self, state: ABState, a, b) -> None:
        self.controls.buttons["ab_loop"].set_accent(state is not ABState.OFF)
        self.controls.seek.set_ab(a, b)

    def update_chapters(self, chapters, current: int, in_menu: bool = False) -> None:
        """Chapter marks on the timeline and the "Ch 2/12" chip (hidden without chapters)."""
        self.controls.seek.set_chapters([(c.start_ms, c.title) for c in chapters] if len(chapters) > 1 else [])
        chip = self.controls.chapter_chip
        if in_menu:
            chip.setText("Disc menu")
            chip.show()
        elif len(chapters) > 1 and current >= 0:
            chip.setText(f"Ch {current + 1}/{len(chapters)}")
            chip.setToolTip(f"{chapters[current].title} — chapters (Shift+P / Shift+N)")
            chip.show()
        else:
            chip.hide()

    def update_fullscreen(self, on: bool) -> None:
        self.controls.buttons["fullscreen"].set_icon_name("fullscreen-exit" if on else "fullscreen")

    def anchor(self, name: str) -> QRect:
        """Global rectangle of a control, for anchoring popovers."""
        if name == "speed":
            w = self.controls.speed_chip
        elif name == "chapters" and self.controls.chapter_chip.isVisible():
            w = self.controls.chapter_chip
        else:
            w = self.controls.buttons.get(name, self.controls)
        return QRect(w.mapToGlobal(QPoint(0, 0)), w.size())

    # ---------------------------------------------------------------- layout
    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._place_floating()

    def _place_floating(self) -> None:
        self.osd.adjustSize()
        self.osd.move(self.width() - self.osd.width() - 24, 56)
        self.toast.adjustSize()
        bottom = self.height() - self.controls.sizeHint().height() - self.toast.height() - 12
        self.toast.move(24, max(60, bottom))

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        # Alpha 1 keeps the window hit-testable over the video without being visible.
        p.fillRect(self.rect(), QColor(0, 0, 0, 1))
        k = self._controls_fader.opacity if self.controls.isVisible() else 0.0
        if k > 0:
            h = self.controls.height() + 50
            g = QLinearGradient(0, self.height() - h, 0, self.height())
            g.setColorAt(0.0, QColor(0, 0, 0, 0))
            g.setColorAt(1.0, QColor(0, 0, 0, int(200 * k)))
            p.fillRect(QRectF(0, self.height() - h, self.width(), h), g)
            th = self.top_bar.height() + 30
            g2 = QLinearGradient(0, 0, 0, th)
            g2.setColorAt(0.0, QColor(0, 0, 0, int(150 * k)))
            g2.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.fillRect(QRectF(0, 0, self.width(), th), g2)
        if self._drag_active:
            pen = QPen(QColor(theme.ACCENT), 3, Qt.DashLine)
            p.setPen(pen)
            p.setBrush(QColor(255, 138, 42, 28))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(12, 12, -12, -12), 16, 16)

    # ----------------------------------------------------------------- input
    def mouseMoveEvent(self, e) -> None:
        self.reveal()

    def mousePressEvent(self, e) -> None:
        self.pressed.emit()
        if e.button() == Qt.LeftButton:
            self._click_timer.start(250)

    def mouseDoubleClickEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self._click_timer.stop()
            self.double_clicked.emit()

    def contextMenuEvent(self, e) -> None:
        self.context_requested.emit(e.globalPos())

    def wheelEvent(self, e) -> None:
        delta = e.angleDelta().y()
        if delta:
            self.wheel.emit(1 if delta > 0 else -1)

    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasUrls() or e.mimeData().hasText():
            e.acceptProposedAction()
            self._drag_active = True
            self.update()

    def dragLeaveEvent(self, e) -> None:
        self._drag_active = False
        self.update()

    def dropEvent(self, e) -> None:
        self._drag_active = False
        self.update()
        self.files_dropped.emit(mime_to_inputs(e.mimeData()))
        e.acceptProposedAction()


def mime_to_inputs(mime) -> list[str]:
    items: list[str] = []
    if mime.hasUrls():
        for url in mime.urls():
            items.append(url.toLocalFile() if url.isLocalFile() else url.toString())
    elif mime.hasText():
        for line in mime.text().splitlines():
            line = line.strip()
            if line:
                url = QUrl(line)
                items.append(url.toLocalFile() if url.isLocalFile() else line)
    return items
