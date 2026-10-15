"""Reusable painted widgets: icon buttons, seek bar, volume slider, OSD pill."""

from __future__ import annotations

from PySide6.QtCore import (QEasingCurve, QPointF, QPropertyAnimation, QRectF, QSize,
                            Qt, QTimer, QVariantAnimation, Signal)
from PySide6.QtGui import QColor, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QCheckBox, QGraphicsOpacityEffect, QLabel, QSizePolicy, QToolButton, QWidget

from ..core.timefmt import format_ms
from . import theme
from .icons import icon


class IconButton(QToolButton):
    """Flat round button with a painted icon and a soft hover highlight."""

    def __init__(self, icon_name: str, tooltip: str = "", size: int = 36, icon_size: int = 22,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._icon_name = icon_name
        self._hover = 0.0
        self._accent = False
        self.setIcon(icon(icon_name))
        self.setIconSize(QSize(icon_size, icon_size))
        self.setFixedSize(size, size)
        self.setToolTip(tooltip)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAutoRaise(True)
        self.setStyleSheet("QToolButton { border: none; background: transparent; }")
        self._anim = QVariantAnimation(self, duration=140, easingCurve=QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._set_hover)

    def set_icon_name(self, name: str) -> None:
        if name != self._icon_name:
            self._icon_name = name
            self.setIcon(icon(name, theme.ACCENT if self._accent else None))

    def set_accent(self, on: bool) -> None:
        """Highlight the icon (used for active toggles like repeat or A-B)."""
        if on != self._accent:
            self._accent = on
            self.setIcon(icon(self._icon_name, theme.ACCENT if on else None))

    def _set_hover(self, v) -> None:
        self._hover = float(v)
        self.update()

    def _animate(self, target: float) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._hover)
        self._anim.setEndValue(target)
        self._anim.start()

    def enterEvent(self, e) -> None:
        self._animate(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        self._animate(0.0)
        super().leaveEvent(e)

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self._hover > 0 or self.isDown() or self._accent:
            bg = QColor(255, 255, 255, int(26 * self._hover + (14 if self.isDown() else 0)))
            if self._accent:
                bg = QColor(theme.ACCENT)
                bg.setAlpha(int(34 + 20 * self._hover))
            p.setPen(Qt.NoPen)
            p.setBrush(bg)
            p.drawEllipse(QRectF(self.rect()).adjusted(1, 1, -1, -1))
        mode = self.icon().Mode.Normal if self.isEnabled() else self.icon().Mode.Disabled
        s = self.iconSize()
        x = (self.width() - s.width()) / 2
        y = (self.height() - s.height()) / 2
        self.icon().paint(p, int(x), int(y), s.width(), s.height(), Qt.AlignCenter, mode)


class SeekSlider(QWidget):
    """Timeline with hover time preview, drag-to-seek, A-B and chapter markers."""

    seek_requested = Signal(int)  # ms

    BUBBLE_SPACE = 30
    TRACK_ZONE = 18

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setMinimumWidth(120)
        self.setFixedHeight(self.BUBBLE_SPACE + self.TRACK_ZONE)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._length = -1
        self._position = 0
        self._hover_x: float | None = None
        self._dragging = False
        self._drag_ms = 0
        self._ab: tuple[int | None, int | None] = (None, None)
        self._chapters: list[tuple[int, str]] = []  # (start ms, title)
        self._grow = 0.0
        self._anim = QVariantAnimation(self, duration=150, easingCurve=QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._set_grow)
        self._drag_timer = QTimer(self, singleShot=True, interval=60)
        self._drag_timer.timeout.connect(lambda: self.seek_requested.emit(self._drag_ms))

    # ------------------------------------------------------------ public api
    def set_length(self, ms: int) -> None:
        if ms != self._length:
            self._length = ms
            self.update()

    def set_position(self, ms: int) -> None:
        if not self._dragging and ms != self._position:
            self._position = ms
            self.update()

    def set_ab(self, a: int | None, b: int | None) -> None:
        self._ab = (a, b)
        self.update()

    def set_chapters(self, chapters: list[tuple[int, str]]) -> None:
        chapters = sorted(chapters)
        if chapters != self._chapters:
            self._chapters = chapters
            self.update()

    def chapter_at(self, ms: int) -> str:
        title = ""
        for start, name in self._chapters:
            if start <= ms:
                title = name
        return title

    @property
    def is_dragging(self) -> bool:
        return self._dragging

    # --------------------------------------------------------------- geometry
    def _track_rect(self) -> QRectF:
        h = 4 + 2 * self._grow
        cy = self.BUBBLE_SPACE + self.TRACK_ZONE / 2
        return QRectF(8, cy - h / 2, self.width() - 16, h)

    def _x_to_ms(self, x: float) -> int:
        r = self._track_rect()
        frac = min(max((x - r.left()) / max(1.0, r.width()), 0.0), 1.0)
        return int(frac * max(0, self._length))

    def _ms_to_x(self, ms: int) -> float:
        r = self._track_rect()
        if self._length <= 0:
            return r.left()
        return r.left() + r.width() * min(max(ms / self._length, 0.0), 1.0)

    def _set_grow(self, v) -> None:
        self._grow = float(v)
        self.update()

    def _animate(self, target: float) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._grow)
        self._anim.setEndValue(target)
        self._anim.start()

    # ----------------------------------------------------------------- events
    def enterEvent(self, e) -> None:
        self._animate(1.0)

    def leaveEvent(self, e) -> None:
        self._hover_x = None
        if not self._dragging:
            self._animate(0.0)
        self.update()

    def mouseMoveEvent(self, e) -> None:
        self._hover_x = e.position().x()
        if self._dragging:
            self._drag_ms = self._x_to_ms(self._hover_x)
            self._position = self._drag_ms
            if not self._drag_timer.isActive():
                self._drag_timer.start()
        self.update()

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton and self._length > 0:
            self._dragging = True
            self._drag_ms = self._x_to_ms(e.position().x())
            self._position = self._drag_ms
            self.seek_requested.emit(self._drag_ms)
            self.update()

    def mouseReleaseEvent(self, e) -> None:
        if self._dragging and e.button() == Qt.LeftButton:
            self._dragging = False
            self._drag_timer.stop()
            self.seek_requested.emit(self._drag_ms)
            if not self.underMouse():
                self._animate(0.0)
            self.update()

    def wheelEvent(self, e) -> None:
        e.ignore()  # let the overlay map wheel to volume

    # ------------------------------------------------------------------ paint
    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self._track_rect()
        radius = r.height() / 2
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 46))
        p.drawRoundedRect(r, radius, radius)

        a, b = self._ab
        if a is not None and self._length > 0:
            xa = self._ms_to_x(a)
            xb = self._ms_to_x(b) if b is not None else xa
            band = QColor(theme.ACCENT)
            band.setAlpha(70)
            p.setBrush(band)
            p.drawRoundedRect(QRectF(xa, r.top() - 3, max(2.0, xb - xa), r.height() + 6), 2, 2)
            p.setBrush(QColor(theme.ACCENT))
            for x in ([xa, xb] if b is not None else [xa]):
                p.drawRoundedRect(QRectF(x - 1.25, r.top() - 5, 2.5, r.height() + 10), 1, 1)

        if self._hover_x is not None and self._length > 0 and not self._dragging:
            hx = min(max(self._hover_x, r.left()), r.right())
            p.setBrush(QColor(255, 255, 255, 40))
            p.drawRoundedRect(QRectF(r.left(), r.top(), hx - r.left(), r.height()), radius, radius)

        if self._length > 0:
            px = self._ms_to_x(self._position)
            p.setBrush(QColor(theme.ACCENT))
            p.drawRoundedRect(QRectF(r.left(), r.top(), px - r.left(), r.height()), radius, radius)
            self._paint_chapter_marks(p, r)
            knob = 3 + 4 * self._grow
            if knob > 3.2:
                p.setBrush(QColor("#ffffff"))
                p.drawEllipse(QPointF(px, r.center().y()), knob, knob)

        if self._hover_x is not None and self._length > 0:
            ms = self._drag_ms if self._dragging else self._x_to_ms(self._hover_x)
            chapter = self.chapter_at(ms)
            chapter = chapter if len(chapter) <= 40 else chapter[:39] + "…"
            self._paint_bubble(p, self._ms_to_x(ms), f"{format_ms(ms)}  ·  {chapter}" if chapter else format_ms(ms))

    def _paint_chapter_marks(self, p: QPainter, r: QRectF) -> None:
        """Small gaps in the track at each chapter start (like a segmented timeline)."""
        if len(self._chapters) < 2:
            return
        p.setBrush(QColor(0, 0, 0, 170))
        for start, _ in self._chapters:
            if 0 < start < self._length:
                x = self._ms_to_x(start)
                p.drawRect(QRectF(x - 1.0, r.top() - 1, 2.0, r.height() + 2))

    def _paint_bubble(self, p: QPainter, x: float, text: str) -> None:
        font = theme.app_font(9.5)
        font.setWeight(font.Weight.DemiBold)
        p.setFont(font)
        fm = QFontMetricsF(font)
        w = fm.horizontalAdvance(text) + 16
        h = fm.height() + 8
        left = min(max(x - w / 2, 0.0), self.width() - w)
        rect = QRectF(left, self.BUBBLE_SPACE - h - 4, w, h)
        path = QPainterPath()
        path.addRoundedRect(rect, 6, 6)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.SURFACE_3))
        p.drawPath(path)
        p.setPen(QColor(theme.TEXT))
        p.drawText(rect, Qt.AlignCenter, text)


class VolumeSlider(QWidget):
    """0–max volume with a marked 100% point; the boost zone is tinted."""

    volume_requested = Signal(int)

    def __init__(self, maximum: int = 200, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._max = maximum
        self._value = 100
        self._muted = False
        self._dragging = False
        self.setFixedSize(108, 36)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setToolTip("Volume (scroll to adjust, above 100% boosts)")

    def set_maximum(self, maximum: int) -> None:
        self._max = max(100, maximum)
        self.update()

    def set_value(self, value: int, muted: bool = False) -> None:
        self._value = value
        self._muted = muted
        self.update()

    def _track(self) -> QRectF:
        return QRectF(6, self.height() / 2 - 2, self.width() - 12, 4)

    def _value_at(self, x: float) -> int:
        r = self._track()
        frac = min(max((x - r.left()) / r.width(), 0.0), 1.0)
        v = int(round(frac * self._max))
        if abs(v - 100) <= 3:
            v = 100  # gentle snap at unity gain
        return v

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self._dragging = True
            self.volume_requested.emit(self._value_at(e.position().x()))

    def mouseMoveEvent(self, e) -> None:
        if self._dragging:
            self.volume_requested.emit(self._value_at(e.position().x()))

    def mouseReleaseEvent(self, e) -> None:
        self._dragging = False

    def wheelEvent(self, e) -> None:
        e.ignore()

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self._track()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 46))
        p.drawRoundedRect(r, 2, 2)
        unity_x = r.left() + r.width() * 100 / self._max
        frac = min(self._value / self._max, 1.0)
        fill_x = r.left() + r.width() * frac
        normal = QColor(theme.TEXT_MUTED if self._muted else theme.TEXT)
        p.setBrush(normal)
        p.drawRoundedRect(QRectF(r.left(), r.top(), min(fill_x, unity_x) - r.left(), r.height()), 2, 2)
        if fill_x > unity_x:
            boost = QColor(theme.ACCENT if not self._muted else theme.TEXT_MUTED)
            p.setBrush(boost)
            p.drawRoundedRect(QRectF(unity_x, r.top(), fill_x - unity_x, r.height()), 2, 2)
        if self._max > 100:
            p.setBrush(QColor(255, 255, 255, 120))
            p.drawRect(QRectF(unity_x - 0.75, r.top() - 4, 1.5, r.height() + 8))
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QPointF(fill_x, r.center().y()), 6, 6)


class FadingLabel(QLabel):
    """A pill label that fades in, waits, then fades out (used for OSD)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Osd")
        self.setStyleSheet(
            f"#Osd {{ background: {theme.OVERLAY_BG}; border-radius: 12px; padding: 8px 16px;"
            f" font-size: 12pt; font-weight: 600; }}")
        self._effect = QGraphicsOpacityEffect(self, opacity=0.0)
        self.setGraphicsEffect(self._effect)
        self._anim = QPropertyAnimation(self._effect, b"opacity", self, duration=180)
        self._timer = QTimer(self, singleShot=True)
        self._timer.timeout.connect(self._fade_out)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.hide()

    def flash(self, text: str, ms: int = 1300) -> None:
        self.setText(text)
        self.adjustSize()
        self.show()
        self.raise_()
        self._anim.stop()
        self._anim.setStartValue(self._effect.opacity())
        self._anim.setEndValue(1.0)
        self._anim.start()
        self._timer.start(ms)

    def _fade_out(self) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._effect.opacity())
        self._anim.setEndValue(0.0)
        self._anim.start()


class ToggleSwitch(QCheckBox):
    """iOS-style switch with an animated knob."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(42, 24)
        self._pos = 0.0
        self._anim = QVariantAnimation(self, duration=140, easingCurve=QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._set_pos)
        self.toggled.connect(self._animate)

    def _set_pos(self, v) -> None:
        self._pos = float(v)
        self.update()

    def _animate(self, on: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def setChecked(self, on: bool) -> None:  # noqa: N802 (Qt naming)
        super().setChecked(on)
        self._anim.stop()
        self._set_pos(1.0 if on else 0.0)

    def hitButton(self, pos) -> bool:  # noqa: N802
        return self.rect().contains(pos)

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        off = QColor(theme.SURFACE_3)
        on = QColor(theme.ACCENT)
        track = QColor(
            int(off.red() + (on.red() - off.red()) * self._pos),
            int(off.green() + (on.green() - off.green()) * self._pos),
            int(off.blue() + (on.blue() - off.blue()) * self._pos),
        )
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 6
        x = r.left() + 3 + (r.width() - d - 6) * self._pos
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QRectF(x, r.top() + 3, d, d))


class Fader:
    """Animate a widget's opacity with a QGraphicsOpacityEffect."""

    def __init__(self, widget: QWidget, duration: int = 220) -> None:
        self.widget = widget
        self.effect = QGraphicsOpacityEffect(widget, opacity=1.0)
        widget.setGraphicsEffect(self.effect)
        self.anim = QPropertyAnimation(self.effect, b"opacity", widget, duration=duration)
        self.anim.setEasingCurve(QEasingCurve.InOutCubic)
        self.anim.finished.connect(self._finished)
        self._target = 1.0

    def fade_to(self, target: float) -> None:
        if target == self._target and self.anim.state() == QPropertyAnimation.Running:
            return
        self._target = target
        if target > 0:
            self.widget.show()
        self.anim.stop()
        self.anim.setStartValue(self.effect.opacity())
        self.anim.setEndValue(target)
        self.anim.start()

    def _finished(self) -> None:
        if self._target == 0:
            self.widget.hide()

    @property
    def opacity(self) -> float:
        return self.effect.opacity()


__all__ = ["IconButton", "SeekSlider", "VolumeSlider", "FadingLabel", "Fader", "ToggleSwitch"]
