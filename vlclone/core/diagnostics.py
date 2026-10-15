"""Logging, crash detection and crash reports (no Qt).

* Rotating log files in ``<data>/logs`` (``vlclone.log``, 1 MB x 5).
* Every running process leaves a ``session-<pid>.json`` marker, removed on a
  clean exit. A marker whose process is gone means that session crashed:
  the next launch turns it into ``crash-<time>.log`` (the native fault
  trace from ``faulthandler`` plus the end of the log) and the UI offers
  "Open log folder".
* Uncaught Python exceptions are logged with their traceback.
"""

from __future__ import annotations

import faulthandler
import json
import logging
import logging.handlers
import os
import platform
import sys
import threading
import time
import traceback
from pathlib import Path

from .. import APP_NAME, __version__
from .paths import data_dir

LOG_NAME = "vlclone.log"
LOG_BYTES = 1_000_000
LOG_BACKUPS = 5
KEEP_CRASH_REPORTS = 10

log = logging.getLogger("vlclone")


def logs_dir() -> Path:
    path = data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def setup_logging(folder: Path | None = None, level: int = logging.INFO) -> Path:
    folder = folder or logs_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / LOG_NAME
    for h in list(log.handlers):
        if getattr(h, "_vlclone", False):
            log.removeHandler(h)
            h.close()
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=LOG_BYTES, backupCount=LOG_BACKUPS,
                                                   encoding="utf-8", delay=True)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    handler._vlclone = True
    log.addHandler(handler)
    if sys.stderr is not None and os.environ.get("VLCLONE_LOG_STDERR"):
        stream = logging.StreamHandler()
        stream._vlclone = True
        log.addHandler(stream)
    log.setLevel(level)
    log.propagate = False
    return path


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # access denied: it exists
        try:
            code = wintypes.DWORD()
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return bool(ok) and code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class CrashGuard:
    """One per process: detects crashed sessions and arms the fault handler for this one."""

    def __init__(self, folder: Path | None = None, pid: int | None = None) -> None:
        self.folder = folder or logs_dir()
        self.folder.mkdir(parents=True, exist_ok=True)
        self.pid = pid or os.getpid()
        self.marker = self.folder / f"session-{self.pid}.json"
        self.fault_path = self.folder / f"fault-{self.pid}.log"
        self._fault_file = None
        self._old_hooks = None

    # ------------------------------------------------------------ previous runs
    def collect_crashes(self) -> list[Path]:
        """Crash reports for sessions that did not end cleanly (their markers are consumed)."""
        reports = []
        for marker in sorted(self.folder.glob("session-*.json")):
            try:
                pid = int(marker.stem.split("-", 1)[1])
            except ValueError:
                pid = -1
            if pid == self.pid or pid_alive(pid):
                continue
            try:
                info = json.loads(marker.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                info = {}
            reports.append(self._write_report(pid, info))
            marker.unlink(missing_ok=True)
            (self.folder / f"fault-{pid}.log").unlink(missing_ok=True)
        self._prune_reports()
        return reports

    def _write_report(self, pid: int, info: dict) -> Path:
        fault = self.folder / f"fault-{pid}.log"
        try:
            fault_text = fault.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            fault_text = ""
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = self.folder / f"crash-{stamp}-{pid}.log"
        lines = [f"{APP_NAME} crash report",
                 f"Session: pid {pid}, started {info.get('started', '?')}, version {info.get('version', '?')}",
                 f"Detected: {time.strftime('%Y-%m-%d %H:%M:%S')} by version {__version__}",
                 f"System: {platform.platform()}, Python {platform.python_version()}",
                 ""]
        if info.get("exception"):
            lines += ["Unhandled exception:", info["exception"], ""]
        lines += ["Native fault trace (faulthandler):", fault_text or "(none: the process was killed or "
                  "exited without a Python-level fault)", "", "End of the log:", self._log_tail()]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        log.error("previous session (pid %s) did not exit cleanly; report %s", pid, path.name)
        return path

    def _log_tail(self, lines: int = 80) -> str:
        try:
            with open(self.folder / LOG_NAME, encoding="utf-8", errors="replace") as fh:
                return "".join(fh.readlines()[-lines:])
        except OSError:
            return "(no log)"

    def _prune_reports(self) -> None:
        reports = sorted(self.folder.glob("crash-*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in reports[KEEP_CRASH_REPORTS:]:
            old.unlink(missing_ok=True)

    # ------------------------------------------------------------ this run
    def start(self) -> None:
        """Mark this session as running and route native faults and uncaught exceptions to the logs."""
        self._write_marker({})
        try:
            self._fault_file = open(self.fault_path, "w", encoding="utf-8")
            faulthandler.enable(self._fault_file, all_threads=True)
        except (OSError, RuntimeError, ValueError):
            self._fault_file = None
        self._old_hooks = (sys.excepthook, threading.excepthook)
        sys.excepthook = self._excepthook
        threading.excepthook = lambda args: self._log_exception(args.exc_type, args.exc_value, args.exc_traceback,
                                                                 f"thread {args.thread.name if args.thread else '?'}")

    def _write_marker(self, extra: dict) -> None:
        data = {"pid": self.pid, "version": __version__, "started": time.strftime("%Y-%m-%d %H:%M:%S"), **extra}
        try:
            self.marker.write_text(json.dumps(data), encoding="utf-8")
        except OSError:
            pass

    def _log_exception(self, exc_type, exc, tb, where: str = "main thread") -> str:
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        log.error("uncaught exception in %s:\n%s", where, text.rstrip())
        return text

    def _excepthook(self, exc_type, exc, tb) -> None:
        # PySide6 sends exceptions raised in slots here and keeps running: log, don't die.
        self._log_exception(exc_type, exc, tb)

    def fatal(self, exc: BaseException) -> None:
        """An exception escaped main(): keep the marker (with the traceback) so the next launch reports it."""
        text = self._log_exception(type(exc), exc, exc.__traceback__)
        self._write_marker({"exception": text})

    def stop(self) -> None:
        """Clean exit: no crash to report next time."""
        if self._old_hooks:
            sys.excepthook, threading.excepthook = self._old_hooks
            self._old_hooks = None
        if self._fault_file is not None:
            try:
                faulthandler.disable()
                if sys.stderr is not None:
                    faulthandler.enable(sys.stderr)
                self._fault_file.close()
            except (OSError, ValueError):
                pass
            self._fault_file = None
        self.marker.unlink(missing_ok=True)
        self.fault_path.unlink(missing_ok=True)
