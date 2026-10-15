"""Quick-settings popover: speed, loop, tracks and video in one place.

Pages are small widgets with a ``refresh()`` method; new feature areas
(equalizer, effects, ...) plug in with :meth:`QuickSettings.add_page`.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRect, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (QButtonGroup, QFrame, QGraphicsDropShadowEffect, QGridLayout,
                               QHBoxLayout, QLabel, QPushButton, QSlider, QStackedWidget,
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
        self.setFixedWidth(430)

        self._anim = QPropertyAnimation(self, b"windowOpacity", self, duration=160)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._slide = QPropertyAnimation(self, b"pos", self, duration=180)
        self._slide.setEasingCurve(QEasingCurve.OutCubic)

        self.add_page("playback", "Playback", PlaybackPage(ctl))
        for sig in (ctl.rate_changed, ctl.ab_changed, ctl.repeat_changed, ctl.shuffle_changed,
                    ctl.tracks_changed, ctl.delays_changed, ctl.video_changed, ctl.media_changed):
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

    def show_page(self, key: str) -> None:
        page = self.pages.get(key)
        if page is None:
            return
        page.property("tab_button").setChecked(True)
        self.stack.setCurrentWidget(page)
        page.refresh()
        self.adjustSize()

    def _refresh_if_visible(self) -> None:
        if self.isVisible():
            self.stack.currentWidget().refresh()

    def popup(self, anchor: QRect, page: str | None = None) -> None:
        if page:
            self.show_page(page)
        else:
            self.stack.currentWidget().refresh()
        self.adjustSize()
        screen = QGuiApplication.screenAt(anchor.center()) or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()
        x = anchor.right() - self.width() + 18
        y = anchor.top() - self.height() + 10
        x = max(avail.left(), min(x, avail.right() - self.width()))
        y = max(avail.top(), y)
        self.setWindowOpacity(0.0)
        self.move(x, y + 12)
        self.show()
        self._anim.stop()
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()
        self._slide.stop()
        self._slide.setStartValue(QPoint(x, y + 12))
        self._slide.setEndValue(QPoint(x, y))
        self._slide.start()

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        self.closed.emit()
