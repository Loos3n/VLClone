"""Turning user input (paths, folders, URLs, drops) into playable MRLs."""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import pathname2url, url2pathname

VIDEO_EXTENSIONS = {
    "3g2", "3gp", "amv", "asf", "avi", "divx", "dv", "f4v", "flv", "m2t", "m2ts",
    "m2v", "m4v", "mkv", "mov", "mp2v", "mp4", "mp4v", "mpe", "mpeg", "mpeg1",
    "mpeg2", "mpeg4", "mpg", "mpv2", "mts", "mxf", "nsv", "nuv", "ogg", "ogm",
    "ogv", "ogx", "ps", "rec", "rm", "rmvb", "rpl", "thp", "tod", "ts", "tts",
    "vob", "vro", "webm", "wmv", "wtv", "xesc", "y4m",
}
AUDIO_EXTENSIONS = {
    "3ga", "669", "a52", "aac", "ac3", "adt", "adts", "aif", "aifc", "aiff", "amr",
    "aob", "ape", "au", "caf", "dts", "flac", "it", "m4a", "m4b", "m4p", "mid",
    "mka", "mlp", "mod", "mp1", "mp2", "mp3", "mpc", "mpga", "oga", "oma", "opus",
    "qcp", "ra", "rmi", "s3m", "spx", "tak", "thd", "tta", "voc", "vqf", "w64",
    "wav", "wma", "wv", "xa", "xm",
}
PLAYLIST_EXTENSIONS = {"m3u", "m3u8"}
SUBTITLE_EXTENSIONS = {"srt", "ass", "ssa", "sub", "idx", "vtt", "smi", "txt", "usf", "jss"}
MEDIA_EXTENSIONS = VIDEO_EXTENSIONS | AUDIO_EXTENSIONS

_URL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://")
_WINDOWS_DRIVE_RE = re.compile(r"^[a-zA-Z]:[\\/]")


def extension(name: str) -> str:
    base = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if "." not in base:
        return ""
    return base.rsplit(".", 1)[-1].lower()


def is_url(text: str) -> bool:
    """True for anything with a scheme (http://, rtsp://, file://, ...)."""
    return bool(_URL_RE.match(text.strip())) and not _WINDOWS_DRIVE_RE.match(text.strip())


def is_media_file(path: str) -> bool:
    return extension(path) in MEDIA_EXTENSIONS


def is_subtitle_file(path: str) -> bool:
    return extension(path) in SUBTITLE_EXTENSIONS


def is_playlist_file(path: str) -> bool:
    return extension(path) in PLAYLIST_EXTENSIONS


def path_to_mrl(path: str) -> str:
    """Convert a local filesystem path to a ``file://`` MRL; URLs pass through."""
    if is_url(path):
        return path.strip()
    absolute = os.path.abspath(path)
    url_path = pathname2url(absolute)
    # pathname2url gives '///C:/x' on Windows and '/x' on POSIX.
    if url_path.startswith("///"):
        return "file:" + url_path
    if not url_path.startswith("/"):
        url_path = "/" + url_path
    return "file://" + url_path


def mrl_to_path(mrl: str) -> str | None:
    """Return the local path for a ``file://`` MRL, or ``None`` for remote ones."""
    if not mrl.startswith("file:"):
        return None if is_url(mrl) else mrl
    parsed = urlparse(mrl)
    path = parsed.path
    if parsed.netloc and parsed.netloc not in ("", "localhost"):
        path = f"//{parsed.netloc}{path}"
    return url2pathname(path) if os.name == "nt" else unquote(path)


def display_name(mrl: str) -> str:
    """Human readable title for an MRL (file name, or host/path for URLs)."""
    local = mrl_to_path(mrl)
    if local is not None:
        return Path(local).name or local
    parsed = urlparse(mrl)
    tail = unquote(parsed.path.rstrip("/").rsplit("/", 1)[-1])
    return tail or parsed.netloc or mrl


def _natural_key(text: str):
    return [int(tok) if tok.isdigit() else tok.lower() for tok in re.split(r"(\d+)", text)]


def scan_folder(folder: str, recursive: bool = True) -> list[str]:
    """Return media files in ``folder`` in natural sort order."""
    found: list[str] = []
    root = Path(folder)
    if not root.is_dir():
        return found
    if recursive:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort(key=_natural_key)
            for name in sorted(filenames, key=_natural_key):
                if is_media_file(name):
                    found.append(os.path.join(dirpath, name))
    else:
        for entry in sorted(root.iterdir(), key=lambda p: _natural_key(p.name)):
            if entry.is_file() and is_media_file(entry.name):
                found.append(str(entry))
    return found


def expand_inputs(items: list[str], recursive: bool = True) -> list[str]:
    """Expand a mix of files, folders and URLs into a flat list of MRLs.

    Playlist files are returned as-is (callers decide whether to parse them),
    unknown local files are kept too because libVLC may still play them.
    """
    result: list[str] = []
    for item in items:
        item = item.strip()
        if not item:
            continue
        if is_url(item) and not item.startswith("file:"):
            result.append(item)
            continue
        local = mrl_to_path(item) if item.startswith("file:") else item
        if local and os.path.isdir(local):
            result.extend(path_to_mrl(p) for p in scan_folder(local, recursive))
        elif local:
            result.append(path_to_mrl(local))
    return result
