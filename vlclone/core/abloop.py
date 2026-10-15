"""A-B repeat: loop playback between two timestamps."""

from __future__ import annotations

from enum import Enum


class ABState(str, Enum):
    OFF = "off"
    A_SET = "a_set"
    ACTIVE = "active"


class ABLoop:
    #: Minimum loop length, so A and B never collapse into a busy loop.
    MIN_LENGTH_MS = 200

    def __init__(self) -> None:
        self.a: int | None = None
        self.b: int | None = None

    @property
    def state(self) -> ABState:
        if self.a is None:
            return ABState.OFF
        if self.b is None:
            return ABState.A_SET
        return ABState.ACTIVE

    def toggle(self, time_ms: int) -> ABState:
        """Cycle like VLC's A-B button: set A, then B, then clear."""
        state = self.state
        if state is ABState.OFF:
            self.a = max(0, time_ms)
        elif state is ABState.A_SET:
            if time_ms < self.a:
                self.a, time_ms = time_ms, self.a
            if time_ms - self.a < self.MIN_LENGTH_MS:
                time_ms = self.a + self.MIN_LENGTH_MS
            self.b = time_ms
        else:
            self.clear()
        return self.state

    def clear(self) -> None:
        self.a = None
        self.b = None

    def check(self, time_ms: int) -> int | None:
        """Return the time to seek to if playback has passed B, else ``None``."""
        if self.state is ABState.ACTIVE and time_ms >= self.b:  # type: ignore[operator]
            return self.a
        return None
