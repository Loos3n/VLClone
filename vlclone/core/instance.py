"""Single-instance handoff: the message format and the batching (no Qt here).

A second launch (Explorer "Open", "Play with VLClone", "Add to VLClone
playlist", a shortcut) sends its inputs to the running window and exits.
The transport (``QLocalServer``/``QLocalSocket`` plus a ``QLockFile``) lives
in ``ui/instance.py``. Messages are one JSON object per line:

    {"v": 1, "cmd": "open", "inputs": ["D:/a.mkv"], "mode": "play", "cwd": "D:/"}

``mode`` is "play", "enqueue" or null (= the running instance's setting).
An open with no inputs just brings the window to the front.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import Callable

from .. import APP_NAME

PROTOCOL = 1
MAX_MESSAGE = 4 * 1024 * 1024  # bytes; a few thousand paths is far below this
MODES = ("play", "enqueue")


@dataclass
class HandoffMessage:
    inputs: list[str] = field(default_factory=list)
    mode: str | None = None
    cwd: str = ""
    cmd: str = "open"

    def encode(self) -> bytes:
        data = {"v": PROTOCOL, "cmd": self.cmd, "inputs": list(self.inputs), "mode": self.mode, "cwd": self.cwd}
        return json.dumps(data, ensure_ascii=False).encode("utf-8") + b"\n"

    @classmethod
    def from_json(cls, obj) -> "HandoffMessage | None":
        if not isinstance(obj, dict) or not isinstance(obj.get("cmd"), str):
            return None
        inputs = obj.get("inputs") or []
        if not isinstance(inputs, list) or not all(isinstance(i, str) for i in inputs):
            return None
        mode = obj.get("mode")
        cwd = obj.get("cwd")
        return cls([i for i in inputs if i.strip()], mode if mode in MODES else None,
                   cwd if isinstance(cwd, str) else "", obj["cmd"])

    def resolved_inputs(self) -> list[str]:
        """Relative paths are taken relative to the sender's working directory; URLs pass through."""
        out = []
        for item in self.inputs:
            if "://" in item or not self.cwd or os.path.isabs(item) or item.startswith("\\\\"):
                out.append(item)
            else:
                out.append(os.path.normpath(os.path.join(self.cwd, item)))
        return out


def decode_stream(buffer: bytes) -> tuple[list[HandoffMessage], bytes]:
    """Split complete lines off ``buffer``; returns (messages, unparsed rest). Garbage lines are dropped."""
    messages: list[HandoffMessage] = []
    while b"\n" in buffer:
        line, buffer = buffer.split(b"\n", 1)
        if not line.strip():
            continue
        try:
            msg = HandoffMessage.from_json(json.loads(line.decode("utf-8")))
        except (UnicodeDecodeError, ValueError):
            msg = None
        if msg is not None:
            messages.append(msg)
    if len(buffer) > MAX_MESSAGE:
        buffer = b""  # a peer that never sends a newline cannot grow memory forever
    return messages, buffer


def server_name(config_dir: str, user: str | None = None) -> str:
    """Per user and per config folder, so portable copies with their own config stay separate."""
    if user is None:
        try:
            user = getpass.getuser()
        except Exception:
            user = os.environ.get("USERNAME") or os.environ.get("USER") or "user"
    digest = hashlib.sha1(f"{user}|{os.path.normcase(os.path.abspath(config_dir))}".encode(
        "utf-8", "surrogatepass")).hexdigest()[:16]
    return f"{APP_NAME}-{digest}"


@dataclass
class OpenRequest:
    inputs: list[str]
    play: bool


class HandoffBatcher:
    """Collects messages that arrive close together into as few opens as possible.

    Selecting 20 files in Explorer and pressing Enter starts 20 processes, one
    file each. Without batching, "Play now" would restart playback 20 times;
    batched, the 20 files are added in one go and the first one plays.
    """

    def __init__(self, default_mode: str = "play", window_s: float = 0.5,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.default_mode = default_mode
        self.window_s = window_s
        self.clock = clock
        self._pending: list[HandoffMessage] = []
        self._last = 0.0

    def add(self, msg: HandoffMessage) -> None:
        self._pending.append(msg)
        self._last = self.clock()

    @property
    def pending(self) -> bool:
        return bool(self._pending)

    def take(self, force: bool = False) -> list[OpenRequest] | None:
        """The batch once no message arrived for ``window_s`` (or ``force``); None while waiting."""
        if not self._pending or (not force and self.clock() - self._last < self.window_s):
            return None
        msgs, self._pending = self._pending, []
        requests: list[OpenRequest] = []
        for msg in msgs:
            inputs = msg.resolved_inputs()
            if not inputs:
                continue
            play = (msg.mode or self.default_mode) == "play"
            if requests and requests[-1].play == play:
                requests[-1].inputs.extend(inputs)
            else:
                requests.append(OpenRequest(list(inputs), play))
        # Only one group may start playback, else the last "play" wins over the files before it:
        # the first playing group plays, later "play" groups are added after it.
        played = False
        for req in requests:
            if req.play and played:
                req.play = False
            played = played or req.play
        return requests
