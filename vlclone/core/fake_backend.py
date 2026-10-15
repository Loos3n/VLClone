"""In-memory backend used by tests and ``--fake-backend`` UI demos.

Time only moves when :meth:`advance` is called, which makes controller
logic deterministic to test.
"""

from __future__ import annotations

from .backend import MediaBackend, MediaInfo, PlaybackState, StreamInfo, Track


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
        self.pending_rotation = 0
        self.rotation = 0

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
        self.rotation = self.pending_rotation
        self.loads.append(mrl)
        self._state = PlaybackState.IDLE
        self._time = 0
        self.audio_delay = 0
        self.subtitle_delay = 0

    def play(self) -> None:
        if self.mrl is None:
            return
        if self._state in (PlaybackState.ENDED, PlaybackState.STOPPED):
            self._time = 0
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

    def set_rotation(self, degrees: int) -> None:
        self.pending_rotation = degrees

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
