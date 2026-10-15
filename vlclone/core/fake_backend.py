"""In-memory backend used by tests and ``--fake-backend`` UI demos.

Time only moves when :meth:`advance` is called, which makes controller
logic deterministic to test.
"""

from __future__ import annotations

from .backend import (CaptureDevice, Chapter, MediaBackend, MediaInfo, PlaybackState, StreamInfo, TitleInfo,
                      Track)
from .effects import EngineFilters


class FakeBackend(MediaBackend):
    def __init__(self, default_length_ms: int = 60_000) -> None:
        self.default_length_ms = default_length_ms
        self.lengths: dict[str, int] = {}  # per-MRL override
        self.mrl: str | None = None
        self.options: list[str] = []
        self._state = PlaybackState.IDLE
        self._time = 0
        self._volume = 100
        self.muted = False
        self._rate = 1.0
        self.audio_delay = 0
        self.subtitle_delay = 0
        self._audio_tracks = [Track(-1, "Disable"), Track(1, "Track 1 - English"), Track(2, "Track 2 - Japanese")]
        self._audio = 1
        self._subs = [Track(-1, "Disable"), Track(3, "Track 1 - English")]
        self._sub = -1
        self.aspect_ratio: str | None = None
        self.crop: str | None = None
        self.zoom = 0.0
        self.deinterlace: str | None = None
        self.snapshots: list[str] = []
        self.loads: list[str] = []
        self.frames_stepped = 0
        self.handle: int | None = None
        self.pending_filters = EngineFilters()
        self.filters = EngineFilters()  # what the current media was loaded with
        self.equalizer: tuple[float, list[float]] | None = None
        self.stereo_mode = 1
        self.adjust: dict[str, float] | None = None
        self.normalization = (False, 2.0)
        self.normalization_supported = True
        # Phase 3 test hooks (all keyed by MRL)
        self.chapter_map: dict[str, list[Chapter]] = {}
        self.title_map: dict[str, list[TitleInfo]] = {}
        self.subitem_map: dict[str, list[tuple[str, str]]] = {}  # "playlist" URLs (YouTube…)
        self.fail_mrls: set[str] = set()   # go to ERROR when played
        self.hang_mrls: set[str] = set()   # stay OPENING forever
        self.navigations: list[str] = []
        self._title = 0
        self.devices = {"video": [CaptureDevice("Integrated Camera", "Integrated Camera")],
                        "audio": [CaptureDevice("", "Default microphone"),
                                  CaptureDevice("Microphone Array", "Microphone Array")]}

    # helpers for tests
    def advance(self, ms: int) -> None:
        if self._state is PlaybackState.PLAYING:
            self._time += int(ms * self._rate)
            if self._time >= self.length():
                self._time = self.length()
                self._state = PlaybackState.ENDED

    def set_video_output(self, handle: int) -> None:
        self.handle = handle

    def release(self) -> None:
        self._state = PlaybackState.IDLE

    def load(self, mrl: str, options: list[str] | None = None) -> None:
        self.mrl = mrl
        self.options = list(options or [])
        self.filters = self.pending_filters
        self.loads.append(mrl)
        self._title = 0
        self._state = PlaybackState.IDLE
        self._time = 0
        self.audio_delay = 0
        self.subtitle_delay = 0

    def play(self) -> None:
        if self.mrl is None:
            return
        if self._state in (PlaybackState.ENDED, PlaybackState.STOPPED):
            self._time = 0
        if self.mrl in self.fail_mrls:
            self._state = PlaybackState.ERROR
        elif self.mrl in self.hang_mrls:
            self._state = PlaybackState.OPENING
        elif self.mrl in self.subitem_map and self._state is not PlaybackState.PAUSED:
            self._state = PlaybackState.ENDED  # libVLC ends a playlist page after listing its items
        else:
            self._state = PlaybackState.PLAYING

    def pause(self) -> None:
        if self._state is PlaybackState.PLAYING:
            self._state = PlaybackState.PAUSED

    def stop(self) -> None:
        if self.mrl is not None:
            self._state = PlaybackState.STOPPED
            self._time = 0

    def state(self) -> PlaybackState:
        return self._state

    def time(self) -> int:
        return self._time if self.mrl else -1

    def set_time(self, ms: int) -> None:
        self._time = max(0, min(int(ms), self.length()))

    def length(self) -> int:
        if self.mrl is None:
            return -1
        return self.lengths.get(self.mrl, self.default_length_ms)

    def is_seekable(self) -> bool:
        return self.length() > 0

    def next_frame(self) -> None:
        self.frames_stepped += 1
        self._time += 40
        self._state = PlaybackState.PAUSED

    def volume(self) -> int:
        return self._volume

    def set_volume(self, percent: int) -> None:
        self._volume = int(percent)

    def set_mute(self, muted: bool) -> None:
        self.muted = muted

    def set_rate(self, rate: float) -> None:
        self._rate = rate

    def rate(self) -> float:
        return self._rate

    def audio_tracks(self) -> list[Track]:
        return list(self._audio_tracks)

    def audio_track(self) -> int:
        return self._audio

    def set_audio_track(self, track_id: int) -> None:
        self._audio = track_id

    def set_audio_delay(self, ms: int) -> None:
        self.audio_delay = ms

    def subtitle_tracks(self) -> list[Track]:
        return list(self._subs)

    def subtitle_track(self) -> int:
        return self._sub

    def set_subtitle_track(self, track_id: int) -> None:
        self._sub = track_id

    def add_subtitle_file(self, mrl: str) -> bool:
        new_id = max(t.id for t in self._subs) + 1
        self._subs.append(Track(new_id, mrl.rsplit("/", 1)[-1]))
        self._sub = new_id
        return True

    def set_subtitle_delay(self, ms: int) -> None:
        self.subtitle_delay = ms

    def has_video(self) -> bool:
        return self._state in (PlaybackState.PLAYING, PlaybackState.PAUSED)

    def video_size(self) -> tuple[int, int]:
        return (1920, 1080) if self.mrl else (0, 0)

    def set_aspect_ratio(self, ratio: str | None) -> None:
        self.aspect_ratio = ratio

    def set_crop(self, geometry: str | None) -> None:
        self.crop = geometry

    def set_zoom(self, factor: float) -> None:
        self.zoom = factor

    def set_deinterlace(self, mode: str | None) -> None:
        self.deinterlace = mode

    def take_snapshot(self, path: str) -> bool:
        self.snapshots.append(path)
        return True

    def set_engine_filters(self, filters: EngineFilters) -> None:
        self.pending_filters = filters

    @property
    def rotation(self) -> int:
        """Rotation implied by the loaded transform (for phase-1 tests)."""
        return {"90": 90, "180": 180, "270": 270}.get(self.filters.transform or "", 0)

    def set_equalizer(self, preamp: float | None, bands: list[float] | None = None) -> None:
        self.equalizer = None if preamp is None else (preamp, list(bands or []))

    def set_stereo_mode(self, mode: int) -> None:
        self.stereo_mode = mode

    def set_video_adjust(self, values: dict[str, float] | None) -> None:
        self.adjust = None if values is None else dict(values)

    @property
    def supports_normalization(self) -> bool:
        return self.normalization_supported

    def set_normalization(self, enabled: bool, level: float) -> None:
        self.normalization = (enabled, level)

    # --- titles / chapters / menus
    def titles(self) -> list[TitleInfo]:
        return list(self.title_map.get(self.mrl or "", []))

    def title(self) -> int:
        return self._title if self.titles() else -1

    def set_title(self, index: int) -> None:
        if 0 <= index < len(self.titles()):
            self._title = index
            self._time = 0

    def chapters(self) -> list[Chapter]:
        if self.titles() and self.titles()[self._title].is_menu:
            return []
        return list(self.chapter_map.get(self.mrl or "", []))

    def chapter(self) -> int:
        chapters = self.chapters()
        return max((c.index for c in chapters if c.start_ms <= self._time), default=-1)

    def set_chapter(self, index: int) -> None:
        chapters = self.chapters()
        if 0 <= index < len(chapters):
            self._time = chapters[index].start_ms

    def navigate(self, action: str) -> None:
        self.navigations.append(action)
        titles = self.titles()
        if action == "activate" and titles and titles[self._title].is_menu and len(titles) > 1:
            self.set_title(next(t.index for t in titles if not t.is_menu))

    def subitems(self) -> list[tuple[str, str]]:
        if self._state is PlaybackState.ENDED:
            return list(self.subitem_map.get(self.mrl or "", []))
        return []

    def capture_devices(self):
        return {k: list(v) for k, v in self.devices.items()}

    def media_info(self) -> MediaInfo:
        if not self.mrl:
            return MediaInfo()
        return MediaInfo(
            mrl=self.mrl,
            title="",
            duration_ms=self.length(),
            meta={"Title": self.mrl.rsplit("/", 1)[-1]},
            streams=[
                StreamInfo("video", "H264 - MPEG-4 AVC (part 10) (h264)", width=1920, height=1080, fps=23.976),
                StreamInfo("audio", "MPEG AAC Audio (mp4a)", language="English", channels=2, sample_rate=48000),
            ],
            input_bitrate_kbps=4500.0,
            demux_bitrate_kbps=4400.0,
        )

    def engine_version(self) -> str:
        return "fake 0.0"
