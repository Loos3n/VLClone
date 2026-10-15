"""Native widget that libVLC renders into."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPalette
from PySide6.QtWidgets import QWidget


class VideoSurface(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_NativeWindow)
        self.setAttribute(Qt.WA_DontCreateNativeAncestors)
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        pal = self.palette()
        pal.setColor(QPalette.Window, QColor("#000000"))
        self.setPalette(pal)
        self.setAutoFillBackground(True)
        self.setMinimumSize(320, 180)

    def native_handle(self) -> int:
        return int(self.winId())

    def paintEvent(self, e) -> None:
        QPainter(self).fillRect(self.rect(), QColor("#000000"))
