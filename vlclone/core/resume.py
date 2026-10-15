"""Remember where each file was left off."""

from __future__ import annotations

import time
from pathlib import Path

from .jsonstore import load_json, save_json
from .paths import config_dir


class ResumeStore:
    MAX_ENTRIES = 500
    #: Positions this close to the end count as "finished".
    END_MARGIN_MS = 15_000

    def __init__(self, path: Path | None = None, min_position_ms: int = 15_000) -> None:
        self.path = path or (config_dir() / "resume.json")
        self.min_position_ms = min_position_ms
        raw = load_json(self.path, {})
        self._data: dict[str, dict] = raw if isinstance(raw, dict) else {}
        self._dirty = False

    def get(self, mrl: str) -> int | None:
        entry = self._data.get(mrl)
        if not isinstance(entry, dict):
            return None
        pos = entry.get("pos")
        return int(pos) if isinstance(pos, (int, float)) and pos > 0 else None

    def update(self, mrl: str, position_ms: int, length_ms: int) -> None:
        """Record a position; clears the entry near the start or the end."""
        if not mrl or position_ms is None or position_ms < 0:
            return
        if length_ms is not None and length_ms <= 0:
            # Live streams and unknown lengths have nothing to resume.
            return
        near_end = length_ms and position_ms >= length_ms - self.END_MARGIN_MS
        if position_ms < self.min_position_ms or near_end:
            self.forget(mrl)
            return
        self._data[mrl] = {"pos": int(position_ms), "len": int(length_ms or 0), "t": time.time()}
        self._dirty = True

    def forget(self, mrl: str) -> None:
        if self._data.pop(mrl, None) is not None:
            self._dirty = True

    def save(self) -> None:
        if not self._dirty:
            return
        if len(self._data) > self.MAX_ENTRIES:
            newest = sorted(self._data.items(), key=lambda kv: kv[1].get("t", 0), reverse=True)
            self._data = dict(newest[: self.MAX_ENTRIES])
        save_json(self.path, self._data)
        self._dirty = False
