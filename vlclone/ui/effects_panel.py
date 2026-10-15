"""Effects & filters panel: equalizer, audio effects and image adjust.

Everything in this panel is live (no reload). Load-time filters such as
flip and sharpen live next to rotate/crop/zoom in Quick settings → Video,
where they are marked "↻ reloads".
"""

from __future__ import annotations

import math
from typing import Callable

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QFrame, QGraphicsDropShadowEffect, QHBoxLayout,
                               QInputDialog, QLabel, QMessageBox, QPushButton, QSlider, QStackedWidget,
                               QVBoxLayout, QWidget)

from ..core.controller import PlayerController
from ..core.effects import (ADJUST_RANGES, EQ_BAND_FREQUENCIES, EQ_MAX_DB, EQ_MIN_DB, NORMALIZE_MAX,
                            NORMALIZE_MIN, STEREO_MODES, format_frequency)
from .icons import icon
from .quick_settings import DelayControl, ValueSlider, badge, chip, chip_grid, section
from .widgets import IconButton, ToggleSwitch

CUSTOM_LABEL = "Custom"


class EqBand(QWidget):
    """One vertical equalizer slider with its gain and frequency labels."""

    def __init__(self, caption: str, on_change: Callable[[float], None]) -> None:
        super().__init__()
        self.on_change = on_change
        self._updating = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.value = QLabel("0.0")
        self.value.setObjectName("ValueLabel")
        self.value.setAlignment(Qt.AlignCenter)
        self.slider = QSlider(Qt.Vertical)
        self.slider.setRange(int(EQ_MIN_DB * 10), int(EQ_MAX_DB * 10))
        self.slider.setSingleStep(5)
        self.slider.setPageStep(30)
        self.slider.setMinimumHeight(150)
        self.slider.setFocusPolicy(Qt.NoFocus)
        self.slider.setToolTip(f"{caption}: drag to boost or cut; double-click resets")
        self.slider.valueChanged.connect(self._moved)
        self.slider.mouseDoubleClickEvent = lambda e: self.on_change(0.0)
        cap = QLabel(caption)
        cap.setObjectName("ValueLabel")
        cap.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.value)
        lay.addWidget(self.slider, 1, Qt.AlignHCenter)
        lay.addWidget(cap)
        self.setMinimumWidth(38)

    def _moved(self, raw: int) -> None:
        self.value.setText(f"{raw / 10:+.1f}" if raw else "0.0")
        if not self._updating:
            self.on_change(raw / 10)

    def set_gain(self, db: float) -> None:
        self._updating = True
        self.slider.setValue(int(round(db * 10)))
        self.value.setText(f"{db:+.1f}" if db else "0.0")
        self._updating = False


class EqualizerPage(QWidget):
    def __init__(self, ctl: PlayerController) -> None:
        super().__init__()
        self.ctl = ctl
        self._updating = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        top = QHBoxLayout()
        top.setSpacing(10)
        self.enabled = ToggleSwitch()
        self.enabled.setToolTip("Equalizer on/off")
        self.enabled.toggled.connect(lambda on: None if self._updating else ctl.set_eq_enabled(on))
        title = QLabel("Equalizer")
        title.setStyleSheet("font-weight: 600;")
        self.presets = QComboBox()
        self.presets.setMinimumWidth(190)
        self.presets.setToolTip("VLC presets and your saved presets")
        self.presets.activated.connect(self._preset_picked)
        self.save = chip("Save…", checkable=False)
        self.save.setToolTip("Save the current curve as a preset")
        self.save.clicked.connect(self._save_preset)
        self.delete = chip("Delete", checkable=False)
        self.delete.clicked.connect(self._delete_preset)
        top.addWidget(self.enabled)
        top.addWidget(title)
        top.addWidget(badge("live"))
        top.addStretch(1)
        top.addWidget(self.presets)
        top.addWidget(self.save)
        top.addWidget(self.delete)
        lay.addLayout(top)

        bands = QHBoxLayout()
        bands.setSpacing(2)
        self.preamp = EqBand("Preamp", ctl.set_eq_preamp)
        bands.addWidget(self.preamp)
        line = QFrame()
        line.setFrameShape(QFrame.VLine)
        line.setStyleSheet("color: #2b303b;")
        bands.addSpacing(6)
        bands.addWidget(line)
        bands.addSpacing(6)
        self.bands = []
        for i, hz in enumerate(EQ_BAND_FREQUENCIES):
            band = EqBand(format_frequency(hz), lambda db, i=i: ctl.set_eq_band(i, db))
            self.bands.append(band)
            bands.addWidget(band)
        lay.addLayout(bands, 1)
        scale = QLabel(f"{EQ_MIN_DB:+.0f} … {EQ_MAX_DB:+.0f} dB · double-click a slider to zero it")
        scale.setObjectName("Muted")
        lay.addWidget(scale)

    def _rebuild_presets(self) -> None:
        current = self.ctl.effects.audio.eq_preset
        builtin = list(self.ctl.builtin_eq_presets())
        custom = self.ctl.custom_eq_presets()
        self.presets.clear()
        if not current:
            self.presets.addItem(CUSTOM_LABEL, None)
        for name in builtin:
            self.presets.addItem(name, name)
        if custom:
            self.presets.insertSeparator(self.presets.count())
            for name in custom:
                self.presets.addItem(icon("save"), name, name)
        index = self.presets.findData(current) if current else 0
        self.presets.setCurrentIndex(max(0, index))
        self.delete.setEnabled(current in custom)

    def _preset_picked(self, index: int) -> None:
        name = self.presets.itemData(index)
        if name:
            self.ctl.apply_eq_preset(name)

    def _save_preset(self) -> None:
        suggested = self.ctl.effects.audio.eq_preset if self.ctl.effects.audio.eq_preset in \
            self.ctl.custom_eq_presets() else ""
        name, ok = QInputDialog.getText(self.window(), "Save equalizer preset", "Preset name:", text=suggested)
        if not ok or not name.strip():
            return
        try:
            self.ctl.save_eq_preset(name)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self.window(), "Save preset", str(exc))

    def _delete_preset(self) -> None:
        name = self.ctl.effects.audio.eq_preset
        if name and QMessageBox.question(self.window(), "Delete preset", f"Delete the preset “{name}”?") \
                == QMessageBox.Yes:
            self.ctl.delete_eq_preset(name)

    def refresh(self) -> None:
        a = self.ctl.effects.audio
        self._updating = True
        self.enabled.setChecked(a.eq_enabled)
        self._updating = False
        self._rebuild_presets()
        self.preamp.set_gain(a.eq_preamp)
        for band, gain in zip(self.bands, a.eq_bands):
            band.set_gain(gain)
        for w in [self.preamp, *self.bands]:
            w.setEnabled(True)
            w.setProperty("dim", not a.eq_enabled)
        self.preamp.setToolTip("" if a.eq_enabled else "Moving a slider switches the equalizer on")


class AudioEffectsPage(QWidget):
    def __init__(self, ctl: PlayerController) -> None:
        super().__init__()
        self.ctl = ctl
        self._updating = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        head = QHBoxLayout()
        head.addWidget(section("Volume normalization"))
        head.addWidget(badge("live"))
        head.addStretch(1)
        self.normalize = ToggleSwitch()
        self.normalize.toggled.connect(lambda on: None if self._updating else ctl.set_normalization(on))
        head.addWidget(self.normalize)
        lay.addLayout(head)
        row = QHBoxLayout()
        lbl = QLabel("Maximum level")
        lbl.setObjectName("Muted")
        self.level = ValueSlider(NORMALIZE_MIN, NORMALIZE_MAX, 10, ctl.set_normalization_level,
                                 lambda v: f"{v:.1f}")
        self.level.setToolTip("Lower values even out loudness more aggressively")
        row.addWidget(lbl)
        row.addWidget(self.level, 1)
        lay.addLayout(row)
        self.boost_note = QLabel()
        self.boost_note.setObjectName("Muted")
        self.boost_note.setWordWrap(True)
        lay.addWidget(self.boost_note)

        head = QHBoxLayout()
        head.addWidget(section("Stereo mode"))
        head.addWidget(badge("live"))
        head.addStretch(1)
        lay.addLayout(head)
        grid, self.stereo = chip_grid([(label, key) for key, label, _ in STEREO_MODES], ctl.set_stereo_mode, 5)
        lay.addWidget(grid)

        head = QHBoxLayout()
        head.addWidget(section("Audio delay"))
        head.addWidget(badge("live"))
        head.addStretch(1)
        lay.addLayout(head)
        self.delay = DelayControl(ctl.set_audio_delay)
        lay.addWidget(self.delay)
        note = QLabel("Same setting as Quick settings → Audio (J / K).")
        note.setObjectName("Muted")
        lay.addWidget(note)
        lay.addStretch(1)
        ctl.volume_changed.connect(lambda *_: self._update_boost_note())
        ctl.delays_changed.connect(lambda a, s: self.delay.set_value(a))

    def _update_boost_note(self) -> None:
        self.boost_note.setText(
            f"Evens out loud and quiet passages. Gain above 100% stays on the main volume slider "
            f"(now {self.ctl.volume}%, up to {self.ctl.max_volume}%).")

    def refresh(self) -> None:
        a = self.ctl.effects.audio
        supported = self.ctl.backend.supports_normalization
        self._updating = True
        self.normalize.setChecked(a.normalize)
        self._updating = False
        self.normalize.setEnabled(supported)
        self.level.setEnabled(supported)
        self.level.set_value(a.normalize_level)
        if not supported:
            self.normalize.setToolTip("Not available with this libVLC version")
        self._update_boost_note()
        self.stereo[a.stereo_mode].setChecked(True)
        self.delay.set_value(self.ctl.audio_delay)


def _gamma_to_slider(gamma: float) -> float:
    return math.log10(max(gamma, 0.01))


class ImagePage(QWidget):
    """Brightness, contrast, hue, saturation, gamma: all live via libVLC's adjust API."""

    LABELS = {"brightness": "Brightness", "contrast": "Contrast", "hue": "Hue", "saturation": "Saturation",
              "gamma": "Gamma"}

    def __init__(self, ctl: PlayerController, open_video_page: Callable[[], None]) -> None:
        super().__init__()
        self.ctl = ctl
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        head = QHBoxLayout()
        head.addWidget(section("Image adjust"))
        head.addWidget(badge("live"))
        head.addStretch(1)
        reset = chip("Reset image", checkable=False)
        reset.clicked.connect(lambda: ctl.reset_adjust())
        head.addWidget(reset)
        lay.addLayout(head)
        self.sliders: dict[str, ValueSlider] = {}
        for name, (lo, hi, neutral) in ADJUST_RANGES.items():
            row = QHBoxLayout()
            row.setSpacing(8)
            label = QLabel(self.LABELS[name])
            label.setMinimumWidth(84)
            if name == "gamma":
                # Logarithmic: 0.1 … 10 with 1.0 in the middle.
                slider = ValueSlider(-1.0, 1.0, 100, lambda x: ctl.set_adjust("gamma", 10 ** x),
                                     lambda x: f"{10 ** x:.2f}")
            elif name == "hue":
                slider = ValueSlider(lo, hi, 1, lambda x: ctl.set_adjust("hue", x), lambda x: f"{x:+.0f}°")
            else:
                slider = ValueSlider(lo, hi, 100, lambda x, n=name: ctl.set_adjust(n, x),
                                     lambda x: f"{x * 100:.0f}%")
            self.sliders[name] = slider
            undo = IconButton("close", f"Reset {self.LABELS[name].lower()}", 26, 12)
            undo.clicked.connect(lambda _=False, n=name: ctl.reset_adjust(n))
            row.addWidget(label)
            row.addWidget(slider, 1)
            row.addWidget(undo)
            lay.addLayout(row)
        more = QHBoxLayout()
        note = QLabel("Rotate, flip, sharpen, crop, zoom and deinterlace are in Quick settings → Video.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        go = chip("Open", checkable=False)
        go.clicked.connect(open_video_page)
        more.addWidget(note, 1)
        more.addWidget(go)
        lay.addSpacing(6)
        lay.addLayout(more)
        lay.addStretch(1)

    def refresh(self) -> None:
        v = self.ctl.effects.video
        for name, slider in self.sliders.items():
            value = getattr(v, name)
            slider.set_value(_gamma_to_slider(value) if name == "gamma" else value)


class EffectsPanel(QWidget):
    """Floating panel above the video; stays open while you watch."""

    closed = Signal()

    def __init__(self, ctl: PlayerController, open_video_page: Callable[[], None],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.ctl = ctl
        self._drag_from: QPoint | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)
        self.frame = QFrame()
        self.frame.setObjectName("Popover")
        shadow = QGraphicsDropShadowEffect(self.frame, blurRadius=36, offset=QPoint(0, 8))
        shadow.setColor(QColor(0, 0, 0, 150))
        self.frame.setGraphicsEffect(shadow)
        outer.addWidget(self.frame)
        lay = QVBoxLayout(self.frame)
        lay.setContentsMargins(16, 12, 12, 16)
        lay.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(4)
        self.title = QLabel("Effects & filters")
        self.title.setStyleSheet("font-size: 12pt; font-weight: 600;")
        self.title.setCursor(Qt.SizeAllCursor)
        self.title.setToolTip("Drag to move")
        header.addWidget(self.title)
        header.addSpacing(12)
        self.tab_group = QButtonGroup(self)
        self.stack = QStackedWidget()
        self.pages = {
            "equalizer": EqualizerPage(ctl),
            "audio": AudioEffectsPage(ctl),
            "image": ImagePage(ctl, open_video_page),
        }
        for i, (key, title) in enumerate((("equalizer", "Equalizer"), ("audio", "Audio"), ("image", "Image"))):
            b = QPushButton(title)
            b.setObjectName("Tab")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setFocusPolicy(Qt.NoFocus)
            self.tab_group.addButton(b, i)
            header.addWidget(b)
            self.stack.addWidget(self.pages[key])
        self.tab_group.button(0).setChecked(True)
        self.tab_group.idClicked.connect(self._show_index)
        header.addStretch(1)
        reset = chip("Reset all", checkable=False)
        reset.setToolTip("Equalizer, normalization, stereo mode, image adjust, flip and sharpen")
        reset.clicked.connect(ctl.reset_all_effects)
        close = IconButton("close", "Close (Esc)", 30, 16)
        close.clicked.connect(self.hide)
        header.addWidget(reset)
        header.addWidget(close)
        lay.addLayout(header)
        lay.addWidget(self.stack, 1)
        self.setFixedWidth(640)
        for sig in (ctl.effects_changed, ctl.media_changed):
            sig.connect(lambda *_: self.refresh() if self.isVisible() else None)

    def _show_index(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.stack.currentWidget().refresh()

    def show_page(self, key: str) -> None:
        index = list(self.pages).index(key)
        self.tab_group.button(index).setChecked(True)
        self._show_index(index)

    def refresh(self) -> None:
        self.stack.currentWidget().refresh()

    def popup(self, anchor: QRect, page: str | None = None) -> None:
        if page:
            self.show_page(page)
        self.refresh()
        self.adjustSize()
        screen = QGuiApplication.screenAt(anchor.center()) or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()
        x = max(avail.left(), min(anchor.right() - self.width() + 18, avail.right() - self.width()))
        y = max(avail.top(), anchor.top() - self.height() + 10)
        self.move(x, y)
        self.show()
        self.raise_()

    # drag the panel by its title
    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton and self.title.geometry().contains(
                self.title.parentWidget().mapFrom(self, e.position().toPoint())):
            self._drag_from = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e) -> None:
        if self._drag_from is not None:
            self.move(e.globalPosition().toPoint() - self._drag_from)

    def mouseReleaseEvent(self, e) -> None:
        self._drag_from = None

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(e)

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        self.closed.emit()
