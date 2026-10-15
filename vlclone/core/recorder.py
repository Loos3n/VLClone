"""Recording what is playing (VLC's record button, Shift+R).

libVLC 3 has no public record call, so recording goes through the
conversion pipeline, in one of two ways:

* **Segment** (seekable media: files, discs, HTTP video): remember where
  recording started; on stop, copy that stretch of the source into a file
  (a remux job, no re-encoding). The start snaps to the keyframe before it.
* **Live** (streams without an end: IPTV, RTSP cameras, radio): a second
  libVLC player opens the same address and copies it to disk until stopped.

Cameras and screen capture are recorded from Open Media → Capture → Record
to file (a device cannot be opened twice).
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from .capture import is_capture_mrl
from .converter import ConvertJob, ConvertQueue, JobState
from .events import Signal
from .paths import default_videos_dir
from .transcode import ConvertSettings, safe_name

MIN_SEGMENT_MS = 500


def recording_name(title: str, ext: str, when: datetime | None = None) -> str:
    when = when or datetime.now()
    return f"{safe_name(Path(title).stem if '.' in title else title)} {when:%Y-%m-%d %H.%M.%S}.{ext}"


class Recorder:
    def __init__(self, controller, queue: ConvertQueue, settings,
                 live_preset: Callable[[], str] = lambda: "remux_mkv", clock: Callable[[], float] = time.monotonic,
                 now: Callable[[], datetime] = datetime.now) -> None:
        self.ctl = controller
        self.queue = queue
        self.settings = settings
        self.live_preset = live_preset  # remux_mkv, or remux_ts when Matroska is unavailable
        self.clock = clock
        self.now = now
        self.changed = Signal()  # (active: bool)
        self.saved = Signal()    # (ConvertJob) the job that writes the recording
        self.mode: str | None = None  # "segment" | "live" | None
        self._item = None
        self._start_ms = 0
        self._last_ms = 0
        self._started_at = 0.0
        self._job: ConvertJob | None = None

    @property
    def active(self) -> bool:
        return self.mode is not None

    def folder(self) -> str:
        return self.settings.record_dir or default_videos_dir()

    def elapsed_ms(self) -> int:
        if self.mode == "segment":
            return max(0, self._last_ms - self._start_ms)
        if self.mode == "live":
            return int((self.clock() - self._started_at) * 1000)
        return 0

    def toggle(self) -> str | None:
        if self.active:
            self.stop()
            return None
        return self.start()

    def start(self) -> str | None:
        """Start recording; returns a message when it cannot."""
        if self.active:
            return None
        ctl = self.ctl
        item = ctl.playlist.current
        if item is None or not ctl.has_media:
            return "Play something to record it"
        if is_capture_mrl(item.mrl):
            return "Record cameras and the screen from Open Media → Capture → Record to file"
        self._item = item
        self._started_at = self.clock()
        if ctl.backend.is_seekable() and ctl.length() > 0:
            self.mode = "segment"
            self._start_ms = self._last_ms = max(0, ctl.time())
        else:
            key = self.live_preset()
            from .transcode import PRESETS

            output = f"{self.folder()}/{recording_name(item.title, PRESETS[key].ext, self.now())}"
            self._job = self.queue.add(item.mrl, f"Recording: {item.title}", ConvertSettings(key, live=True),
                                       output, ctl.media_options(item), live=True)
            if self._job.state is JobState.FAILED:
                message = self._job.error
                self._job = None
                return message
            self.mode = "live"
            self.saved.emit(self._job)
        self.changed.emit(True)
        return None

    def stop(self) -> ConvertJob | None:
        """Stop recording; returns the job writing the file (None if nothing was recorded)."""
        if not self.active:
            return None
        job = None
        if self.mode == "segment":
            if self._is_current():
                self._last_ms = max(0, self.ctl.time())
            a, b = sorted((self._start_ms, self._last_ms))
            if b - a >= MIN_SEGMENT_MS:
                item = self._item
                output = f"{self.folder()}/{recording_name(item.title, 'mkv', self.now())}"
                settings = ConvertSettings("remux_mkv", start_ms=a, end_ms=b)
                job = self.queue.add(item.mrl, f"Recording: {item.title}", settings, output,
                                     [o for o in item.options if not o.startswith((":start-time", ":stop-time"))],
                                     duration_ms=self.ctl.length())
                self.saved.emit(job)
        elif self._job is not None:
            job = self._job
            self.queue.finish(job.id)
        self.mode, self._job, self._item = None, None, None
        self.changed.emit(False)
        return job

    def _is_current(self) -> bool:
        cur = self.ctl.playlist.current
        return cur is not None and self._item is not None and cur.uid == self._item.uid and self.ctl.has_media

    def tick(self) -> None:
        """Track the position; stop when the recorded media goes away."""
        if not self.active:
            return
        if not self._is_current():
            self.stop()
            return
        if self.mode == "segment":
            self._last_ms = max(0, self.ctl.time())
        elif self._job is not None and self._job.finished:  # the stream ended or failed by itself
            self.mode, self._job = None, None
            self.changed.emit(False)
