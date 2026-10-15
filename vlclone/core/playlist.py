"""Playlist model: ordering, current item, repeat and shuffle.

Pure Python so it can be unit-tested and reused by any frontend.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum
from itertools import count

from .events import Signal
from .media_sources import display_name

_ids = count(1)


class RepeatMode(str, Enum):
    NONE = "none"
    ALL = "all"
    ONE = "one"

    def next(self) -> "RepeatMode":
        order = [RepeatMode.NONE, RepeatMode.ALL, RepeatMode.ONE]
        return order[(order.index(self) + 1) % len(order)]


@dataclass
class PlaylistItem:
    mrl: str
    title: str = ""
    duration_ms: int = -1
    uid: int = field(default_factory=lambda: next(_ids))

    def __post_init__(self) -> None:
        if not self.title:
            self.title = display_name(self.mrl)


class Playlist:
    """Ordered list of items with a "current" pointer.

    Shuffle keeps a separate play order so that the visible order is never
    changed by toggling shuffle; every item is visited once per cycle.
    """

    def __init__(self, rng: random.Random | None = None) -> None:
        self._items: list[PlaylistItem] = []
        self._current: int = -1
        self.repeat: RepeatMode = RepeatMode.NONE
        self._shuffle = False
        self._order: list[int] = []  # uids in shuffled play order
        self._rng = rng or random.Random()
        self.changed = Signal()          # structure changed
        self.current_changed = Signal()  # (index)

    # ----------------------------------------------------------- inspection
    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self):
        return iter(self._items)

    def __getitem__(self, index: int) -> PlaylistItem:
        return self._items[index]

    @property
    def items(self) -> list[PlaylistItem]:
        return list(self._items)

    @property
    def current_index(self) -> int:
        return self._current

    @property
    def current(self) -> PlaylistItem | None:
        if 0 <= self._current < len(self._items):
            return self._items[self._current]
        return None

    def index_of(self, uid: int) -> int:
        for i, item in enumerate(self._items):
            if item.uid == uid:
                return i
        return -1

    # ------------------------------------------------------------- mutation
    def add(self, mrls: list[str], at: int | None = None) -> list[int]:
        """Insert items (MRLs or PlaylistItems); returns the inserted indexes."""
        new = [m if isinstance(m, PlaylistItem) else PlaylistItem(m) for m in mrls]
        if not new:
            return []
        pos = len(self._items) if at is None else max(0, min(at, len(self._items)))
        self._items[pos:pos] = new
        if self._current >= pos and self._current != -1:
            self._current += len(new)
        if self._shuffle:
            # New items are still to come in the current shuffle cycle.
            cur_uid = self.current.uid if self.current else None
            lo = self._order.index(cur_uid) + 1 if cur_uid in self._order else 0
            for item in new:
                self._order.insert(self._rng.randint(lo, len(self._order)), item.uid)
        self.changed.emit()
        return list(range(pos, pos + len(new)))

    def remove(self, indexes: list[int]) -> None:
        current_uid = self.current.uid if self.current else None
        removed = {self._items[i].uid for i in indexes if 0 <= i < len(self._items)}
        if not removed:
            return
        self._items = [it for it in self._items if it.uid not in removed]
        self._order = [u for u in self._order if u not in removed]
        if current_uid in removed:
            self._current = -1
        elif current_uid is not None:
            self._current = self.index_of(current_uid)
        self.changed.emit()
        if current_uid in removed:
            self.current_changed.emit(-1)

    def clear(self) -> None:
        self._items.clear()
        self._order.clear()
        had_current = self._current != -1
        self._current = -1
        self.changed.emit()
        if had_current:
            self.current_changed.emit(-1)

    def move(self, rows: list[int], dest: int) -> int:
        """Move ``rows`` so they end up in front of the item at ``dest``.

        ``dest`` is an index in the list *before* the move (like Qt's
        drag-and-drop row). Returns the new index of the first moved row.
        """
        rows = sorted(r for r in set(rows) if 0 <= r < len(self._items))
        if not rows:
            return -1
        dest = max(0, min(dest, len(self._items)))
        current_uid = self.current.uid if self.current else None
        moving = [self._items[r] for r in rows]
        shift = sum(1 for r in rows if r < dest)
        remaining = [it for i, it in enumerate(self._items) if i not in rows]
        insert_at = dest - shift
        self._items = remaining[:insert_at] + moving + remaining[insert_at:]
        if current_uid is not None:
            self._current = self.index_of(current_uid)
        self.changed.emit()
        return insert_at

    def set_title(self, index: int, title: str) -> None:
        if 0 <= index < len(self._items) and title:
            self._items[index].title = title
            self.changed.emit()

    def set_duration(self, index: int, duration_ms: int) -> None:
        if 0 <= index < len(self._items):
            self._items[index].duration_ms = duration_ms
            self.changed.emit()

    # ------------------------------------------------------------ navigation
    def set_current(self, index: int) -> PlaylistItem | None:
        if not 0 <= index < len(self._items):
            index = -1
        self._current = index
        if self._shuffle and index != -1:
            # Restart the shuffle cycle from the explicitly chosen item.
            self._reshuffle(start_uid=self._items[index].uid)
        self.current_changed.emit(index)
        return self.current

    @property
    def shuffle(self) -> bool:
        return self._shuffle

    @shuffle.setter
    def shuffle(self, enabled: bool) -> None:
        if enabled == self._shuffle:
            return
        self._shuffle = enabled
        if enabled:
            self._reshuffle(start_uid=self.current.uid if self.current else None)
        else:
            self._order = []

    def _reshuffle(self, start_uid: int | None = None) -> None:
        uids = [it.uid for it in self._items if it.uid != start_uid]
        self._rng.shuffle(uids)
        self._order = ([start_uid] if start_uid is not None else []) + uids

    def _play_order(self) -> list[int]:
        if self._shuffle:
            return [self.index_of(u) for u in self._order]
        return list(range(len(self._items)))

    def peek_next(self, auto: bool = False) -> int:
        """Index that :meth:`next` would select (-1 if none).

        ``auto`` means "the current item finished on its own", which is the
        only case where repeat-one replays the same item.
        """
        if not self._items:
            return -1
        if auto and self.repeat is RepeatMode.ONE and self._current != -1:
            return self._current
        order = self._play_order()
        if self._current == -1 or self._current not in order:
            return order[0]
        pos = order.index(self._current) + 1
        if pos < len(order):
            return order[pos]
        if self.repeat is not RepeatMode.NONE:
            return order[0]
        return -1

    def peek_previous(self) -> int:
        if not self._items:
            return -1
        order = self._play_order()
        if self._current == -1 or self._current not in order:
            return order[0]
        pos = order.index(self._current) - 1
        if pos >= 0:
            return order[pos]
        if self.repeat is not RepeatMode.NONE:
            return order[-1]
        return order[0]

    def next(self, auto: bool = False) -> PlaylistItem | None:
        index = self.peek_next(auto)
        if index == -1:
            return None
        if self._shuffle and index != self._current and self.current is not None:
            if self._order and self._order[-1] == self.current.uid:
                # A full shuffle cycle finished: start a fresh random order.
                self._reshuffle(start_uid=self._items[index].uid)
        self._current = index
        self.current_changed.emit(index)
        return self.current

    def previous(self) -> PlaylistItem | None:
        index = self.peek_previous()
        if index == -1:
            return None
        self._current = index
        self.current_changed.emit(index)
        return self.current
