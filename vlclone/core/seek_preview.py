"""Thumbnails above the seek bar, made in the background.

Frames come from the media library's probe (its own libVLC instance and
``vmem`` decoding, see ``VlcMediaProbe.thumbnail``) and are cached as PNGs
next to the library thumbnails (``<data>/thumbnails/seek/<file hash>/``),
so a file is only decoded once per time slot, across launches.

The timeline is cut into at most ``max_frames`` slots. Hovering asks for a
slot (newest hover first); in between, a coarse set is filled in so the
first hover usually finds something. Like the library, nothing calls back
into Qt: the seek bar polls :meth:`frame_for`.
"""

from __future__ import annotations

import hashlib
import math
import os
import shutil
import threading
from pathlib import Path
from typing import Callable

from .library_scanner import MetadataProbe


class SeekPreviewService:
    def __init__(self, probe_factory: Callable[[], MetadataProbe], cache_dir: str | Path,
                 width: int = 240, max_frames: int = 120, prefill: int = 20, keep_media: int = 40) -> None:
        self.probe_factory = probe_factory
        self.cache_root = Path(cache_dir)
        self.width = width
        self.max_frames = max_frames
        self.prefill = prefill
        self.keep_media = keep_media
        self._lock = threading.Lock()
        self._wake = threading.Condition(self._lock)
        self._path: str | None = None
        self._dir: Path | None = None
        self._duration = 0
        self._step = 0
        self._wanted: list[int] = []      # hover requests, newest last
        self._prefill: list[int] = []
        self._done: set[int] = set()
        self._failed: set[int] = set()
        self._generation = 0
        self._stop = False
        self._busy = False
        self._thread: threading.Thread | None = None
        self.enabled = True

    # ------------------------------------------------------------- media
    def set_media(self, path: str | None, duration_ms: int = -1) -> None:
        """The file now playing (``None`` / a stream / unknown length: no previews)."""
        with self._lock:
            if path == self._path and duration_ms == self._duration:
                return
            self._generation += 1
            self._wanted.clear()
            self._prefill.clear()
            self._done.clear()
            self._failed.clear()
            self._path, self._dir, self._duration, self._step = None, None, 0, 0
            if not path or duration_ms <= 0 or not os.path.isfile(path):
                return
            self._path, self._duration = path, duration_ms
            self._step = max(1000, math.ceil(duration_ms / self.max_frames))
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                mtime = 0
            key = hashlib.sha1(f"{path}|{int(mtime)}".encode("utf-8", "surrogatepass")).hexdigest()
            self._dir = self.cache_root / key
            if self._dir.is_dir():
                for f in self._dir.glob("*.png"):
                    if f.stem.isdigit():
                        self._done.add(int(f.stem))
            n = min(self.prefill, self.max_frames)
            self._prefill = [self.slot(int(duration_ms * (i + 0.5) / n)) for i in range(n)][::-1]
            self._ensure_thread()
            self._wake.notify_all()

    @property
    def active(self) -> bool:
        return self.enabled and self._path is not None

    def slot(self, ms: int) -> int:
        """The cached time slot for a position (slot starts are multiples of the step)."""
        if not self._step:
            return 0
        ms = min(max(0, ms), max(0, self._duration - 1))
        return (ms // self._step) * self._step + self._step // 2

    def frame_for(self, ms: int) -> str | None:
        """The PNG for ``ms`` if made; otherwise queue it and return the nearest ready one (or None)."""
        with self._lock:
            if not self.enabled or self._dir is None:
                return None
            want = self.slot(ms)
            if want in self._done:
                return str(self._dir / f"{want}.png")
            if want not in self._failed:
                if want in self._wanted:
                    self._wanted.remove(want)
                self._wanted.append(want)
                del self._wanted[:-4]  # only the last few hovers matter
                self._wake.notify_all()
            if not self._done:
                return None
            nearest = min(self._done, key=lambda s: abs(s - want))
            # Only a close neighbour is better than nothing (otherwise the picture lies).
            return str(self._dir / f"{nearest}.png") if abs(nearest - want) <= 3 * self._step else None

    def is_exact(self, ms: int) -> bool:
        with self._lock:
            return self.slot(ms) in self._done

    # ------------------------------------------------------------- worker
    def _ensure_thread(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._stop = False
            self._thread = threading.Thread(target=self._run, name="seek-previews", daemon=True)
            self._thread.start()

    def _next_job(self):
        while not self._stop:
            if self._path and self.enabled:
                while self._wanted:
                    s = self._wanted.pop()
                    if s not in self._done and s not in self._failed:
                        return s, self._path, self._dir, self._generation, self._duration
                while self._prefill:
                    s = self._prefill.pop()
                    if s not in self._done and s not in self._failed:
                        return s, self._path, self._dir, self._generation, self._duration
            self._wake.wait(1.0)
        return None

    def _run(self) -> None:
        probe = None
        try:
            while True:
                with self._lock:
                    self._busy = False
                    job = self._next_job()
                    self._busy = job is not None
                if job is None:
                    return
                slot, path, folder, generation, duration = job
                if probe is None:
                    try:
                        probe = self.probe_factory()
                    except Exception:
                        with self._lock:
                            self.enabled = False
                        return
                out = folder / f"{slot}.png"
                try:
                    folder.mkdir(parents=True, exist_ok=True)
                    ok = probe.thumbnail(path, str(out), self.width, duration, at_ms=slot)
                except Exception:
                    ok = False
                with self._lock:
                    if generation != self._generation:
                        continue
                    (self._done if ok and out.is_file() else self._failed).add(slot)
        finally:
            if probe is not None:
                try:
                    probe.close()
                except Exception:
                    pass
            self._prune()

    def wait_idle(self, timeout: float = 10.0) -> bool:
        """Tests: until every queued slot is made."""
        import time

        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self._lock:
                if not self._wanted and not self._prefill and not self._busy:
                    return True
            time.sleep(0.02)
        return False

    def _prune(self) -> None:
        """Keep the previews of the most recent files only."""
        try:
            dirs = sorted((d for d in self.cache_root.iterdir() if d.is_dir()),
                          key=lambda d: d.stat().st_mtime, reverse=True)
        except OSError:
            return
        for d in dirs[self.keep_media:]:
            shutil.rmtree(d, ignore_errors=True)

    def shutdown(self, timeout: float = 3.0) -> None:
        with self._lock:
            self._stop = True
            self._wake.notify_all()
        if self._thread is not None:
            self._thread.join(timeout)
