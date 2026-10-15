"""Stream the current media over HTTP on the local network (VLC's "Stream…", kept basic).

A separate libVLC player serves ``http://<this computer>:<port>/stream``
as MPEG-TS, either copying the streams (no re-encode) or re-encoding to
H.264 + AAC for players that cannot decode the original. libVLC paces HTTP
output in real time, so viewers watch it like a live channel.
"""

from __future__ import annotations

import socket
from typing import Callable

from .converter import TranscodeEngine

STREAM_MODES: dict[str, str] = {
    "copy": "Original streams (no re-encode)",
    "h264": "H.264 + AAC (re-encode, most compatible)",
}
STREAM_PATH = "stream"
DEFAULT_PORT = 8080


def stream_sout(mode: str, port: int, path: str = STREAM_PATH) -> str:
    std = f"std{{access=http,mux=ts,dst=:{int(port)}/{path}}}"
    if mode == "h264":
        return ("#transcode{vcodec=h264,vb=2500,venc=x264{tune=zerolatency},acodec=mp4a,ab=160,channels=2,"
                "samplerate=44100}:" + std)
    return "#" + std


def port_available(port: int) -> bool:
    """True if nothing listens on ``port`` (all interfaces)."""
    if not 1 <= port <= 65535:
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("0.0.0.0", port))
        except OSError:
            return False
    return True


def lan_address() -> str:
    """This computer's address on the local network (no traffic is sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("10.255.255.255", 1))  # picks the outgoing interface
            address = sock.getsockname()[0]
            if address and not address.startswith("0."):
                return address
    except OSError:
        pass
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return "127.0.0.1"


def stream_url(port: int, host: str | None = None, path: str = STREAM_PATH) -> str:
    return f"http://{host or lan_address()}:{port}/{path}"


class HttpStreamer:
    def __init__(self, engine: Callable[[], TranscodeEngine]) -> None:
        self._engine_getter = engine
        self._handle = None
        self.port = DEFAULT_PORT
        self.mode = "copy"
        self.title = ""
        self.error = ""
        self._state = "idle"

    @property
    def engine(self) -> TranscodeEngine:
        return self._engine_getter()

    @property
    def active(self) -> bool:
        return self._handle is not None

    def start(self, mrl: str, options: list[str], title: str = "", start_ms: int = 0, mode: str = "copy",
              port: int = DEFAULT_PORT) -> str | None:
        """Start serving; returns an error message, or None."""
        self.stop()
        if mode not in STREAM_MODES:
            return f"Unknown stream format {mode!r}"
        if not port_available(port):
            return f"Port {port} is already in use; pick another one"
        opts = [o for o in options if not o.startswith((":sout", ":start-time"))]
        opts.append(f":sout={stream_sout(mode, port)}")
        if mode == "copy":
            opts.append(":sout-all")  # every audio track (languages)
        opts.append(":no-sout-spu")  # MPEG-TS cannot carry text subtitles
        if start_ms > 0:
            opts.append(f":start-time={start_ms / 1000:.3f}")
        try:
            self._handle = self.engine.start(mrl, opts)
        except Exception as exc:
            return f"Could not start streaming: {exc}"
        self.port, self.mode, self.title, self.error = port, mode, title, ""
        self._state = "starting"
        return None

    def poll(self) -> str:
        """"idle", "starting", "streaming", "ended" or "error"."""
        if self._handle is None:
            return self._state
        st = self.engine.poll(self._handle).state
        if st == "running":
            self._state = "streaming"
        elif st in ("ended", "error"):
            self._state = st
            if st == "error":
                self.error = "libVLC could not stream this media"
            self._release()
        return self._state

    def _release(self) -> None:
        if self._handle is not None:
            try:
                self.engine.stop(self._handle)
            except Exception:
                pass
            self._handle = None

    def stop(self) -> None:
        self._release()
        self._state = "idle"

    def url(self, host: str | None = None) -> str:
        return stream_url(self.port, host)
