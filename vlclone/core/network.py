"""Network streams: URL checks, protocol detection, recent-URL history, error text.

Playback itself is libVLC's: HTTP(S), HLS, DASH, RTSP, RTMP, UDP/RTP, MMS,
FTP… and pages handled by VLC's Lua playlist scripts (YouTube, Vimeo,
Dailymotion…). This module only prepares and explains.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

DEFAULT_NETWORK_CACHING_MS = 1000  # VLC's default "network-caching"
MAX_RECENT_URLS = 20

_YOUTUBE_HOSTS = ("youtube.com", "youtu.be", "youtube-nocookie.com")
_LUA_SITES = ("vimeo.com", "dailymotion.com", "soundcloud.com", "twitch.tv", "bbc.co.uk")

#: scheme -> human label for schemes VLC can open.
SCHEMES: dict[str, str] = {
    "http": "HTTP", "https": "HTTPS", "rtsp": "RTSP", "rtsps": "RTSP (TLS)", "rtmp": "RTMP",
    "rtmps": "RTMP (TLS)", "rtmpt": "RTMP (HTTP tunnel)", "udp": "UDP", "rtp": "RTP", "mms": "MMS",
    "mmsh": "MMS over HTTP", "ftp": "FTP", "ftps": "FTP (TLS)", "sftp": "SFTP", "smb": "SMB",
    "nfs": "NFS", "srt": "SRT", "file": "Local file",
}


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _host_is(host: str, domains) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def normalize_url(text: str) -> str:
    """Trim, accept "www.…"/"youtu.be/…" without a scheme, and VLC's ``udp://@:1234`` form."""
    url = text.strip().strip("<>\"'")
    if not url:
        return ""
    if "://" not in url:
        lowered = url.lower()
        if lowered.startswith(("www.", "youtube.com/", "youtu.be/", "m.youtube.com/")):
            url = "https://" + url
        elif re.match(r"^@?[\d.]*:\d+$", url):  # "@:1234" or "239.0.0.1:1234"
            url = "udp://" + (url if url.startswith("@") else "@" + url)
    return url


def stream_kind(url: str) -> str:
    """Short label for what VLC will do with ``url`` (shown as a hint)."""
    url = normalize_url(url)
    scheme = url.split("://", 1)[0].lower() if "://" in url else ""
    host = _host(url)
    path = urlparse(url).path.lower() if scheme else ""
    if scheme in ("http", "https"):
        if _host_is(host, _YOUTUBE_HOSTS):
            return "YouTube (via VLC's Lua script)"
        if _host_is(host, _LUA_SITES):
            return "Web video (via VLC's Lua scripts)"
        if path.endswith(".m3u8"):
            return "HLS stream"
        if path.endswith(".mpd"):
            return "DASH stream"
        if path.endswith((".m3u", ".pls", ".xspf")):
            return "Remote playlist"
    return SCHEMES.get(scheme, scheme.upper() if scheme else "")


def is_youtube(url: str) -> bool:
    return _host_is(_host(normalize_url(url)), _YOUTUBE_HOSTS)


def is_network_mrl(mrl: str) -> bool:
    scheme = mrl.split("://", 1)[0].lower() if "://" in mrl else ""
    return scheme in SCHEMES and scheme != "file"


def validate_url(text: str) -> str | None:
    """``None`` if the URL looks openable, else a short reason."""
    url = normalize_url(text)
    if not url:
        return "Enter a URL"
    if any(c.isspace() for c in url):
        return "URLs cannot contain spaces (use %20)"
    if "://" not in url:
        return "Add a protocol, e.g. https:// or rtsp://"
    scheme = url.split("://", 1)[0].lower()
    if not re.match(r"^[a-z][a-z0-9+.\-]*$", scheme):
        return "That protocol name is not valid"
    if scheme in ("udp", "rtp"):
        return None  # udp://@:1234 has no host
    if scheme not in SCHEMES:
        return None  # let libVLC try (dvb://, screen:// …); it reports failures itself
    if scheme != "file" and not _host(url):
        return "The address has no host name"
    return None


def caching_option(ms: int) -> str:
    return f":network-caching={max(0, int(ms))}"


def describe_open_error(mrl: str, reason: str = "") -> str:
    """User-facing message for media that failed to open."""
    from .capture import is_capture_mrl
    from .discs import is_disc_mrl

    reason = f" ({reason})" if reason else ""
    if is_youtube(mrl):
        return ("Could not open the YouTube link" + reason + ". YouTube changes often and VLC's "
                "youtube.lua script may be out of date; updating VLC (or its lua/playlist/youtube.luac) "
                "usually fixes it.")
    if is_disc_mrl(mrl):
        return ("Could not read the disc" + reason + ". Check that a disc is inserted, or that the folder/ISO "
                "contains VIDEO_TS or BDMV. Encrypted Blu-rays are not supported.")
    if is_capture_mrl(mrl):
        return "Could not open the capture device" + reason + ". It may be in use by another application."
    if is_network_mrl(mrl):
        host = _host(mrl) or mrl
        return (f"Could not open the stream from {host}" + reason
                + ". Check the address and your connection, or try a larger network caching value.")
    return "Could not play this media" + reason


class UrlHistory:
    """Most-recent-first list of opened URLs, stored in ``Settings.recent_urls``."""

    def __init__(self, entries: list | None = None, limit: int = MAX_RECENT_URLS) -> None:
        self.limit = limit
        self.entries: list[str] = []
        for entry in entries or []:
            if isinstance(entry, str) and normalize_url(entry) and normalize_url(entry) not in self.entries:
                self.entries.append(normalize_url(entry))
        del self.entries[limit:]

    def add(self, url: str) -> None:
        url = normalize_url(url)
        if not url:
            return
        self.entries = [url] + [u for u in self.entries if u != url]
        del self.entries[self.limit:]

    def remove(self, url: str) -> None:
        self.entries = [u for u in self.entries if u != url]

    def clear(self) -> None:
        self.entries = []

    def matching(self, text: str) -> list[str]:
        needle = text.strip().lower()
        return [u for u in self.entries if needle in u.lower()] if needle else list(self.entries)
