"""Small dialogs: base dialog, jump to time."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

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
        self.error.setObjectName("Error")
        self.body.addWidget(self.error)
        self.add_buttons("Go")

    def accept(self) -> None:
        if self.value() is None:
            self.error.setText("Use seconds, m:ss or h:mm:ss")
            return
        super().accept()

    def value(self) -> int | None:
        return parse_time(self.edit.text())
