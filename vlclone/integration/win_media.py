"""Windows media overlay (System Media Transport Controls) and hardware media keys.

A desktop app without a CoreWindow gets an SMTC through a
``Windows.Media.Playback.MediaPlayer`` whose own command manager is turned
off: VLClone never plays anything through it, it only borrows its SMTC to
publish the title and state and to receive the overlay's / media keys'
Play, Pause, Next, Previous and Stop. pywinrt (``winrt-*`` packages)
provides the WinRT projection; without it this is a no-op.

Button presses arrive on a WinRT thread pool thread, so they are queued and
the window's timer drains them with :meth:`MediaOverlay.poll`.
"""

from __future__ import annotations

import collections
import logging
import threading

from ..core.desktop import SessionState

log = logging.getLogger("vlclone.smtc")


def _enum(enum_type, *names):
    for name in names:
        if hasattr(enum_type, name):
            return getattr(enum_type, name)
    raise AttributeError(f"{enum_type.__name__} has none of {names}")


class MediaOverlay:
    def __init__(self) -> None:
        from winrt.windows.media import (MediaPlaybackStatus, MediaPlaybackType,
                                         SystemMediaTransportControlsButton)
        from winrt.windows.media.playback import MediaPlayer

        B = SystemMediaTransportControlsButton
        self._buttons = {}
        for command, names in (("play", ("PLAY", "Play")), ("pause", ("PAUSE", "Pause")),
                               ("next", ("NEXT", "Next")), ("previous", ("PREVIOUS", "Previous")),
                               ("stop", ("STOP", "Stop"))):
            self._buttons[_enum(B, *names)] = command
        S = MediaPlaybackStatus
        self._status = {"playing": _enum(S, "PLAYING", "Playing"), "paused": _enum(S, "PAUSED", "Paused"),
                        "stopped": _enum(S, "STOPPED", "Stopped"), "closed": _enum(S, "CLOSED", "Closed")}
        self._type_video = _enum(MediaPlaybackType, "VIDEO", "Video")
        self._type_music = _enum(MediaPlaybackType, "MUSIC", "Music")
        self._queue: collections.deque[str] = collections.deque(maxlen=32)
        self._lock = threading.Lock()
        self._player = MediaPlayer()
        self._player.command_manager.is_enabled = False
        smtc = self._player.system_media_transport_controls
        smtc.is_enabled = True
        for prop in ("is_play_enabled", "is_pause_enabled", "is_stop_enabled"):
            setattr(smtc, prop, True)
        smtc.is_next_enabled = False
        smtc.is_previous_enabled = False
        smtc.playback_status = self._status["closed"]
        self._token = smtc.add_button_pressed(self._pressed)
        self._smtc = smtc
        self._last: SessionState | None = None
        log.info("media overlay (SMTC) ready")

    def _pressed(self, sender, args) -> None:  # WinRT thread
        command = self._buttons.get(args.button)
        if command:
            with self._lock:
                self._queue.append(command)

    def poll(self) -> list[str]:
        with self._lock:
            commands = list(self._queue)
            self._queue.clear()
        return commands

    def update(self, s: SessionState) -> None:
        if s == self._last:
            return
        last, self._last = self._last, s
        smtc = self._smtc
        if last is None or (last.title, last.artist, last.has_video) != (s.title, s.artist, s.has_video):
            du = smtc.display_updater
            du.clear_all()
            if s.title:
                if s.has_video:
                    du.type = self._type_video
                    du.video_properties.title = s.title
                    if s.artist:
                        du.video_properties.subtitle = s.artist
                else:
                    du.type = self._type_music
                    du.music_properties.title = s.title
                    du.music_properties.artist = s.artist
            du.update()
        smtc.is_next_enabled = s.can_next
        smtc.is_previous_enabled = s.can_previous
        smtc.playback_status = self._status["closed" if not s.title else s.status]

    def close(self) -> None:
        try:
            self._smtc.remove_button_pressed(self._token)
            self._smtc.is_enabled = False
        except Exception:
            pass


def create_media_overlay() -> MediaOverlay | None:
    try:
        return MediaOverlay()
    except Exception as exc:  # no pywinrt, old Windows, sandbox…
        log.warning("media overlay unavailable: %s: %s", type(exc).__name__, exc)
        return None
