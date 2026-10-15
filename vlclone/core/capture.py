"""Capture devices (webcam, microphone): engine MRLs and device discovery.

VLC opens capture devices through access modules: DirectShow (``dshow://``)
on Windows, Video4Linux2 + ALSA (``v4l2://``, ``alsa://``) on Linux and
AVFoundation (``avcapture://``) on macOS. This module only builds MRLs and
options, and lists devices where that needs no engine; on Windows the libVLC
backend asks VLC's own DirectShow module for the device names.
"""

from __future__ import annotations

import glob
import os
import re
import sys
from dataclasses import dataclass

from .backend import CaptureDevice

#: (width, height, label); 0×0 lets the device pick its default format.
RESOLUTIONS: list[tuple[int, int, str]] = [
    (0, 0, "Device default"),
    (320, 240, "320 × 240"),
    (640, 480, "640 × 480 (VGA)"),
    (800, 600, "800 × 600"),
    (1280, 720, "1280 × 720 (HD)"),
    (1920, 1080, "1920 × 1080 (Full HD)"),
    (3840, 2160, "3840 × 2160 (4K)"),
]
DEFAULT_CAPTURE_CACHING_MS = 300  # VLC's own default for live capture


@dataclass
class CaptureRequest:
    """What to capture. Device ids come from :func:`list_devices_generic` or the backend.

    ``None`` means "no video" / "no audio"; ``""`` is the system default device.
    """

    video_device: str | None = ""
    audio_device: str | None = None
    width: int = 0
    height: int = 0
    fps: float = 0.0
    caching_ms: int = DEFAULT_CAPTURE_CACHING_MS


def capture_mrl(req: CaptureRequest, platform: str = sys.platform) -> tuple[str, list[str], str]:
    """Return ``(mrl, media options, title)`` for a capture request.

    Raises ``ValueError`` when neither video nor audio is selected.
    """
    if req.video_device is None and req.audio_device is None:
        raise ValueError("Choose a video or an audio device")
    options: list[str] = []
    size = req.width > 0 and req.height > 0
    if platform == "win32":
        options.append(f":dshow-vdev={'none' if req.video_device is None else req.video_device}")
        options.append(f":dshow-adev={'none' if req.audio_device is None else req.audio_device}")
        if size:
            options.append(f":dshow-size={req.width}x{req.height}")
        if req.fps > 0:
            options.append(f":dshow-fps={req.fps:g}")
        mrl = "dshow://"
    elif platform == "darwin":
        if req.video_device is None:
            raise ValueError("Audio-only capture is not available on macOS")
        mrl = f"avcapture://{req.video_device}"
    else:
        if req.video_device is not None:
            mrl = f"v4l2://{req.video_device or '/dev/video0'}"
            if size:
                options += [f":v4l2-width={req.width}", f":v4l2-height={req.height}"]
            if req.fps > 0:
                options.append(f":v4l2-fps={req.fps:g}")
            if req.audio_device is not None:
                options.append(f":input-slave=alsa://{req.audio_device or 'default'}")
        else:
            mrl = f"alsa://{req.audio_device or 'default'}"
    options.append(f":live-caching={max(0, int(req.caching_ms))}")
    names = [n for n in (req.video_device, req.audio_device) if n]
    title = "Capture: " + (" + ".join(names) if names else "default device")
    return mrl, options, title


def is_capture_mrl(mrl: str) -> bool:
    return mrl.split("://", 1)[0].lower() in {"dshow", "v4l2", "v4l", "alsa", "avcapture", "qtcapture"}


# ----------------------------------------------------------------- discovery
def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def linux_video_devices(dev_root: str = "/dev", sys_root: str = "/sys/class/video4linux") -> list[CaptureDevice]:
    """V4L2 capture nodes with their driver names (metadata-only nodes skipped)."""
    devices = []
    for node in sorted(glob.glob(os.path.join(dev_root, "video*")),
                       key=lambda p: int(re.sub(r"\D", "", os.path.basename(p)) or 0)):
        base = os.path.basename(node)
        index = _read(os.path.join(sys_root, base, "index"))
        if index not in ("", "0"):
            continue  # second node of the same camera (metadata stream)
        name = _read(os.path.join(sys_root, base, "name")) or base
        devices.append(CaptureDevice(node, f"{name} ({base})"))
    return devices


def linux_audio_devices(pcm_file: str = "/proc/asound/pcm") -> list[CaptureDevice]:
    """ALSA PCM devices that can capture, as ``hw:card,device``."""
    devices = [CaptureDevice("default", "Default microphone")]
    for line in _read(pcm_file).splitlines():
        m = re.match(r"(\d+)-(\d+):\s*([^:]*):\s*([^:]*)", line)
        if m and "capture" in line:
            card, dev = int(m.group(1)), int(m.group(2))
            devices.append(CaptureDevice(f"hw:{card},{dev}", f"{m.group(3).strip()} (hw:{card},{dev})"))
    return devices


def list_devices_generic(platform: str = sys.platform) -> dict[str, list[CaptureDevice]]:
    """Devices discoverable without the engine.

    Windows only gets the "default device" entries here; the libVLC backend
    replaces them with the real DirectShow list.
    """
    if platform == "win32":
        return {"video": [CaptureDevice("", "Default camera")], "audio": [CaptureDevice("", "Default microphone")]}
    if platform.startswith("linux"):
        return {"video": linux_video_devices(), "audio": linux_audio_devices()}
    if platform == "darwin":
        return {"video": [CaptureDevice("", "Default camera")], "audio": []}
    return {"video": [], "audio": []}
