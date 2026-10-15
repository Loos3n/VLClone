"""Small dialogs: open URL, jump to time."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QVBoxLayout, QWidget)

from ..core.media_sources import is_url
from ..core.timefmt import format_ms, parse_time


class BaseDialog(QDialog):
    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(22, 20, 22, 18)
        self.body.setSpacing(12)
        heading = QLabel(title)
        heading.setObjectName("Heading")
        self.body.addWidget(heading)

    def add_buttons(self, ok_text: str = "OK") -> QDialogButtonBox:
        box = QDialogButtonBox()
        cancel = box.addButton(QDialogButtonBox.Cancel)
        ok = box.addButton(ok_text, QDialogButtonBox.AcceptRole)
        ok.setObjectName("Primary")
        ok.setDefault(True)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        cancel.setCursor(Qt.PointingHandCursor)
        ok.setCursor(Qt.PointingHandCursor)
        self.body.addSpacing(4)
        self.body.addWidget(box)
        return box


class OpenUrlDialog(BaseDialog):
    def __init__(self, recent: list[str] | None = None, parent: QWidget | None = None) -> None:
        super().__init__("Open network stream", parent)
        self.setMinimumWidth(520)
        hint = QLabel("HTTP, HLS, RTSP, RTP/UDP, MMS, FTP… anything VLC can open.")
        hint.setObjectName("Muted")
        self.body.addWidget(hint)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("https://example.com/video.m3u8  ·  rtsp://camera.local/stream")
        clip = QGuiApplication.clipboard().text().strip() if QGuiApplication.clipboard() else ""
        if clip and is_url(clip) and "\n" not in clip:
            self.edit.setText(clip)
            self.edit.selectAll()
        self.body.addWidget(self.edit)
        if recent:
            label = QLabel("Recent")
            label.setObjectName("SectionTitle")
            self.body.addWidget(label)
            for url in recent[:5]:
                b = QPushButton(url)
                b.setObjectName("ListRow")
                b.setCursor(Qt.PointingHandCursor)
                b.clicked.connect(lambda _=False, u=url: (self.edit.setText(u), self.accept()))
                self.body.addWidget(b)
        self.add_buttons("Play")
        self.edit.setFocus()

    def url(self) -> str:
        return self.edit.text().strip()


class JumpToTimeDialog(BaseDialog):
    def __init__(self, current_ms: int, length_ms: int, parent: QWidget | None = None) -> None:
        super().__init__("Jump to time", parent)
        self.length_ms = length_ms
        row = QHBoxLayout()
        self.edit = QLineEdit(format_ms(current_ms, force_hours=length_ms >= 3_600_000))
        self.edit.setPlaceholderText("h:mm:ss")
        self.edit.selectAll()
        row.addWidget(self.edit, 1)
        if length_ms > 0:
            total = QLabel(f"/ {format_ms(length_ms)}")
            total.setObjectName("Muted")
            row.addWidget(total)
        self.body.addLayout(row)
        self.error = QLabel("")
        self.error.setStyleSheet("color: #ff5d5d;")
        self.body.addWidget(self.error)
        self.add_buttons("Go")

    def accept(self) -> None:
        if self.value() is None:
            self.error.setText("Use seconds, m:ss or h:mm:ss")
            return
        super().accept()

    def value(self) -> int | None:
        return parse_time(self.edit.text())
