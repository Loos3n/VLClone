"""Background scanning for the media library: indexing, folder watching, thumbnails.

All work happens on one worker thread at a time. Nothing calls back into
the UI: the UI polls :meth:`LibraryService.tick` / :attr:`status` from its
timer and reloads its views when :attr:`LibraryService.generation` changes,
the same polling design the player uses for libVLC.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from .library import FOLDER_KINDS, LibraryDB, LibraryItem, MediaMeta
from .media_sources import AUDIO_EXTENSIONS, VIDEO_EXTENSIONS, extension
from .paths import data_dir

# .ogg is usually audio in a music folder (phase 1 lists it as video for drops).
FOLDER_EXTENSIONS = {"video": VIDEO_EXTENSIONS, "music": AUDIO_EXTENSIONS | {"ogg"}}
ITEM_KIND = {"video": "video", "music": "audio"}
_SKIP_DIRS = {"$recycle.bin", "system volume information", "@eadir", "node_modules"}


class MetadataProbe(ABC):
    """Reads metadata and makes thumbnails. Created and used on the worker thread."""

    @abstractmethod
    def probe(self, path: str) -> MediaMeta: ...

    def thumbnail(self, path: str, out_path: str, width: int = 320, duration_ms: int = -1) -> bool:
        return False

    def close(self) -> None:
        pass


class FilenameProbe(MetadataProbe):
    """Fallback without libVLC: "Artist - Title" from the file name, album from the folder."""

    def probe(self, path: str) -> MediaMeta:
        stem = Path(path).stem
        artist, sep, title = stem.partition(" - ")
        meta = MediaMeta(title=title if sep else stem, artist=artist if sep else "")
        if extension(path) in VIDEO_EXTENSIONS - {"ogg"}:
            meta.has_video = True
        else:
            meta.album = Path(path).parent.name
        return meta


@dataclass(frozen=True)
class ScanStatus:
    state: str = "idle"  # "idle" | "checking" | "scanning" | "thumbnails"
    folder: str = ""
    current: str = ""
    done: int = 0
    added: int = 0
    removed: int = 0
    last_scan: float = 0.0
    error: str = ""

    @property
    def busy(self) -> bool:
        return self.state != "idle"

    @property
    def text(self) -> str:
        if self.state == "checking":
            return "Checking folders…"
        if self.state == "scanning":
            return f"Scanning… {self.done} files" + (f" · {Path(self.current).name}" if self.current else "")
        if self.state == "thumbnails":
            return f"Making thumbnails… {self.done}"
        return self.error


def thumb_name(path: str, mtime: float) -> str:
    return hashlib.sha1(f"{path}|{int(mtime)}".encode("utf-8", "surrogatepass")).hexdigest() + ".png"


def _walk_media(folder: str, exts: set[str], cancel: threading.Event):
    for dirpath, dirnames, filenames in os.walk(folder):
        if cancel.is_set():
            return
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d.lower() not in _SKIP_DIRS]
        for name in filenames:
            if not name.startswith(".") and extension(name) in exts:
                yield os.path.join(dirpath, name)


def folder_signature(folder: str, cancel: threading.Event | None = None) -> tuple:
    """Cheap change detector: every directory's mtime (adding/removing/renaming files changes it)."""
    cancel = cancel or threading.Event()
    sig = []
    for dirpath, dirnames, _ in os.walk(folder):
        if cancel.is_set():
            break
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d.lower() not in _SKIP_DIRS]
        try:
            sig.append((dirpath, os.stat(dirpath).st_mtime_ns))
        except OSError:
            pass
    return tuple(sorted(sig))


def scan_folder(db: LibraryDB, folder: str, kind: str, probe: MetadataProbe, cancel: threading.Event,
                progress: Callable[[str, bool], None] = lambda path, changed: None) -> tuple[int, int]:
    """Incrementally index one folder. Returns (added or updated, removed).

    Files whose size and modification time are unchanged are not probed again.
    """
    known = db.known_files(folder)
    seen: set[str] = set()
    changed = 0
    for path in _walk_media(folder, FOLDER_EXTENSIONS[kind], cancel):
        try:
            st = os.stat(path)
        except OSError:
            continue
        seen.add(path)
        previous = known.get(path)
        if previous and previous[0] == st.st_size and int(previous[1]) == int(st.st_mtime):
            progress(path, False)
            continue
        try:
            meta = probe.probe(path)
        except Exception:
            meta = MediaMeta()
        db.upsert(path, folder, ITEM_KIND[kind], st.st_size, st.st_mtime, meta)
        changed += 1
        progress(path, True)
        if cancel.is_set():
            return changed, 0
    if cancel.is_set():
        return changed, 0  # an interrupted walk must not delete what it did not reach
    removed = [p for p in known if p not in seen]
    db.remove_paths(removed)
    return changed, len(removed)


class LibraryService:
    """Owns the database and the background worker; driven by :meth:`tick` from the UI."""

    THUMB_WIDTH = 320

    def __init__(self, db: LibraryDB, probe_factory: Callable[[], MetadataProbe] = FilenameProbe,
                 thumbs_dir: str | Path | None = None, watch: bool = True, thumbnails: bool = True,
                 watch_interval_s: float = 30.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.db = db
        self.probe_factory = probe_factory
        self.thumbs_dir = Path(thumbs_dir) if thumbs_dir else data_dir() / "thumbnails"
        self.watch = watch
        self.thumbnails = thumbnails
        self.watch_interval_s = watch_interval_s
        self.clock = clock
        self._lock = threading.Lock()
        self._status = ScanStatus()
        self._generation = 0
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._pending: str | None = None  # "scan" or "check" queued while busy
        self._signatures: dict[str, tuple] = {}
        self._last_check = clock()

    # ------------------------------------------------------------- status
    @property
    def status(self) -> ScanStatus:
        with self._lock:
            return self._status

    @property
    def generation(self) -> int:
        """Bumped whenever the library content changes (the UI reloads then)."""
        with self._lock:
            return self._generation

    def _bump(self) -> None:
        with self._lock:
            self._generation += 1

    def _set_status(self, **changes) -> None:
        with self._lock:
            self._status = replace(self._status, **changes)

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------ folders
    def folders(self) -> list[tuple[str, str]]:
        return self.db.folders()

    def add_folder(self, path: str, kind: str) -> str | None:
        error = self.db.add_folder(path, kind)
        if error is None:
            self._bump()
            self.rescan()
        return error

    def remove_folder(self, path: str) -> None:
        self.db.remove_folder(path)
        self._signatures.pop(path, None)
        self._bump()
        if self.busy:
            self.rescan()  # the running scan may still be writing that folder's files

    # ---------------------------------------------------------------- jobs
    def rescan(self) -> None:
        """Index every watched folder now (incremental), then make missing thumbnails."""
        self._start("scan")

    def _start(self, job: str) -> None:
        if self.busy:
            if job == "scan" or self._pending is None:
                self._pending = job
            return
        self._cancel = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(job, self._cancel), name="library-scan",
                                        daemon=True)
        self._thread.start()

    def tick(self) -> None:
        """Call regularly from the UI thread: starts queued jobs and periodic folder checks."""
        if self.busy:
            return
        if self._pending:
            job, self._pending = self._pending, None
            self._start(job)
            return
        if self.watch and self.clock() - self._last_check >= self.watch_interval_s and self.db.folders():
            self._last_check = self.clock()
            self._start("check")

    def wait(self, timeout: float = 30.0) -> bool:
        """Block until idle, running queued jobs too (tests, shutdown)."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            thread = self._thread
            if thread is not None and thread.is_alive():
                thread.join(max(0.0, min(0.2, end - time.monotonic())))
                continue
            if self._pending:
                self.tick()
                continue
            return True
        return False

    def shutdown(self, timeout: float = 5.0) -> None:
        self._pending = None
        self._cancel.set()
        if self._thread is not None:
            self._thread.join(timeout)
        self.db.close()

    def _run(self, job: str, cancel: threading.Event) -> None:
        probe = None
        try:
            folders = self.db.folders()
            if job == "check":
                self._set_status(state="checking", error="")
                changed = [f for f, _ in folders
                           if os.path.isdir(f) and self._signatures.get(f) != folder_signature(f, cancel)]
                if not changed or cancel.is_set():
                    return
                folders = [(f, k) for f, k in folders if f in changed]
            probe = self.probe_factory()
            self._scan(folders, probe, cancel)
            if self.thumbnails and not cancel.is_set():
                self._make_thumbnails(probe, cancel)
        except Exception as exc:  # keep the app alive; show the problem in the status
            self._set_status(error=f"Library scan failed: {exc}")
        finally:
            if probe is not None:
                try:
                    probe.close()
                except Exception:
                    pass
            self._set_status(state="idle", current="", folder="", last_scan=time.time())
            self.db.close()  # this thread's connection

    def _scan(self, folders: list[tuple[str, str]], probe: MetadataProbe, cancel: threading.Event) -> None:
        done = 0
        added = removed = 0
        self._set_status(state="scanning", done=0, added=0, removed=0, error="")
        last_bump = time.monotonic()

        def progress(path: str, changed: bool) -> None:
            nonlocal done, last_bump
            done += 1
            self._set_status(current=path, done=done)
            if changed and time.monotonic() - last_bump > 0.75:  # let big scans show up gradually
                last_bump = time.monotonic()
                self._bump()

        for folder, kind in folders:
            if cancel.is_set():
                break
            if kind not in FOLDER_KINDS:
                continue
            if not os.path.isdir(folder):
                self._set_status(error=f"Folder not found: {folder}")
                continue
            self._set_status(folder=folder)
            signature = folder_signature(folder, cancel)
            n_added, n_removed = scan_folder(self.db, folder, kind, probe, cancel, progress)
            added += n_added
            removed += n_removed
            if not cancel.is_set():
                self._signatures[folder] = signature
        self._set_status(added=added, removed=removed)
        self._bump()

    def _make_thumbnails(self, probe: MetadataProbe, cancel: threading.Event) -> None:
        todo = self.db.missing_thumbs()
        if not todo:
            return
        self._set_status(state="thumbnails", done=0, current="")
        self.thumbs_dir.mkdir(parents=True, exist_ok=True)
        last_bump = time.monotonic()
        for n, (path, duration, mtime) in enumerate(todo, 1):
            if cancel.is_set():
                return
            out = self.thumbs_dir / thumb_name(path, mtime)
            ok = out.is_file()
            if not ok:
                try:
                    ok = probe.thumbnail(path, str(out), self.THUMB_WIDTH, duration)
                except Exception:
                    ok = False
            self.db.set_thumb(path, str(out) if ok and out.is_file() else "")
            self._set_status(done=n, current=path)
            if time.monotonic() - last_bump > 1.0:
                last_bump = time.monotonic()
                self._bump()
        self._bump()

    # ------------------------------------------------------------- queries
    def videos(self, search: str = "", sort: str = "title") -> list[LibraryItem]:
        return self.db.items("video", search, sort)

    def music(self, search: str = ""):
        return self.db.albums(search)

    def recent(self, search: str = "") -> list[LibraryItem]:
        return self.db.recent(search=search)

    def continue_watching(self, resume_entries: dict, search: str = "") -> list[LibraryItem]:
        return self.db.continue_watching(resume_entries, search)

    def record_play(self, mrl: str, title: str) -> None:
        self.db.record_play(mrl, title)
        self._bump()

    def forget_history(self, mrl: str | None = None) -> None:
        self.db.forget_history(mrl)
        self._bump()
