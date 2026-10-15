"""Windows media overlay (System Media Transport Controls) and hardware media keys.

A desktop app without a CoreWindow gets an SMTC through a
``Windows.Media.Playback.MediaPlayer`` whose own command manager is turned
off: VLClone never plays anything through it, it only borrows its SMTC to
publish the title and state and to receive the overlay's / media keys'
Play, Pause, Next, Previous and Stop. pywinrt (``winrt-*`` packages)
provides the WinRT projection; without it this is a no-op.

Everything WinRT runs on one worker thread: pywinrt initialises COM as a
multi-threaded apartment when imported, while Qt's GUI thread is already a
single-threaded apartment (OLE drag and drop), and the two must not meet on
one thread. The window hands states to the worker with :meth:`update` and
drains button presses with :meth:`poll` from its timer (presses arrive on
a WinRT thread-pool thread and are queued).
"""

from __future__ import annotations

import collections
import logging
import queue
import threading

from ..core.desktop import SessionState

log = logging.getLogger("vlclone.smtc")

_CLOSE = object()


def _enum(enum_type, *names):
    for name in names:
        if hasattr(enum_type, name):
            return getattr(enum_type, name)
    raise AttributeError(f"{enum_type.__name__} has none of {names}")


class _Session:
    """The WinRT side; created and used on the worker thread only."""

    def __init__(self, pressed) -> None:
        from winrt.windows.media import (MediaPlaybackStatus, MediaPlaybackType,
                                         SystemMediaTransportControlsButton)
        from winrt.windows.media.playback import MediaPlayer

        B = SystemMediaTransportControlsButton
        self.buttons = {}
        for command, names in (("play", ("PLAY", "Play")), ("pause", ("PAUSE", "Pause")),
                               ("next", ("NEXT", "Next")), ("previous", ("PREVIOUS", "Previous")),
                               ("stop", ("STOP", "Stop"))):
            self.buttons[_enum(B, *names)] = command
        S = MediaPlaybackStatus
        self.status = {"playing": _enum(S, "PLAYING", "Playing"), "paused": _enum(S, "PAUSED", "Paused"),
                       "stopped": _enum(S, "STOPPED", "Stopped"), "closed": _enum(S, "CLOSED", "Closed")}
        self.type_video = _enum(MediaPlaybackType, "VIDEO", "Video")
        self.type_music = _enum(MediaPlaybackType, "MUSIC", "Music")
        self.player = MediaPlayer()
        self.player.command_manager.is_enabled = False
        smtc = self.player.system_media_transport_controls
        smtc.is_enabled = True
        smtc.is_play_enabled = True
        smtc.is_pause_enabled = True
        smtc.is_stop_enabled = True
        smtc.is_next_enabled = False
        smtc.is_previous_enabled = False
        smtc.playback_status = self.status["closed"]
        self.token = smtc.add_button_pressed(lambda sender, args: pressed(self.buttons.get(args.button)))
        self.smtc = smtc
        self.last: SessionState | None = None

    def update(self, s: SessionState) -> None:
        last, self.last = self.last, s
        smtc = self.smtc
        if last is None or (last.title, last.artist, last.has_video) != (s.title, s.artist, s.has_video):
            du = smtc.display_updater
            du.clear_all()
            if s.title:
                if s.has_video:
                    du.type = self.type_video
                    du.video_properties.title = s.title
                    if s.artist:
                        du.video_properties.subtitle = s.artist
                else:
                    du.type = self.type_music
                    du.music_properties.title = s.title
                    du.music_properties.artist = s.artist
            du.update()
        smtc.is_next_enabled = s.can_next
        smtc.is_previous_enabled = s.can_previous
        smtc.playback_status = self.status["closed" if not s.title else s.status]

    def close(self) -> None:
        try:
            self.smtc.remove_button_pressed(self.token)
            self.smtc.is_enabled = False
        except Exception:
            pass


class MediaOverlay:
    def __init__(self, timeout_s: float = 8.0) -> None:
        self._pressed: collections.deque[str] = collections.deque(maxlen=32)
        self._lock = threading.Lock()
        self._jobs: queue.Queue = queue.Queue()
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self._last: SessionState | None = None
        self._thread = threading.Thread(target=self._run, name="media-overlay", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout_s):
            raise TimeoutError("the media overlay did not start")
        if self._error is not None:
            raise self._error
        log.info("media overlay (SMTC) ready")

    def _press(self, command: str | None) -> None:  # WinRT thread pool
        if command:
            with self._lock:
                self._pressed.append(command)

    def _run(self) -> None:
        try:
            session = _Session(self._press)
        except BaseException as exc:  # ImportError without pywinrt, OSError on old Windows…
            self._error = exc
            self._ready.set()
            return
        self._ready.set()
        while True:
            job = self._jobs.get()
            if job is _CLOSE:
                session.close()
                return
            try:
                session.update(job)
            except Exception as exc:
                log.warning("media overlay update failed: %s", exc)

    def poll(self) -> list[str]:
        with self._lock:
            commands = list(self._pressed)
            self._pressed.clear()
        return commands

    def update(self, s: SessionState) -> None:
        """Publish the player's state (only changes are sent; position ticks are ignored)."""
        key = (s.status, s.title, s.artist, s.has_video, s.can_previous, s.can_next)
        if self._last is not None and key == (self._last.status, self._last.title, self._last.artist,
                                              self._last.has_video, self._last.can_previous, self._last.can_next):
            return
        self._last = s
        self._jobs.put(s)

    def close(self, timeout_s: float = 2.0) -> None:
        self._jobs.put(_CLOSE)
        self._thread.join(timeout_s)


def create_media_overlay() -> MediaOverlay | None:
    try:
        return MediaOverlay()
    except Exception as exc:  # no pywinrt, old Windows, sandbox…
        log.warning("media overlay unavailable: %s: %s", type(exc).__name__, exc)
        return None
