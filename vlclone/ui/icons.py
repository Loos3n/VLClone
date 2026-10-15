"""Vector icons painted with QPainter.

Drawn on a 24×24 grid and rendered at whatever device pixel ratio is
needed, so they stay crisp on high-DPI screens without image assets.
"""

from __future__ import annotations

import math
from typing import Callable

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import (QColor, QFont, QIcon, QIconEngine, QPainter, QPainterPath, QPen,
                           QPixmap, QPolygonF)

from . import theme

Painter = Callable[[QPainter, QColor], None]
CONE_ORANGE = "#ff8a2a"
_REGISTRY: dict[str, Painter] = {}


def _icon(name: str):
    def deco(fn: Painter) -> Painter:
        _REGISTRY[name] = fn
        return fn
    return deco


def _pen(p: QPainter, c: QColor, width: float = 2.0) -> None:
    pen = QPen(c, width)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)


def _fill(p: QPainter, c: QColor) -> None:
    p.setPen(Qt.NoPen)
    p.setBrush(c)


def _poly(*pts: tuple[float, float]) -> QPolygonF:
    return QPolygonF([QPointF(x, y) for x, y in pts])


def _rounded_triangle(p: QPainter, pts, c: QColor) -> None:
    path = QPainterPath()
    path.addPolygon(_poly(*pts))
    path.closeSubpath()
    pen = QPen(c, 2.2)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(c)
    p.drawPath(path)


@_icon("play")
def _play(p, c):
    _rounded_triangle(p, [(8, 5.5), (18.5, 12), (8, 18.5)], c)


@_icon("pause")
def _pause(p, c):
    _fill(p, c)
    p.drawRoundedRect(QRectF(6.5, 5, 4, 14), 1.4, 1.4)
    p.drawRoundedRect(QRectF(13.5, 5, 4, 14), 1.4, 1.4)


@_icon("stop")
def _stop(p, c):
    _fill(p, c)
    p.drawRoundedRect(QRectF(6.5, 6.5, 11, 11), 2, 2)


@_icon("previous")
def _previous(p, c):
    _fill(p, c)
    p.drawRoundedRect(QRectF(5.5, 6, 2.6, 12), 1.2, 1.2)
    _rounded_triangle(p, [(18, 6.5), (10.5, 12), (18, 17.5)], c)


@_icon("next")
def _next(p, c):
    _fill(p, c)
    p.drawRoundedRect(QRectF(15.9, 6, 2.6, 12), 1.2, 1.2)
    _rounded_triangle(p, [(6, 6.5), (13.5, 12), (6, 17.5)], c)


@_icon("frame")
def _frame(p, c):
    _rounded_triangle(p, [(6, 7), (13, 12), (6, 17)], c)
    _fill(p, c)
    p.drawRoundedRect(QRectF(15.5, 6.5, 2.6, 11), 1.2, 1.2)


def _speaker(p, c):
    _fill(p, c)
    p.drawPolygon(_poly((3.5, 9.5), (7.5, 9.5), (12, 5.5), (12, 18.5), (7.5, 14.5), (3.5, 14.5)))


@_icon("volume-high")
def _vol_high(p, c):
    _speaker(p, c)
    _pen(p, c, 1.9)
    p.drawArc(QRectF(11, 8.5, 6, 7), -60 * 16, 120 * 16)
    p.drawArc(QRectF(11, 5.5, 10, 13), -60 * 16, 120 * 16)


@_icon("volume-low")
def _vol_low(p, c):
    _speaker(p, c)
    _pen(p, c, 1.9)
    p.drawArc(QRectF(11, 8.5, 6, 7), -60 * 16, 120 * 16)


@_icon("volume-muted")
def _vol_muted(p, c):
    _speaker(p, c)
    _pen(p, c, 1.9)
    p.drawLine(QPointF(15.5, 9.5), QPointF(20.5, 14.5))
    p.drawLine(QPointF(20.5, 9.5), QPointF(15.5, 14.5))


@_icon("fullscreen")
def _fullscreen(p, c):
    _pen(p, c, 2)
    for path in ([(4.5, 9), (4.5, 4.5), (9, 4.5)], [(15, 4.5), (19.5, 4.5), (19.5, 9)],
                 [(19.5, 15), (19.5, 19.5), (15, 19.5)], [(9, 19.5), (4.5, 19.5), (4.5, 15)]):
        p.drawPolyline(_poly(*path))


@_icon("fullscreen-exit")
def _fullscreen_exit(p, c):
    _pen(p, c, 2)
    for path in ([(4.5, 9), (9, 9), (9, 4.5)], [(15, 4.5), (15, 9), (19.5, 9)],
                 [(19.5, 15), (15, 15), (15, 19.5)], [(9, 19.5), (9, 15), (4.5, 15)]):
        p.drawPolyline(_poly(*path))


@_icon("playlist")
def _playlist(p, c):
    _pen(p, c, 2)
    p.drawLine(QPointF(4, 6.5), QPointF(16, 6.5))
    p.drawLine(QPointF(4, 11.5), QPointF(16, 11.5))
    p.drawLine(QPointF(4, 16.5), QPointF(11, 16.5))
    _rounded_triangle(p, [(15, 14), (20, 17), (15, 20)], c)


@_icon("sliders")
def _sliders(p, c):
    _pen(p, c, 2)
    for y, x in ((6.5, 15), (12, 8), (17.5, 13)):
        p.drawLine(QPointF(4, y), QPointF(20, y))
    _fill(p, c)
    for y, x in ((6.5, 15), (12, 8), (17.5, 13)):
        p.drawEllipse(QPointF(x, y), 2.6, 2.6)
    p.setBrush(QColor(theme.BG))
    for y, x in ((6.5, 15), (12, 8), (17.5, 13)):
        p.drawEllipse(QPointF(x, y), 1.0, 1.0)


@_icon("more")
def _more(p, c):
    _fill(p, c)
    for x in (6, 12, 18):
        p.drawEllipse(QPointF(x, 12), 1.9, 1.9)


@_icon("menu")
def _menu(p, c):
    _pen(p, c, 2)
    for y in (7, 12, 17):
        p.drawLine(QPointF(5, y), QPointF(19, y))


def _arrow_head(p, c, tip: tuple[float, float], angle_deg: float, size: float = 3.4):
    a = math.radians(angle_deg)
    left = (tip[0] - size * math.cos(a - 0.6), tip[1] - size * math.sin(a - 0.6))
    right = (tip[0] - size * math.cos(a + 0.6), tip[1] - size * math.sin(a + 0.6))
    p.drawPolyline(_poly(left, tip, right))


@_icon("repeat")
def _repeat(p, c):
    _pen(p, c, 2)
    p.drawPolyline(_poly((5, 13), (5, 9.5), (6.5, 8), (18.5, 8)))
    _arrow_head(p, c, (19, 8), 0)
    p.drawPolyline(_poly((19, 11), (19, 14.5), (17.5, 16), (5.5, 16)))
    _arrow_head(p, c, (5, 16), 180)


@_icon("repeat-one")
def _repeat_one(p, c):
    _repeat(p, c)
    f = QFont()
    f.setPixelSize(7)
    f.setBold(True)
    p.setFont(f)
    p.setPen(c)
    p.drawText(QRectF(8, 8.5, 8, 7.5), Qt.AlignCenter, "1")


@_icon("shuffle")
def _shuffle(p, c):
    _pen(p, c, 2)
    path = QPainterPath(QPointF(4, 7.5))
    path.cubicTo(QPointF(11, 7.5), QPointF(12, 16.5), QPointF(19, 16.5))
    p.drawPath(path)
    path = QPainterPath(QPointF(4, 16.5))
    path.cubicTo(QPointF(11, 16.5), QPointF(12, 7.5), QPointF(19, 7.5))
    p.drawPath(path)
    _arrow_head(p, c, (19.5, 7.5), 0, 3)
    _arrow_head(p, c, (19.5, 16.5), 0, 3)


@_icon("ab")
def _ab(p, c):
    f = QFont()
    f.setPixelSize(10)
    f.setBold(True)
    p.setFont(f)
    p.setPen(c)
    p.drawText(QRectF(1, 4, 22, 16), Qt.AlignCenter, "A-B")


@_icon("folder")
def _folder(p, c):
    _pen(p, c, 1.9)
    path = QPainterPath(QPointF(3.5, 7))
    path.lineTo(3.5, 18)
    path.lineTo(20.5, 18)
    path.lineTo(20.5, 9)
    path.lineTo(11.5, 9)
    path.lineTo(9.5, 6)
    path.lineTo(4.5, 6)
    path.closeSubpath()
    p.drawPath(path)


@_icon("file")
def _file(p, c):
    _pen(p, c, 1.9)
    p.drawPolyline(_poly((14, 3.5), (6, 3.5), (6, 20.5), (18, 20.5), (18, 7.5), (14, 3.5), (14, 7.5), (18, 7.5)))
    _fill(p, c)
    _rounded_triangle(p, [(10.5, 11), (14.5, 13.5), (10.5, 16)], c)


@_icon("link")
def _link(p, c):
    _pen(p, c, 2)
    p.save()
    p.translate(12, 12)
    p.rotate(-45)
    p.drawRoundedRect(QRectF(-8.5, -3, 9, 6), 3, 3)
    p.drawRoundedRect(QRectF(-0.5, -3, 9, 6), 3, 3)
    p.restore()


@_icon("plus")
def _plus(p, c):
    _pen(p, c, 2)
    p.drawLine(QPointF(12, 5), QPointF(12, 19))
    p.drawLine(QPointF(5, 12), QPointF(19, 12))


@_icon("close")
def _close(p, c):
    _pen(p, c, 2)
    p.drawLine(QPointF(6.5, 6.5), QPointF(17.5, 17.5))
    p.drawLine(QPointF(17.5, 6.5), QPointF(6.5, 17.5))


@_icon("trash")
def _trash(p, c):
    _pen(p, c, 1.8)
    p.drawLine(QPointF(4.5, 6.5), QPointF(19.5, 6.5))
    p.drawPolyline(_poly((9.5, 6.5), (9.5, 4), (14.5, 4), (14.5, 6.5)))
    p.drawPolyline(_poly((6.5, 6.5), (7.5, 20), (16.5, 20), (17.5, 6.5)))


@_icon("save")
def _save(p, c):
    _pen(p, c, 1.9)
    p.drawLine(QPointF(12, 4), QPointF(12, 14.5))
    p.drawPolyline(_poly((8, 10.5), (12, 14.5), (16, 10.5)))
    p.drawPolyline(_poly((4.5, 15), (4.5, 19.5), (19.5, 19.5), (19.5, 15)))


@_icon("camera")
def _camera(p, c):
    _pen(p, c, 1.9)
    path = QPainterPath()
    path.addRoundedRect(QRectF(3.5, 7, 17, 12), 2.5, 2.5)
    p.drawPath(path)
    p.drawPolyline(_poly((8.5, 7), (10, 4.5), (14, 4.5), (15.5, 7)))
    p.drawEllipse(QPointF(12, 13), 3.2, 3.2)


@_icon("info")
def _info(p, c):
    _pen(p, c, 1.9)
    p.drawEllipse(QPointF(12, 12), 8.5, 8.5)
    p.drawLine(QPointF(12, 11), QPointF(12, 16.5))
    _fill(p, c)
    p.drawEllipse(QPointF(12, 7.8), 1.2, 1.2)


@_icon("settings")
def _settings(p, c):
    _pen(p, c, 1.9)
    path = QPainterPath()
    teeth = 8
    for i in range(teeth * 2):
        a = math.pi * 2 * i / (teeth * 2)
        r = 8.6 if i % 2 == 0 else 6.6
        a2 = a + math.pi / (teeth * 2)
        for ang in (a, a2):
            pt = QPointF(12 + r * math.cos(ang), 12 + r * math.sin(ang))
            if path.elementCount() == 0:
                path.moveTo(pt)
            else:
                path.lineTo(pt)
    path.closeSubpath()
    p.drawPath(path)
    p.drawEllipse(QPointF(12, 12), 2.8, 2.8)


@_icon("subtitles")
def _subtitles(p, c):
    _pen(p, c, 1.9)
    p.drawRoundedRect(QRectF(3.5, 5.5, 17, 13), 2.5, 2.5)
    p.drawLine(QPointF(7, 12), QPointF(10, 12))
    p.drawLine(QPointF(12.5, 12), QPointF(17, 12))
    p.drawLine(QPointF(7, 15.2), QPointF(14, 15.2))


@_icon("check")
def _check(p, c):
    _pen(p, c, 2.2)
    p.drawPolyline(_poly((5.5, 12.5), (10, 17), (18.5, 7.5)))


@_icon("cone")
def _cone(p, c):
    _fill(p, QColor(CONE_ORANGE))  # the logo keeps VLC's orange whatever the accent
    p.drawPolygon(_poly((9.2, 3.5), (14.8, 3.5), (19, 17), (5, 17)))
    _fill(p, QColor("#ffffff"))
    p.drawPolygon(_poly((8.1, 7.2), (15.9, 7.2), (16.7, 9.8), (7.3, 9.8)))
    p.drawPolygon(_poly((6.6, 12.1), (17.4, 12.1), (18.2, 14.6), (5.8, 14.6)))
    _fill(p, QColor(CONE_ORANGE))
    p.drawRoundedRect(QRectF(3, 16.5, 18, 4), 1.5, 1.5)


@_icon("drop")
def _drop(p, c):
    _pen(p, c, 1.6)
    p.drawRoundedRect(QRectF(3, 3, 18, 18), 4, 4)
    p.drawLine(QPointF(12, 7.5), QPointF(12, 15.5))
    p.drawPolyline(_poly((8.5, 12.5), (12, 16), (15.5, 12.5)))


@_icon("library")
def _library(p, c):
    _pen(p, c, 1.8)
    p.drawRoundedRect(QRectF(3.5, 4, 4, 16), 1, 1)
    p.drawRoundedRect(QRectF(9, 4, 4, 16), 1, 1)
    p.save()
    p.translate(17.2, 4.2)
    p.rotate(14)
    p.drawRoundedRect(QRectF(0, 0, 4, 15.6), 1, 1)
    p.restore()


@_icon("disc")
def _disc(p, c):
    _pen(p, c, 1.8)
    p.drawEllipse(QPointF(12, 12), 8.5, 8.5)
    p.drawEllipse(QPointF(12, 12), 2.2, 2.2)
    _pen(p, c, 1.3)
    p.drawArc(QRectF(6.5, 6.5, 11, 11), 100 * 16, 70 * 16)


@_icon("webcam")
def _webcam(p, c):
    _pen(p, c, 1.8)
    p.drawEllipse(QPointF(12, 10), 7, 7)
    p.drawEllipse(QPointF(12, 10), 2.6, 2.6)
    p.drawPolyline(_poly((8, 19.5), (16, 19.5)))
    p.drawLine(QPointF(12, 17), QPointF(12, 19.5))


@_icon("film")
def _film(p, c):
    _pen(p, c, 1.8)
    p.drawRoundedRect(QRectF(4, 3.5, 16, 17), 2, 2)
    p.drawLine(QPointF(8, 3.5), QPointF(8, 20.5))
    p.drawLine(QPointF(16, 3.5), QPointF(16, 20.5))
    for y in (8, 12, 16):
        p.drawLine(QPointF(4, y), QPointF(8, y))
        p.drawLine(QPointF(16, y), QPointF(20, y))


@_icon("music")
def _music(p, c):
    _pen(p, c, 1.9)
    p.drawPolyline(_poly((9, 17), (9, 5.5), (19, 3.5), (19, 15)))
    _fill(p, c)
    p.drawEllipse(QPointF(6.8, 17.3), 2.6, 2.1)
    p.drawEllipse(QPointF(16.8, 15.3), 2.6, 2.1)


@_icon("clock")
def _clock(p, c):
    _pen(p, c, 1.8)
    p.drawEllipse(QPointF(12, 12), 8.5, 8.5)
    p.drawPolyline(_poly((12, 7), (12, 12), (15.5, 14)))


@_icon("continue")
def _continue(p, c):
    _pen(p, c, 1.8)
    p.drawArc(QRectF(3.5, 3.5, 17, 17), 90 * 16, -300 * 16)
    _rounded_triangle(p, [(10, 8.5), (16, 12), (10, 15.5)], c)


@_icon("refresh")
def _refresh(p, c):
    _pen(p, c, 1.9)
    p.drawArc(QRectF(5, 5, 14, 14), 40 * 16, 280 * 16)
    _arrow_head(p, c, (17.6, 7.2), -50, 3)


@_icon("chapters")
def _chapters(p, c):
    _pen(p, c, 1.9)
    p.drawLine(QPointF(3.5, 12), QPointF(20.5, 12))
    for x in (8, 14):
        p.drawLine(QPointF(x, 8), QPointF(x, 16))


@_icon("record")
def _record(p, c):
    _fill(p, c)
    p.drawEllipse(QPointF(12, 12), 6, 6)


@_icon("convert")
def _convert(p, c):
    _pen(p, c, 1.9)
    p.drawPolyline(_poly((4, 9), (18, 9)))
    _arrow_head(p, c, (19.5, 9), 0, 3.2)
    p.drawPolyline(_poly((20, 15), (6, 15)))
    _arrow_head(p, c, (4.5, 15), 180, 3.2)


@_icon("stream")
def _stream(p, c):
    _fill(p, c)
    p.drawEllipse(QPointF(12, 12), 2.2, 2.2)
    _pen(p, c, 1.8)
    for r in (5.5, 9.0):
        p.drawArc(QRectF(12 - r, 12 - r, 2 * r, 2 * r), -45 * 16, 90 * 16)
        p.drawArc(QRectF(12 - r, 12 - r, 2 * r, 2 * r), 135 * 16, 90 * 16)


class _PaintedEngine(QIconEngine):
    def __init__(self, painter: Painter, color: str | None) -> None:
        super().__init__()
        self._painter = painter
        self._color = color  # a colour, a theme token ("@ACCENT"), or None = the theme's text

    def _color_for(self, mode: QIcon.Mode) -> QColor:
        c = QColor(theme.resolve(self._color) if self._color else theme.TEXT)
        if mode == QIcon.Disabled:
            c.setAlphaF(0.35)
        elif mode == QIcon.Active:
            c = c.lighter(115)
        return c

    def paint(self, painter: QPainter, rect: QRect, mode: QIcon.Mode, state: QIcon.State) -> None:
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        side = min(rect.width(), rect.height())
        painter.translate(rect.x() + (rect.width() - side) / 2, rect.y() + (rect.height() - side) / 2)
        painter.scale(side / 24.0, side / 24.0)
        self._painter(painter, self._color_for(mode))
        painter.restore()

    def pixmap(self, size: QSize, mode: QIcon.Mode, state: QIcon.State) -> QPixmap:
        pm = QPixmap(size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        self.paint(p, QRect(0, 0, size.width(), size.height()), mode, state)
        p.end()
        return pm

    def scaledPixmap(self, size: QSize, mode: QIcon.Mode, state: QIcon.State, scale: float) -> QPixmap:
        pm = self.pixmap(QSize(int(size.width() * scale), int(size.height() * scale)), mode, state)
        pm.setDevicePixelRatio(scale)
        return pm

    def clone(self) -> "QIconEngine":
        return _PaintedEngine(self._painter, self._color)


_cache: dict[tuple[str, str], QIcon] = {}


def icon(name: str, color: str | None = None) -> QIcon:
    """A painted icon. ``color`` is a colour, a theme token like ``"@ACCENT"`` (follows theme
    changes) or None for the current theme's text colour."""
    key = (name, color or "")
    if key not in _cache:
        painter = _REGISTRY.get(name)
        if painter is None:
            raise KeyError(f"unknown icon {name!r}")
        _cache[key] = QIcon(_PaintedEngine(painter, color))
    return _cache[key]


def names() -> list[str]:
    return sorted(_REGISTRY)
