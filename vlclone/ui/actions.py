"""Named actions with VLC-style keyboard shortcuts.

Every command (buttons, menus, shortcuts) goes through an action id, so
new features only need to register an action and, optionally, a default
key in ``core.shortcuts``.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QWidget

from ..core.shortcuts import DEFAULT_SHORTCUTS, resolve_shortcuts


class ActionRegistry:
    def __init__(self, host: QWidget, overrides: dict | None = None) -> None:
        self.host = host
        self.actions: dict[str, QAction] = {}
        self._callbacks: dict[str, Callable[[], None]] = {}
        self.bindings = resolve_shortcuts(overrides)

    def register(self, action_id: str, callback: Callable[[], None], text: str | None = None,
                 checkable: bool = False) -> QAction:
        label = text or DEFAULT_SHORTCUTS.get(action_id, (action_id.replace("_", " ").capitalize(), []))[0]
        action = QAction(label, self.host)
        action.setCheckable(checkable)
        action.setShortcutContext(Qt.ApplicationShortcut)
        keys = [QKeySequence(k) for k in self.bindings.get(action_id, [])]
        action.setShortcuts(keys)
        action.triggered.connect(lambda _checked=False: callback())
        self.host.addAction(action)
        self.actions[action_id] = action
        self._callbacks[action_id] = callback
        return action

    def trigger(self, action_id: str) -> None:
        cb = self._callbacks.get(action_id)
        if cb is not None:
            cb()

    def get(self, action_id: str) -> QAction:
        return self.actions[action_id]

    def apply_bindings(self, overrides: dict | None) -> None:
        self.bindings = resolve_shortcuts(overrides)
        for action_id, action in self.actions.items():
            action.setShortcuts([QKeySequence(k) for k in self.bindings.get(action_id, [])])

    def shortcut_text(self, action_id: str) -> str:
        keys = self.bindings.get(action_id, [])
        return QKeySequence(keys[0]).toString(QKeySequence.NativeText) if keys else ""
