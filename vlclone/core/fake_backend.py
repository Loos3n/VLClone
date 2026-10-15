"""In-memory backend used by tests and ``--fake-backend`` UI demos.

Time only moves when :meth:`advance` is called, which makes controller
logic deterministic to test.
"""

from __future__ import annotations

from .backend import (CaptureDevice, Chapter, MediaBackend, MediaInfo, PlaybackState, StreamInfo, TitleInfo,
                      Track)
from .converter import TranscodeEngine
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
        self.dead_mrls: set[str] = set()   # "end" without playing, like libVLC on HTTP 404
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
        elif self.mrl in self.dead_mrls:
            self._state = PlaybackState.ENDED
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
        from .media_sources import AUDIO_EXTENSIONS, extension

        streams = [StreamInfo("audio", "MPEG AAC Audio (mp4a)", language="English", channels=2, sample_rate=48000)]
        if extension(self.mrl) not in AUDIO_EXTENSIONS:  # audio files have no picture, like in libVLC
            streams.insert(0, StreamInfo("video", "H264 - MPEG-4 AVC (part 10) (h264)", width=1920, height=1080,
                                         fps=23.976))
        return MediaInfo(
            mrl=self.mrl,
            title="",
            duration_ms=self.length(),
            meta={"Title": self.mrl.rsplit("/", 1)[-1]},
            streams=streams,
            input_bitrate_kbps=4500.0,
            demux_bitrate_kbps=4400.0,
        )

    def engine_version(self) -> str:
        return "fake 0.0"


class FakeProbe:
    """Deterministic media-library probe (a ``MetadataProbe``) for tests and ``--fake-backend``.

    Metadata comes from ``metadata[path]`` when given, else from the file
    name ("Artist - Title"); thumbnails are tiny solid PNGs. Counts calls so
    tests can check that unchanged files are not probed again.
    """

    def __init__(self, metadata: dict | None = None, fail_thumbnails: set | None = None) -> None:
        self.metadata = metadata or {}
        self.fail_thumbnails = fail_thumbnails or set()
        self.probed: list[str] = []
        self.thumbnailed: list[str] = []
        self.closed = False

    def probe(self, path: str):
        from .library_scanner import FilenameProbe

        self.probed.append(path)
        meta = self.metadata.get(path)
        if meta is None:
            meta = FilenameProbe().probe(path)
            if meta.has_video:
                meta.width, meta.height, meta.duration_ms = 1280, 720, 90_000
            else:
                meta.duration_ms = 180_000
        return meta

    def thumbnail(self, path: str, out_path: str, width: int = 320, duration_ms: int = -1,
                  at_ms: int | None = None) -> bool:
        from .png import write_png

        self.thumbnailed.append(path if at_ms is None else (path, at_ms))
        if path in self.fail_thumbnails:
            return False
        if at_ms is None:
            write_png(out_path, 4, 2, bytes([200, 80, 20]) * 8)
            return True
        # Seek previews: a 16:9 gradient whose hue follows the time, so frames differ.
        w, h = max(16, width) // 2 * 2, max(16, width) * 9 // 16 // 2 * 2
        t = (at_ms // 1000) % 256
        rows = bytearray()
        for y in range(h):
            for x in range(w):
                rows += bytes(((t + x) % 256, (40 + y * 2) % 256, (200 - t) % 256))
        write_png(out_path, w, h, bytes(rows))
        return True

    def close(self) -> None:
        self.closed = True


class FakeTranscoder(TranscodeEngine):
    """Deterministic conversion engine (a ``TranscodeEngine``) for tests and ``--fake-backend``.

    A job writes a partial file when it starts and its finished content when
    it ends; time only moves with :meth:`advance` unless ``instant`` is set.
    Output files hold the resulting codecs as JSON (after a fake MP3 frame
    header for MP3), which :meth:`media_info` reads back.
    """

    CODEC_FOURCC = {"h264": "h264", "hevc": "hevc", "VP80": "VP80", "VP90": "VP90", "mp4a": "mp4a",
                    "mp3": "mpga", "vorb": "vorb", "opus": "Opus", "flac": "flac"}
    MP3_FRAME = b"\xff\xfb\x90\x00"

    def __init__(self, instant: bool = False, realtime_speed: float = 0.0) -> None:
        self.instant = instant
        #: >0: jobs advance by wall-clock time × speed on poll (for --fake-backend demos)
        self.realtime_speed = realtime_speed
        self.default_length_ms = 10_000
        self.lengths: dict[str, int] = {}
        self.sources: dict = {}  # mrl -> transcode.SourceInfo
        self.fail_mrls: set[str] = set()
        self.unsupported: set[str] = set()  # codec / mux names the "engine" lacks
        self.modules = {"screen": True, "xcb_screen": True}
        self.started: list[tuple[str, list[str]]] = []
        self.handles: list[dict] = []
        self.released = False

    # helpers for tests
    def advance(self, ms: int) -> None:
        for h in self.handles:
            if h["state"] == "running":
                self._advance_one(h, ms)

    @staticmethod
    def _sout(options: list[str]) -> str:
        return next((o[len(":sout="):] for o in options if o.startswith(":sout=")), "")

    def _finish(self, h: dict) -> None:
        h["state"] = "ended"
        self._write(h, final=True)

    def _codecs(self, mrl: str, options: list[str]) -> tuple[list[str], list[str]]:
        import re

        sout = self._sout(options)
        source = self.sources.get(mrl)
        src_v, src_a = (source.video, source.audio) if source else (["h264"], ["mp4a"])
        mux = (re.search(r"mux=(\w+)", sout) or [None, ""])[1]
        if mux in self.unsupported:
            return [], []
        if "#transcode{" not in sout:
            return list(src_v), list(src_a)
        v = (re.search(r"vcodec=(\w+)", sout) or [None, None])[1]
        a = (re.search(r"acodec=(\w+)", sout) or [None, None])[1]
        video = [] if not v or ":no-sout-video" in options or v in self.unsupported or not src_v \
            else [self.CODEC_FOURCC.get(v, v)]
        audio = [] if not a or a in self.unsupported or not src_a else [self.CODEC_FOURCC.get(a, a)]
        return video, audio

    def _write(self, h: dict, final: bool) -> None:
        import json

        if not h["dst"]:
            return
        video, audio = h["codecs"]
        if final and not video and not audio:
            open(h["dst"], "wb").close()  # what a missing encoder/muxer leaves: an empty file
            return
        head = self.MP3_FRAME if "mpga" in audio and not video else b""
        body = json.dumps({"video": video, "audio": audio, "final": final}).encode()
        with open(h["dst"], "wb") as fh:
            fh.write(head + body)

    # TranscodeEngine
    def start(self, mrl: str, options: list[str]):
        import re

        self.started.append((mrl, list(options)))
        sout = self._sout(options)
        m = re.search(r"dst='((?:[^'\\]|\\.)*)'", sout)
        dst = m.group(1).replace("\\'", "'").replace("\\\\", "\\") if m and "access=file" in sout else ""
        length = self.lengths.get(mrl, self.default_length_ms)
        start = next((int(float(o.split("=", 1)[1]) * 1000) for o in options if o.startswith(":start-time=")), 0)
        stop = next((int(float(o.split("=", 1)[1]) * 1000) for o in options if o.startswith(":stop-time=")), 0)
        live = mrl.split("://", 1)[0] in ("screen", "dshow", "v4l2", "udp", "rtsp") or length <= 0
        h = {"mrl": mrl, "dst": dst, "time": start, "end": stop or length, "length": length, "live": live,
             "state": "error" if mrl in self.fail_mrls else "running", "codecs": self._codecs(mrl, options)}
        self.handles.append(h)
        if h["state"] == "running":
            self._write(h, final=False)
            if self.instant and not live:
                self._finish(h)
        return h

    def poll(self, handle):
        import time as _time

        from .converter import EngineStatus

        if self.realtime_speed and handle["state"] == "running":
            now = _time.monotonic()
            last = handle.setdefault("wall", now)
            handle["wall"] = now
            self._advance_one(handle, int((now - last) * 1000 * self.realtime_speed))
        return EngineStatus(handle["state"], handle["time"], handle["length"])

    def _advance_one(self, h: dict, ms: int) -> None:
        h["time"] += ms
        if not h["live"] and h["time"] >= h["end"]:
            h["time"] = h["end"]
            self._finish(h)

    def stop(self, handle) -> None:
        if handle["state"] == "running":
            handle["state"] = "stopped"
            if handle["live"]:
                self._write(handle, final=True)  # a stopped live job keeps what it wrote

    def media_info(self, path_or_mrl: str):
        import json

        from .transcode import SourceInfo

        if path_or_mrl in self.sources:
            return self.sources[path_or_mrl]
        try:
            with open(path_or_mrl, "rb") as fh:
                data = fh.read()
            raw = json.loads(data[data.index(b"{"):])
            return SourceInfo(raw["video"], raw["audio"], 0, 1000)
        except (OSError, ValueError, KeyError):
            return SourceInfo()

    def has_module(self, name: str):
        return self.modules.get(name)

    def version(self) -> str:
        return "fake 1.0"

    def release(self) -> None:
        self.released = True
