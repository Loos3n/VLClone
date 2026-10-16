"""Conversion engine contract, the job queue and the preset probe.

``TranscodeEngine`` is the only contract with the converting engine
(``vlc_backend.VlcTranscoder`` runs a separate libVLC instance; tests use
``fake_backend.FakeTranscoder``). ``ConvertQueue`` is polled from the UI
timer like the player: no engine callbacks reach the UI.

Output safety: a job writes to ``<output>.part`` and only a verified
result is renamed to the final name. Cancel, failure, app shutdown and a
job removed while running all delete the part file.
"""

from __future__ import annotations

import os
import tempfile
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from itertools import count
from typing import Callable

from .events import Signal
from .transcode import (CONVERT_PRESETS, PRESETS, ConvertSettings, PresetSupport, SourceInfo, check_output,
                        media_options, part_path, unique_path)


@dataclass
class EngineStatus:
    state: str  # "opening" | "running" | "ended" | "error"
    time_ms: int = -1
    length_ms: int = -1


class TranscodeEngine(ABC):
    """Runs sout jobs on its own engine instance (never the player's)."""

    @abstractmethod
    def start(self, mrl: str, options: list[str]) -> object:
        """Start playing ``mrl`` with ``options`` (incl. ``:sout=``); returns a handle."""

    @abstractmethod
    def poll(self, handle: object) -> EngineStatus: ...

    @abstractmethod
    def stop(self, handle: object) -> None:
        """Stop and close the outputs (muxers write their index/trailer here)."""

    @abstractmethod
    def media_info(self, path_or_mrl: str) -> SourceInfo: ...

    def has_module(self, name: str) -> bool | None:
        """Whether the engine has a plugin (e.g. "screen"); None if it cannot tell."""
        return None

    def version(self) -> str:
        return ""

    def release(self) -> None:
        pass


def run_to_end(engine: TranscodeEngine, mrl: str, options: list[str], timeout_s: float = 30.0) -> str:
    """Run one job synchronously (probe, self-test). Returns "ended", "error" or "timeout"."""
    handle = engine.start(mrl, options)
    end = time.monotonic() + timeout_s
    state = "timeout"
    try:
        while time.monotonic() < end:
            st = engine.poll(handle).state
            if st in ("ended", "error"):
                state = st
                break
            time.sleep(0.02)
    finally:
        engine.stop(handle)
    return state


# ---------------------------------------------------------------------- jobs
class JobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


_job_ids = count(1)


@dataclass
class ConvertJob:
    source: str  # MRL
    title: str
    settings: ConvertSettings
    output: str
    source_options: list[str] = field(default_factory=list)  # e.g. capture device options
    live: bool = False  # runs until stopped (capture, screen, live streams)
    duration_ms: int = -1  # source length when known (progress before the engine reports it)
    id: int = field(default_factory=lambda: next(_job_ids))
    state: JobState = JobState.QUEUED
    progress: float = 0.0
    eta_s: float | None = None
    elapsed_s: float = 0.0
    error: str = ""
    started_at: float | None = None
    handle: object = None
    stopping: bool = False  # live job asked to finish
    length_known: bool = True  # False: the source reports no length (streams), progress is indeterminate
    media_time_ms: int = -1  # last position the engine reported
    part_size: int = 0  # last size of the part file

    @property
    def part(self) -> str:
        return part_path(self.output)

    @property
    def preset(self):
        return PRESETS[self.settings.preset]

    @property
    def finished(self) -> bool:
        return self.state in (JobState.DONE, JobState.FAILED, JobState.CANCELLED)


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


class ConvertQueue:
    """One file conversion at a time; live jobs (recordings) run alongside."""

    #: Treat a job that makes no progress for this long as stuck.
    STALL_S = 60.0

    def __init__(self, engine_factory: Callable[[], TranscodeEngine], clock: Callable[[], float] = time.monotonic,
                 verify: bool = True) -> None:
        self._factory = engine_factory
        self._engine: TranscodeEngine | None = None
        self.clock = clock
        self.verify = verify
        self.jobs: list[ConvertJob] = []
        self.generation = 0
        self.changed = Signal()  # (job) — emitted from tick() on the caller's thread
        self._last_progress: dict[int, tuple[float, float]] = {}

    @property
    def engine(self) -> TranscodeEngine:
        if self._engine is None:
            self._engine = self._factory()
        return self._engine

    def _bump(self, job: ConvertJob | None = None) -> None:
        self.generation += 1
        self.changed.emit(job)

    # ------------------------------------------------------------ editing
    def taken_outputs(self) -> set[str]:
        return {j.output for j in self.jobs if not j.finished}

    def add(self, source: str, title: str, settings: ConvertSettings, output: str,
            source_options: list[str] | None = None, live: bool = False, duration_ms: int = -1) -> ConvertJob:
        job = ConvertJob(source, title, settings, unique_path(output, self.taken_outputs()),
                         list(source_options or []), live, duration_ms)
        self.jobs.append(job)
        self._bump(job)
        if live:
            self._start(job)  # recordings start now, whatever else is converting
        return job

    def get(self, job_id: int) -> ConvertJob | None:
        return next((j for j in self.jobs if j.id == job_id), None)

    def cancel(self, job_id: int) -> None:
        """Stop and discard (the partial output is deleted)."""
        job = self.get(job_id)
        if job is None or job.finished:
            return
        if job.state is JobState.RUNNING:
            self._stop_engine(job)
        _remove(job.part)
        job.state = JobState.CANCELLED
        job.eta_s = None
        self._bump(job)

    def finish(self, job_id: int) -> None:
        """Stop a live job and keep what was recorded."""
        job = self.get(job_id)
        if job is None or job.state is not JobState.RUNNING:
            return
        self._stop_engine(job)
        self._finalize(job)

    def remove(self, job_id: int) -> None:
        job = self.get(job_id)
        if job is None:
            return
        if not job.finished:
            self.cancel(job_id)
        self.jobs.remove(job)
        self._bump()

    def retry(self, job_id: int) -> None:
        job = self.get(job_id)
        if job is None or job.state not in (JobState.FAILED, JobState.CANCELLED) or job.live:
            return
        job.output = unique_path(job.output, self.taken_outputs())
        job.state, job.progress, job.error, job.eta_s = JobState.QUEUED, 0.0, "", None
        self._bump(job)

    def clear_finished(self) -> None:
        before = len(self.jobs)
        self.jobs = [j for j in self.jobs if not j.finished]
        if len(self.jobs) != before:
            self._bump()

    @property
    def running(self) -> list[ConvertJob]:
        return [j for j in self.jobs if j.state is JobState.RUNNING]

    @property
    def pending(self) -> int:
        return sum(1 for j in self.jobs if j.state in (JobState.QUEUED, JobState.RUNNING))

    # ------------------------------------------------------------ running
    def _start(self, job: ConvertJob) -> None:
        os.makedirs(os.path.dirname(job.output) or ".", exist_ok=True)
        _remove(job.part)
        options = list(job.source_options) + media_options(job.preset, job.settings, job.part)
        try:
            job.handle = self.engine.start(job.source, options)
        except Exception as exc:  # engine missing, bad MRL…
            self._fail(job, f"Could not start: {exc}")
            return
        job.state = JobState.RUNNING
        job.started_at = self.clock()
        job.progress, job.error = 0.0, ""
        job.length_known, job.media_time_ms, job.part_size = True, -1, 0
        self._last_progress[job.id] = (0.0, job.started_at)
        self._bump(job)

    def _stop_engine(self, job: ConvertJob) -> None:
        if job.handle is not None:
            try:
                self.engine.stop(job.handle)
            except Exception:
                pass
            job.handle = None

    def _fail(self, job: ConvertJob, message: str) -> None:
        self._stop_engine(job)
        _remove(job.part)
        job.state, job.error, job.eta_s = JobState.FAILED, message, None
        self._bump(job)

    def _finalize(self, job: ConvertJob) -> None:
        """Verify ``part`` and move it into place (stop() was called, so muxers have closed)."""
        try:
            size = os.path.getsize(job.part)
        except OSError:
            size = 0
        if size <= 0:
            self._fail(job, "Nothing was written. The source may be unreadable, or this format is not "
                            "supported by the engine.")
            return
        if self.verify:
            try:
                info = self.engine.media_info(job.part)
            except Exception:
                info = SourceInfo()
            if not info.video and not info.audio:
                self._fail(job, "The output contains no audio or video (encoder or muxer unavailable).")
                return
            problem = None if job.preset.remux else check_output(job.preset, info, job.part, strict=False)
            if problem:
                self._fail(job, f"The result is not what the preset promises: {problem}.")
                return
        final = job.output
        if os.path.exists(final):  # something appeared there meanwhile: never overwrite
            final = unique_path(final)
        try:
            os.replace(job.part, final)
        except OSError as exc:
            self._fail(job, f"Could not save the file: {exc}")
            return
        job.output = final
        job.state, job.progress, job.eta_s = JobState.DONE, 1.0, 0.0
        self._bump(job)

    def tick(self) -> None:
        """Advance the queue; call a few times a second from the UI thread."""
        now = self.clock()
        if not any(j.state is JobState.RUNNING and not j.live for j in self.jobs):
            nxt = next((j for j in self.jobs if j.state is JobState.QUEUED), None)
            if nxt is not None:
                self._start(nxt)
        for job in self.running:
            try:
                st = self.engine.poll(job.handle)
            except Exception as exc:
                self._fail(job, f"Engine error: {exc}")
                continue
            job.elapsed_s = now - (job.started_at or now)
            if st.state == "error":
                self._fail(job, "libVLC could not convert this media (unreadable source, or an encoder "
                                "this format needs is missing).")
            elif st.state == "ended":
                self._stop_engine(job)
                self._finalize(job)
            else:
                self._update_progress(job, st, now)

    def _update_progress(self, job: ConvertJob, st: EngineStatus, now: float) -> None:
        if job.live:
            self._bump(job)  # elapsed time changes
            return
        # Any sign of work counts as progress for the stall check: the position moving on,
        # or the output file growing. Sources without a length (network streams) only have these.
        advanced = False
        if st.time_ms > job.media_time_ms:
            advanced = job.media_time_ms >= 0 or st.time_ms > 0
            job.media_time_ms = st.time_ms
        try:
            size = os.path.getsize(job.part)
        except OSError:
            size = 0
        if size > job.part_size:
            advanced = True
            job.part_size = size
        s = job.settings
        length = st.length_ms if st.length_ms > 0 else job.duration_ms
        end = s.end_ms if s.end_ms > 0 else length
        span = end - s.start_ms if end and end > 0 else 0
        job.length_known = span > 0
        if span > 0 and st.time_ms >= 0:
            progress = min(0.999, max(0.0, (st.time_ms - s.start_ms) / span))
            job.progress = max(job.progress, progress)
            elapsed = job.elapsed_s
            job.eta_s = elapsed * (1 - job.progress) / job.progress if job.progress > 0.02 else None
        else:
            job.eta_s = None
        if advanced:
            self._last_progress[job.id] = (job.progress, now)
        last = self._last_progress.get(job.id, (0.0, now))
        if now - last[1] > self.STALL_S:
            self._fail(job, f"No progress for {int(self.STALL_S)} s; the conversion was stopped.")
            return
        self._bump(job)

    def shutdown(self) -> None:
        """Cancel everything (app exit): no half-written files are left behind."""
        for job in list(self.jobs):
            if job.state is JobState.RUNNING and job.live:
                self.finish(job.id)  # a recording keeps what it has
            elif not job.finished:
                self.cancel(job.id)
        if self._engine is not None:
            self._engine.release()
            self._engine = None


# --------------------------------------------------------------------- probe
def probe_presets(engine: TranscodeEngine, keys: list[str] | None = None,
                  workdir: str | None = None) -> dict[str, PresetSupport]:
    """Convert a tiny generated clip with each preset and check the real output codecs."""
    from ..selftest_media import write_tone, write_y4m
    from .media_sources import path_to_mrl

    keys = keys or CONVERT_PRESETS
    result: dict[str, PresetSupport] = {}
    with tempfile.TemporaryDirectory(dir=workdir) as tmp:
        video = os.path.join(tmp, "probe.y4m")
        audio = os.path.join(tmp, "probe.wav")
        # ~2.4 s: much shorter and a stream copy can start muxing before the video appears.
        write_y4m(video, 160, 120, frames=60)
        write_tone(audio, 2.4)
        source = path_to_mrl(video)
        slave = f":input-slave={path_to_mrl(audio)}"
        h264_sample = None
        for key in [k for k in keys if not PRESETS[k].remux]:
            preset = PRESETS[key]
            out = os.path.join(tmp, f"{key}.{preset.ext}")
            state = run_to_end(engine, source, [slave] + media_options(preset, ConvertSettings(key), out), 20)
            if state != "ended" or not os.path.isfile(out) or os.path.getsize(out) == 0:
                result[key] = PresetSupport(False, "libVLC wrote nothing (encoder or muxer missing)"
                                            if state == "ended" else f"conversion {state}")
                continue
            problem = check_output(preset, engine.media_info(out), out)
            result[key] = PresetSupport(problem is None, problem or "")
            if problem is None and preset.vcodec == "h264" and preset.mux == "mp4":
                h264_sample = out
        for key in [k for k in keys if PRESETS[k].remux]:
            preset = PRESETS[key]
            if h264_sample is None:
                result[key] = PresetSupport(True, "")  # nothing to test with; stream copy needs no encoder
                continue
            out = os.path.join(tmp, f"{key}.{preset.ext}")
            state = run_to_end(engine, path_to_mrl(h264_sample), media_options(preset, ConvertSettings(key), out), 20)
            info = engine.media_info(out) if os.path.isfile(out) and os.path.getsize(out) else SourceInfo()
            ok = state == "ended" and "h264" in info.video
            result[key] = PresetSupport(ok, "" if ok else f"the {preset.mux} muxer is not available")
    return result


class PresetProber:
    """Runs :func:`probe_presets` once on a background thread; results cached by engine version."""

    def __init__(self, engine_factory: Callable[[], TranscodeEngine], store=None) -> None:
        from .transcode import CapsStore

        self._factory = engine_factory
        self.store = store if store is not None else CapsStore()
        self.caps: dict[str, PresetSupport] | None = None
        self.error = ""
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def ready(self) -> bool:
        with self._lock:
            return self.caps is not None

    def start(self) -> None:
        if self.ready or (self._thread and self._thread.is_alive()):
            return
        self._thread = threading.Thread(target=self._run, name="preset-probe", daemon=True)
        self._thread.start()

    def wait(self, timeout: float = 120.0) -> bool:
        if self._thread is not None:
            self._thread.join(timeout)
        return self.ready

    def _run(self) -> None:
        engine = None
        try:
            engine = self._factory()
            version = engine.version()
            caps = self.store.load(version) if version else None
            if caps is None:
                caps = probe_presets(engine)
                if version:
                    try:
                        self.store.save(version, caps)
                    except OSError:
                        pass
        except Exception as exc:  # leave every preset enabled rather than none
            self.error = f"Could not check presets: {exc}"
            caps = {k: PresetSupport(True, "") for k in CONVERT_PRESETS}
        finally:
            if engine is not None:
                try:
                    engine.release()
                except Exception:
                    pass
        with self._lock:
            self.caps = caps

    def support(self, key: str) -> PresetSupport | None:
        with self._lock:
            return None if self.caps is None else self.caps.get(key, PresetSupport(True))
