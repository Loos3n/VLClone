"""A tiny toolkit-agnostic observer used by the core layer.

The UI subscribes with plain callables, so the core stays free of Qt.
"""

from __future__ import annotations

from typing import Any, Callable


class Signal:
    def __init__(self) -> None:
        self._slots: list[Callable[..., Any]] = []

    def connect(self, slot: Callable[..., Any]) -> None:
        if slot not in self._slots:
            self._slots.append(slot)

    def disconnect(self, slot: Callable[..., Any]) -> None:
        if slot in self._slots:
            self._slots.remove(slot)

    def emit(self, *args: Any) -> None:
        for slot in list(self._slots):
            slot(*args)
