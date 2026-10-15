"""Discs: DVD, Blu-ray and audio CD from a drive, a folder or an ISO image.

libVLC reads them with its dvdnav/dvdread, libbluray and cdda modules; this
module builds the MRLs the same way VLC's "Open Disc" panel does and finds
optical drives. Title/chapter selection uses VLC's ``#title:chapter`` MRL
suffix; "no menus" uses ``dvdsimple://`` and ``:no-bluray-menu``.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

DISC_KINDS = {"dvd": "DVD", "bluray": "Blu-ray", "cd": "Audio CD"}
_DISC_SCHEMES = {"dvd", "dvdsimple", "dvdread", "bluray", "cdda", "vcd"}
ISO_EXTENSIONS = {"iso", "img", "udf"}


@dataclass
class DiscRequest:
    kind: str  # "dvd" | "bluray" | "cd"
    location: str  # drive ("D:\\", "/dev/sr0"), folder or ISO image
    menus: bool = True
    title: int = 0  # 0 = start normally (menu, or first title)
    chapter: int = 0
    track: int = 0  # audio CD: 0 = whole disc


@dataclass
class OpticalDrive:
    path: str  # "D:\\" or "/dev/sr0"
    label: str = ""
    kind: str | None = None  # detected disc kind, None if empty/unknown

    @property
    def display(self) -> str:
        what = DISC_KINDS.get(self.kind or "", "")
        parts = [self.path.rstrip("\\/") or self.path]
        if self.label:
            parts.append(self.label)
        if what:
            parts.append(what)
        return " — ".join(parts)


def is_disc_mrl(mrl: str) -> bool:
    return mrl.split("://", 1)[0].lower() in _DISC_SCHEMES


def _location(path: str) -> str:
    """Path part of a disc MRL: ``D:\\`` → ``/D:/``, POSIX paths unchanged."""
    path = path.strip()
    if len(path) >= 2 and path[1] == ":":  # Windows drive or path
        return "/" + path.replace("\\", "/")
    return path.replace("\\", "/") if os.sep == "\\" else path


def disc_mrl(req: DiscRequest) -> tuple[str, list[str], str]:
    """Return ``(mrl, media options, playlist title)``. Raises ``ValueError`` on bad input."""
    if req.kind not in DISC_KINDS:
        raise ValueError(f"Unknown disc type {req.kind!r}")
    if not req.location.strip():
        raise ValueError("Choose a drive, folder or ISO image")
    location = _location(req.location)
    if "#" in location:
        raise ValueError("Disc paths cannot contain '#'")
    options: list[str] = []
    name = Path(req.location.rstrip("\\/")).name or req.location
    if req.kind == "cd":
        mrl = "cdda://" + location
        if req.track > 0:
            options.append(f":cdda-track={req.track}")
        return mrl, options, f"Audio CD ({name})" + (f" — track {req.track}" if req.track > 0 else "")
    if req.kind == "dvd":
        mrl = ("dvd://" if req.menus else "dvdsimple://") + location
    else:
        mrl = "bluray://" + location
        if not req.menus:
            options.append(":no-bluray-menu")
    if req.title > 0:
        mrl += f"#{req.title}"
        if req.chapter > 0:
            mrl += f":{req.chapter}"
    label = DISC_KINDS[req.kind]
    suffix = f" — title {req.title}" if req.title > 0 else ""
    return mrl, options, f"{label} ({name}){suffix}"


# ------------------------------------------------------------------ detection
def _iso_kind(path: str, limit: int = 64 * 1024 * 1024) -> str | None:
    """Look for the VIDEO_TS / BDMV directory names in an ISO's UDF/ISO9660 area."""
    try:
        with open(path, "rb") as fh:
            read = 0
            tail = b""
            while read < limit:
                chunk = fh.read(1024 * 1024)
                if not chunk:
                    break
                data = tail + chunk
                if b"BDMV" in data:
                    return "bluray"
                if b"VIDEO_TS" in data:
                    return "dvd"
                tail = chunk[-16:]
                read += len(chunk)
    except OSError:
        return None
    return None


def detect_disc_kind(path: str) -> str | None:
    """Guess "dvd", "bluray" or "cd" for a drive root, a folder or an image file."""
    p = Path(path)
    try:
        if p.is_file():
            ext = p.suffix.lower().lstrip(".")
            if ext in ISO_EXTENSIONS:
                return _iso_kind(str(p))
            if ext in ("ifo", "vob"):
                return "dvd"
            return None
        if not p.is_dir():
            return None
        if p.name.upper() == "VIDEO_TS":
            return "dvd"
        if p.name.upper() == "BDMV":
            return "bluray"
        names = {child.name.upper() for child in p.iterdir()}
    except OSError:
        return None
    if "BDMV" in names:
        return "bluray"
    if "VIDEO_TS" in names or "VIDEO_TS.IFO" in names:
        return "dvd"
    if any(n.endswith(".CDA") for n in names):
        return "cd"  # Windows shows audio CD tracks as TrackNN.cda
    return None


def disc_root(path: str) -> str:
    """Drive/folder VLC should open for a path inside a disc structure."""
    p = Path(path)
    if p.is_file() and p.suffix.lower().lstrip(".") in ("ifo", "vob"):
        p = p.parent
    if p.name.upper() in ("VIDEO_TS", "BDMV"):
        p = p.parent
    return str(p)


def _windows_drives() -> list[OpticalDrive]:  # pragma: no cover - Windows only
    import ctypes

    kernel32 = ctypes.windll.kernel32
    kernel32.SetErrorMode(1)  # no "insert a disc" popups for empty drives
    drives = []
    mask = kernel32.GetLogicalDrives()
    for i in range(26):
        if not mask & (1 << i):
            continue
        root = f"{chr(65 + i)}:\\"
        if kernel32.GetDriveTypeW(ctypes.c_wchar_p(root)) != 5:  # DRIVE_CDROM
            continue
        label = ctypes.create_unicode_buffer(261)
        has_media = kernel32.GetVolumeInformationW(ctypes.c_wchar_p(root), label, 261, None, None,
                                                   None, None, 0)
        kind = detect_disc_kind(root) if has_media else None
        drives.append(OpticalDrive(root, label.value if has_media else "No disc", kind))
    return drives


def _linux_drives(dev_root: str = "/dev", mounts_file: str = "/proc/mounts") -> list[OpticalDrive]:
    mounts: dict[str, str] = {}
    try:
        with open(mounts_file, encoding="utf-8") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 2:
                    mounts[parts[0]] = parts[1].replace("\\040", " ")
    except OSError:
        pass
    drives = []
    for name in sorted(os.listdir(dev_root)) if os.path.isdir(dev_root) else []:
        if not name.startswith("sr") or not name[2:].isdigit():
            continue
        dev = os.path.join(dev_root, name)
        mount = mounts.get(dev)
        kind = detect_disc_kind(mount) if mount else None
        drives.append(OpticalDrive(dev, Path(mount).name if mount else "", kind))
    return drives


def _mac_drives() -> list[OpticalDrive]:  # pragma: no cover - macOS only
    drives = []
    for vol in sorted(Path("/Volumes").glob("*")):
        kind = detect_disc_kind(str(vol))
        if kind:
            drives.append(OpticalDrive(str(vol), vol.name, kind))
    return drives


def list_optical_drives(platform: str = sys.platform) -> list[OpticalDrive]:
    """Optical drives with the kind of disc in them (when it can be told)."""
    try:
        if platform == "win32":
            return _windows_drives()
        if platform == "darwin":
            return _mac_drives()
        return _linux_drives()
    except Exception:  # never let a weird drive break the dialog
        return []
