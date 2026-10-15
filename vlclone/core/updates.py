"""Update check against GitHub Releases: notify only, never install.

At most once a day (``is_due``), on a worker thread; like the library and
the converter, nothing calls back into the UI: the window polls
:meth:`UpdateChecker.poll` from its timer. Pre-releases and drafts (the
manual ``build-N`` CI builds) are never offered.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.request
from dataclasses import dataclass
from typing import Callable

from .. import GITHUB_REPO, __version__

CHECK_INTERVAL_S = 24 * 3600
TIMEOUT_S = 10.0

_VERSION_RE = re.compile(r"^\s*v?(\d+(?:\.\d+)*)(?:[-.]?((?:a|b|rc|alpha|beta|pre|dev)[.\-]?\d*[\w.\-]*))?"
                         r"(?:\+[\w.\-]+)?\s*$", re.IGNORECASE)


def parse_version(text: str) -> tuple | None:
    """``"v1.2.3"`` -> comparable key; ``None`` if it is not a version (e.g. ``build-15``).

    Pre-releases sort before the release: 1.0.0-beta.2 < 1.0.0-rc.1 < 1.0.0 < 1.0.1.
    Missing parts are zero: 1.2 == 1.2.0.
    """
    m = _VERSION_RE.match(text or "")
    if not m:
        return None
    nums = [int(n) for n in m.group(1).split(".")]
    while len(nums) > 1 and nums[-1] == 0:
        nums.pop()
    pre = m.group(2)
    if not pre:
        return (tuple(nums), 1, ())
    parts = re.findall(r"[a-z]+|\d+", pre.lower())
    order = {"dev": 0, "a": 1, "alpha": 1, "b": 2, "beta": 2, "pre": 3, "rc": 3}
    key = tuple((order.get(p, 0), 0) if not p.isdigit() else (99, int(p)) for p in parts)
    return (tuple(nums), 0, key)


def is_newer(candidate: str, current: str = __version__) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    return a is not None and b is not None and a > b


def is_due(last_check: float, now: float | None = None, interval_s: float = CHECK_INTERVAL_S) -> bool:
    """True when the last check is older than the interval (or in the future: clock changed)."""
    now = time.time() if now is None else now
    return last_check <= 0 or now - last_check >= interval_s or last_check > now + 3600


@dataclass(frozen=True)
class Release:
    version: str
    url: str
    name: str = ""
    notes: str = ""


def parse_release(data) -> Release | None:
    """A ``/releases/latest`` (or list entry) JSON object -> Release, skipping drafts and pre-releases."""
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        return None
    tag = data.get("tag_name")
    if not isinstance(tag, str) or parse_version(tag) is None:
        return None
    url = data.get("html_url") if isinstance(data.get("html_url"), str) else \
        f"https://github.com/{GITHUB_REPO}/releases/tag/{tag}"
    return Release(tag.lstrip("vV"), url, str(data.get("name") or ""), str(data.get("body") or ""))


def latest_release_url() -> str:
    # VLCLONE_UPDATE_URL points the check at a local server (tests, the Windows self-test).
    return os.environ.get("VLCLONE_UPDATE_URL") or f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


def fetch_json(url: str, timeout: float = TIMEOUT_S):
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": f"VLClone/{__version__}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read(1_000_000).decode("utf-8"))


@dataclass
class UpdateResult:
    release: Release | None = None  # set only when it is newer than this version
    error: str = ""
    checked: bool = False


def check_for_update(current: str = __version__, fetch: Callable[[str], object] = fetch_json,
                     url: str | None = None) -> UpdateResult:
    try:
        data = fetch(url or latest_release_url())
    except Exception as exc:  # offline, rate limited, 404 (no releases yet)…
        return UpdateResult(error=f"{type(exc).__name__}: {exc}")
    release = parse_release(data)
    if release is None:
        return UpdateResult(checked=True)
    return UpdateResult(release if is_newer(release.version, current) else None, checked=True)


class UpdateChecker:
    """Runs one check on a daemon thread; the UI calls :meth:`poll` until it returns a result."""

    def __init__(self, fetch: Callable[[str], object] = fetch_json, current: str = __version__) -> None:
        self.fetch = fetch
        self.current = current
        self._result: UpdateResult | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        if self.running:
            return False
        with self._lock:
            self._result = None
        self._thread = threading.Thread(target=self._run, name="update-check", daemon=True)
        self._thread.start()
        return True

    def _run(self) -> None:
        result = check_for_update(self.current, self.fetch)
        with self._lock:
            self._result = result

    def poll(self) -> UpdateResult | None:
        """The finished result, once (then ``None`` again)."""
        with self._lock:
            result, self._result = self._result, None
        return result

    def wait(self, timeout: float = 15.0) -> UpdateResult | None:
        if self._thread is not None:
            self._thread.join(timeout)
        return self.poll()
