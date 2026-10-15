"""What the desktop integrations should show, decided without any OS calls.

The Windows pieces (``vlclone/integration``) only translate these values
into API calls: the media overlay (SMTC), the taskbar thumbnail buttons and
progress, and the keep-awake request. Keeping the decisions here makes them
testable on any platform.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from .backend import PlaybackState


@dataclass(frozen=True)
class SessionState:
    """The player as the OS sees it."""
    status: str = "stopped"   # "playing", "paused", "stopped"
    title: str = ""
    artist: str = ""
    has_video: bool = False
    position_ms: int = 0
    length_ms: int = -1
    can_previous: bool = False
    can_next: bool = False


def status_for(state: PlaybackState) -> str:
    if state in (PlaybackState.PLAYING, PlaybackState.OPENING, PlaybackState.BUFFERING):
        return "playing"
    if state is PlaybackState.PAUSED:
        return "paused"
    return "stopped"


def should_inhibit_sleep(session: SessionState, enabled: bool = True) -> bool:
    """Keep the display awake only while a video is actually playing (not audio, not paused)."""
    return enabled and session.status == "playing" and session.has_video


# Taskbar progress states (same meaning as Windows' TBPF_* flags).
PROGRESS_NONE, PROGRESS_NORMAL, PROGRESS_PAUSED, PROGRESS_INDETERMINATE = "none", "normal", "paused", "indeterminate"


@dataclass(frozen=True)
class TaskbarState:
    progress: str
    value: int      # 0..1000
    play_icon: str  # "play" or "pause": what the middle button would do
    previous_enabled: bool
    next_enabled: bool
    play_enabled: bool


def taskbar_state(session: SessionState) -> TaskbarState:
    value = 0
    if session.status == "stopped":
        progress = PROGRESS_NONE
    elif session.length_ms <= 0:
        progress = PROGRESS_INDETERMINATE if session.status == "playing" else PROGRESS_NONE
    else:
        progress = PROGRESS_NORMAL if session.status == "playing" else PROGRESS_PAUSED
        value = min(1000, max(0, int(session.position_ms * 1000 / session.length_ms)))
    return TaskbarState(progress, value, "pause" if session.status == "playing" else "play",
                        session.can_previous, session.can_next, True)


class CommandDebouncer:
    """Drops a command that arrives twice from two routes in quick succession.

    A media key can reach VLClone both as a Qt shortcut (window focused) and
    through the Windows media overlay; the second copy must not undo the first
    (play/pause twice = nothing happens).
    """

    def __init__(self, window_s: float = 0.35, clock: Callable[[], float] = time.monotonic) -> None:
        self.window_s = window_s
        self.clock = clock
        self._last: dict[str, tuple[str, float]] = {}

    def accept(self, command: str, source: str) -> bool:
        now = self.clock()
        last = self._last.get(command)
        if last is not None and last[0] != source and now - last[1] < self.window_s:
            return False
        self._last[command] = (source, now)
        return True
