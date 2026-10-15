"""The contract between player logic and the media engine.

``PlayerController`` only ever talks to a ``MediaBackend``. The production
implementation wraps libVLC (``vlc_backend.VlcBackend``); tests use
``fake_backend.FakeBackend``. New VLC features (equalizer, filters,
streaming output, ...) are added by extending this interface and both
implementations, never by calling python-vlc from the UI.

Units: times in milliseconds, delays in milliseconds, volume in percent
(0-200), rates as multipliers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum


class PlaybackState(str, Enum):
    IDLE = "idle"
    OPENING = "opening"
    BUFFERING = "buffering"
    PLAYING = "playing"
    PAUSED = "paused"
    STOPPED = "stopped"
    ENDED = "ended"
    ERROR = "error"


@dataclass
class Track:
    id: int
    name: str


@dataclass
class StreamInfo:
    kind: str  # "video" | "audio" | "subtitle" | "unknown"
    codec: str = ""
    language: str = ""
    description: str = ""
    width: int = 0
    height: int = 0
    fps: float = 0.0
    channels: int = 0
    sample_rate: int = 0
    bitrate: int = 0  # bits/s, 0 if unknown


@dataclass
class MediaInfo:
    mrl: str = ""
    title: str = ""
    duration_ms: int = -1
    meta: dict[str, str] = field(default_factory=dict)
    streams: list[StreamInfo] = field(default_factory=list)
    input_bitrate_kbps: float = 0.0
    demux_bitrate_kbps: float = 0.0
    decoded_video: int = 0
    displayed_pictures: int = 0
    lost_pictures: int = 0
    decoded_audio: int = 0
    lost_audio_buffers: int = 0


@dataclass
class VideoOptions:
    """Per-media video adjustments. ``None``/0 means "default"."""

    aspect_ratio: str | None = None
    crop: str | None = None
    zoom: float = 0.0  # 0 = fit to window
    rotation: int = 0  # 0, 90, 180, 270
    deinterlace: str | None = None  # None = off, else a libVLC mode name


class MediaBackend(ABC):
    # --- lifecycle / output
    @abstractmethod
    def set_video_output(self, handle: int) -> None:
        """Attach the video to a native window handle (HWND / XID / NSView)."""

    @abstractmethod
    def release(self) -> None: ...

    # --- media
    @abstractmethod
    def load(self, mrl: str, options: list[str] | None = None) -> None: ...

    @abstractmethod
    def play(self) -> None: ...

    @abstractmethod
    def pause(self) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    @abstractmethod
    def state(self) -> PlaybackState: ...

    @abstractmethod
    def time(self) -> int: ...

    @abstractmethod
    def set_time(self, ms: int) -> None: ...

    @abstractmethod
    def length(self) -> int: ...

    @abstractmethod
    def is_seekable(self) -> bool: ...

    @abstractmethod
    def next_frame(self) -> None: ...

    # --- audio
    @abstractmethod
    def volume(self) -> int: ...

    @abstractmethod
    def set_volume(self, percent: int) -> None: ...

    @abstractmethod
    def set_mute(self, muted: bool) -> None: ...

    @abstractmethod
    def set_rate(self, rate: float) -> None: ...

    @abstractmethod
    def rate(self) -> float: ...

    @abstractmethod
    def audio_tracks(self) -> list[Track]: ...

    @abstractmethod
    def audio_track(self) -> int: ...

    @abstractmethod
    def set_audio_track(self, track_id: int) -> None: ...

    @abstractmethod
    def set_audio_delay(self, ms: int) -> None: ...

    # --- subtitles
    @abstractmethod
    def subtitle_tracks(self) -> list[Track]: ...

    @abstractmethod
    def subtitle_track(self) -> int: ...

    @abstractmethod
    def set_subtitle_track(self, track_id: int) -> None: ...

    @abstractmethod
    def add_subtitle_file(self, mrl: str) -> bool: ...

    @abstractmethod
    def set_subtitle_delay(self, ms: int) -> None: ...

    # --- video
    @abstractmethod
    def has_video(self) -> bool: ...

    @abstractmethod
    def video_size(self) -> tuple[int, int]: ...

    @abstractmethod
    def set_aspect_ratio(self, ratio: str | None) -> None: ...

    @abstractmethod
    def set_crop(self, geometry: str | None) -> None: ...

    @abstractmethod
    def set_zoom(self, factor: float) -> None: ...

    @abstractmethod
    def set_deinterlace(self, mode: str | None) -> None: ...

    @abstractmethod
    def take_snapshot(self, path: str) -> bool: ...

    # --- info
    @abstractmethod
    def media_info(self) -> MediaInfo: ...

    @abstractmethod
    def engine_version(self) -> str: ...

    # --- optional capabilities (defaults keep older backends working)
    def rotation_options(self, degrees: int) -> list[str]:
        """Media options that apply a rotation (libVLC needs a reload for it)."""
        return []
