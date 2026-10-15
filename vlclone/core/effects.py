"""Audio/video effects model (VLC's "Effects and Filters"), free of Qt and libVLC.

Two kinds of effects exist, because libVLC applies them differently:

* **Live** effects change instantly on the playing media: equalizer, preamp,
  stereo mode, volume normalization and image adjust (brightness, contrast,
  hue, saturation, gamma).
* **Reload** effects (flip, sharpen; rotation from phase 1 too) are libVLC
  video filters that only take effect when the media is (re)loaded. They are
  collected in :class:`EngineFilters` and applied by the reload-with-position
  mechanism, batched through a :class:`Debouncer`.
"""

from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from .jsonstore import load_json, save_json
from .paths import config_dir

# ------------------------------------------------------------------ equalizer
#: libVLC 3's 10 equalizer bands (Hz).
EQ_BAND_FREQUENCIES: list[float] = [31.25, 62.5, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
EQ_BANDS = len(EQ_BAND_FREQUENCIES)
EQ_MIN_DB = -20.0
EQ_MAX_DB = 20.0

#: VLC's built-in presets as (preamp, bands), read from libVLC 3.0.20. The
#: libVLC backend reads them from the engine at runtime; this table is the
#: reference for the fake backend and for builds whose engine can't list them.
BUILTIN_EQ_PRESETS: dict[str, tuple[float, list[float]]] = {
    "Flat": (12.0, [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
    "Classical": (12.0, [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, -7.2, -7.2, -7.2, -9.6]),
    "Club": (6.0, [0.0, 0.0, 8.0, 5.6, 5.6, 5.6, 3.2, 0.0, 0.0, 0.0]),
    "Dance": (5.0, [9.6, 7.2, 2.4, 0.0, 0.0, -5.6, -7.2, -7.2, 0.0, 0.0]),
    "Full bass": (5.0, [-8.0, 9.6, 9.6, 5.6, 1.6, -4.0, -8.0, -10.4, -11.2, -11.2]),
    "Full bass and treble": (4.0, [7.2, 5.6, 0.0, -7.2, -4.8, 1.6, 8.0, 11.2, 12.0, 12.0]),
    "Full treble": (3.0, [-9.6, -9.6, -9.6, -4.0, 2.4, 11.2, 16.0, 16.0, 16.0, 16.8]),
    "Headphones": (4.0, [4.8, 11.2, 5.6, -3.2, -2.4, 1.6, 4.8, 9.6, 12.8, 14.4]),
    "Large Hall": (5.0, [10.4, 10.4, 5.6, 5.6, 0.0, -4.8, -4.8, -4.8, 0.0, 0.0]),
    "Live": (7.0, [-4.8, 0.0, 4.0, 5.6, 5.6, 5.6, 4.0, 2.4, 2.4, 2.4]),
    "Party": (6.0, [7.2, 7.2, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 7.2, 7.2]),
    "Pop": (6.0, [-1.6, 4.8, 7.2, 8.0, 5.6, 0.0, -2.4, -2.4, -1.6, -1.6]),
    "Reggae": (8.0, [0.0, 0.0, 0.0, -5.6, 0.0, 6.4, 6.4, 0.0, 0.0, 0.0]),
    "Rock": (5.0, [8.0, 4.8, -5.6, -8.0, -3.2, 4.0, 8.8, 11.2, 11.2, 11.2]),
    "Ska": (6.0, [-2.4, -4.8, -4.0, 0.0, 4.0, 5.6, 8.8, 9.6, 11.2, 9.6]),
    "Soft": (5.0, [4.8, 1.6, 0.0, -2.4, 0.0, 4.0, 8.0, 9.6, 11.2, 12.0]),
    "Soft rock": (7.0, [4.0, 4.0, 2.4, 0.0, -4.0, -5.6, -3.2, 0.0, 2.4, 8.8]),
    "Techno": (5.0, [8.0, 5.6, 0.0, -5.6, -4.8, 0.0, 8.0, 9.6, 9.6, 8.8]),
}


def format_frequency(hz: float) -> str:
    return f"{hz / 1000:g}k" if hz >= 1000 else f"{hz:g}"


def clamp_db(value: float) -> float:
    if not isinstance(value, (int, float)) or math.isnan(value):
        return 0.0
    return round(max(EQ_MIN_DB, min(EQ_MAX_DB, float(value))), 1)


def clean_bands(values) -> list[float]:
    """Validate a list of band gains: exactly EQ_BANDS clamped numbers."""
    bands = [clamp_db(v) for v in (values if isinstance(values, (list, tuple)) else [])][:EQ_BANDS]
    return bands + [0.0] * (EQ_BANDS - len(bands))


# --------------------------------------------------------------- audio extras
#: (key, label, libVLC 3 "stereo-mode" value). 7 is mono (libVLC 3.0's enum
#: has no name for it, but the engine accepts it).
STEREO_MODES: list[tuple[str, str, int]] = [
    ("stereo", "Stereo", 1),
    ("mono", "Mono", 7),
    ("left", "Left", 3),
    ("right", "Right", 4),
    ("reverse", "Reverse", 2),
]
STEREO_MODE_VALUES = {key: value for key, _, value in STEREO_MODES}

NORMALIZE_MIN, NORMALIZE_MAX, NORMALIZE_DEFAULT = 0.5, 10.0, 2.0  # VLC's "maximum level"

# --------------------------------------------------------------- image adjust
#: name -> (minimum, maximum, neutral), matching libVLC's adjust filter.
ADJUST_RANGES: dict[str, tuple[float, float, float]] = {
    "brightness": (0.0, 2.0, 1.0),
    "contrast": (0.0, 2.0, 1.0),
    "hue": (-180.0, 180.0, 0.0),
    "saturation": (0.0, 3.0, 1.0),
    "gamma": (0.01, 10.0, 1.0),
}
ADJUST_NAMES = list(ADJUST_RANGES)

SHARPEN_MIN, SHARPEN_MAX, SHARPEN_DEFAULT = 0.0, 2.0, 0.5  # libVLC "sharpen-sigma"

# ------------------------------------------------------------------ geometry
ASPECT_RATIOS: list[str | None] = [None, "16:9", "4:3", "1:1", "16:10", "2.21:1", "2.35:1", "2.39:1", "5:4"]
CROP_RATIOS: list[str | None] = [None, "16:9", "4:3", "1:1", "16:10", "2.21:1", "2.35:1", "2.39:1", "5:4"]
ZOOM_LEVELS: list[float] = [0.0, 0.25, 0.5, 1.0, 2.0]
ROTATIONS: list[int] = [0, 90, 180, 270]
DEINTERLACE_MODES: list[str | None] = [
    None, "blend", "bob", "discard", "linear", "mean", "x", "yadif", "yadif2x", "phosphor", "ivtc",
]


def _num(value, lo: float, hi: float, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or math.isnan(value):
        return default
    return max(lo, min(hi, float(value)))


@dataclass
class AudioEffects:
    eq_enabled: bool = False
    eq_preset: str = "Flat"  # "" once bands were edited by hand
    eq_preamp: float = 12.0
    eq_bands: list = field(default_factory=lambda: [0.0] * EQ_BANDS)
    normalize: bool = False
    normalize_level: float = NORMALIZE_DEFAULT
    stereo_mode: str = "stereo"


@dataclass
class VideoEffects:
    brightness: float = 1.0
    contrast: float = 1.0
    hue: float = 0.0
    saturation: float = 1.0
    gamma: float = 1.0
    flip_h: bool = False
    flip_v: bool = False
    sharpen: bool = False
    sharpen_strength: float = SHARPEN_DEFAULT

    def adjust_values(self) -> dict[str, float]:
        return {name: getattr(self, name) for name in ADJUST_NAMES}

    @property
    def adjust_active(self) -> bool:
        return any(abs(getattr(self, n) - ADJUST_RANGES[n][2]) > 1e-6 for n in ADJUST_NAMES)


@dataclass
class Geometry:
    """Picture geometry: aspect ratio, crop, zoom, rotation, deinterlace.

    Stored with the effects so it follows the same global / per-file scope.
    """

    aspect_ratio: str | None = None
    crop: str | None = None
    zoom: float = 0.0
    rotation: int = 0
    deinterlace: str | None = None

    @classmethod
    def from_dict(cls, raw) -> "Geometry":
        g = cls()
        if not isinstance(raw, dict):
            return g
        if raw.get("aspect_ratio") in ASPECT_RATIOS:
            g.aspect_ratio = raw["aspect_ratio"]
        if raw.get("crop") in CROP_RATIOS:
            g.crop = raw["crop"]
        zoom = raw.get("zoom")
        if isinstance(zoom, (int, float)) and not isinstance(zoom, bool) and float(zoom) in ZOOM_LEVELS:
            g.zoom = float(zoom)
        if raw.get("rotation") in ROTATIONS and not isinstance(raw.get("rotation"), bool):
            g.rotation = int(raw["rotation"])
        if raw.get("deinterlace") in DEINTERLACE_MODES:
            g.deinterlace = raw["deinterlace"]
        return g

    @property
    def is_default(self) -> bool:
        return self == Geometry()


@dataclass
class Effects:
    audio: AudioEffects = field(default_factory=AudioEffects)
    video: VideoEffects = field(default_factory=VideoEffects)
    geometry: Geometry = field(default_factory=Geometry)

    def to_dict(self) -> dict:
        return {"audio": asdict(self.audio), "video": asdict(self.video), "geometry": asdict(self.geometry)}

    @classmethod
    def from_dict(cls, raw) -> "Effects":
        """Build from untrusted JSON; unknown keys are ignored, values clamped."""
        fx = cls()
        if not isinstance(raw, dict):
            return fx
        a = raw.get("audio") if isinstance(raw.get("audio"), dict) else {}
        v = raw.get("video") if isinstance(raw.get("video"), dict) else {}
        au = fx.audio
        au.eq_enabled = a.get("eq_enabled") is True
        au.eq_preset = a["eq_preset"] if isinstance(a.get("eq_preset"), str) else au.eq_preset
        au.eq_preamp = clamp_db(a["eq_preamp"]) if "eq_preamp" in a else au.eq_preamp
        if "eq_bands" in a:
            au.eq_bands = clean_bands(a["eq_bands"])
        au.normalize = a.get("normalize") is True
        au.normalize_level = _num(a.get("normalize_level"), NORMALIZE_MIN, NORMALIZE_MAX, NORMALIZE_DEFAULT)
        if a.get("stereo_mode") in STEREO_MODE_VALUES:
            au.stereo_mode = a["stereo_mode"]
        vi = fx.video
        for name, (lo, hi, neutral) in ADJUST_RANGES.items():
            setattr(vi, name, _num(v.get(name), lo, hi, neutral))
        vi.flip_h = v.get("flip_h") is True
        vi.flip_v = v.get("flip_v") is True
        vi.sharpen = v.get("sharpen") is True
        vi.sharpen_strength = _num(v.get("sharpen_strength"), SHARPEN_MIN, SHARPEN_MAX, SHARPEN_DEFAULT)
        fx.geometry = Geometry.from_dict(raw.get("geometry"))
        return fx

    def copy(self) -> "Effects":
        return Effects.from_dict(self.to_dict())


# ----------------------------------------------------------- reload filters
_ROTATE = {0: None, 90: "90", 180: "180", 270: "270"}
# Rotating clockwise by r after a horizontal mirror, as one libVLC transform.
_ROTATE_MIRRORED = {0: "hflip", 90: "antitranspose", 180: "vflip", 270: "transpose"}


def transform_type(rotation: int, flip_h: bool, flip_v: bool) -> str | None:
    """Combine rotation and flips into the single libVLC ``transform-type``.

    Flips apply to the source picture, then the rotation. A vertical flip is
    a horizontal flip turned 180°, so every combination is one of the eight
    orientations libVLC's transform filter offers.
    """
    rotation %= 360
    if flip_v:
        rotation = (rotation + 180) % 360
    table = _ROTATE_MIRRORED if flip_h != flip_v else _ROTATE
    return table[rotation]


@dataclass(frozen=True)
class EngineFilters:
    """Video filters libVLC only applies when media is loaded."""

    transform: str | None = None
    sharpen: float | None = None  # sigma, None = off

    @property
    def is_default(self) -> bool:
        return self.transform is None and self.sharpen is None

    def vlc_args(self) -> list[str]:
        chain, args = [], []
        if self.transform:
            chain.append("transform")
            args.append(f"--transform-type={self.transform}")
        if self.sharpen is not None:
            chain.append("sharpen")
            args.append(f"--sharpen-sigma={self.sharpen:.2f}")
        return ([f"--video-filter={':'.join(chain)}"] if chain else []) + args


def engine_filters(rotation: int, video: VideoEffects) -> EngineFilters:
    sharpen = round(video.sharpen_strength, 2) if video.sharpen else None
    return EngineFilters(transform_type(rotation, video.flip_h, video.flip_v), sharpen)


# ------------------------------------------------------------------ debounce
class Debouncer:
    """Collapse a burst of requests into one action after a quiet period.

    Polled from the controller's tick (no threads, no Qt); the clock is
    injectable so tests can drive time.
    """

    def __init__(self, delay_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.delay_s = delay_s
        self.clock = clock
        self._due: float | None = None

    def request(self) -> None:
        self._due = self.clock() + self.delay_s

    @property
    def pending(self) -> bool:
        return self._due is not None

    def cancel(self) -> None:
        self._due = None

    def poll(self) -> bool:
        """True exactly once when the quiet period after the last request ends."""
        if self._due is not None and self.clock() >= self._due:
            self._due = None
            return True
        return False

    def flush(self) -> bool:
        """Fire now if something is pending."""
        if self._due is None:
            return False
        self._due = None
        return True


# ------------------------------------------------------------------- stores
class EqPresetStore:
    """User equalizer presets, saved as JSON next to the settings."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (config_dir() / "eq_presets.json")
        raw = load_json(self.path, {})
        self._presets: dict[str, tuple[float, list[float]]] = {}
        if isinstance(raw, dict):
            for name, value in raw.items():
                if isinstance(name, str) and name.strip() and isinstance(value, dict):
                    self._presets[name.strip()] = (clamp_db(value.get("preamp", 0.0)),
                                                   clean_bands(value.get("bands")))

    def names(self) -> list[str]:
        return sorted(self._presets, key=str.lower)

    def get(self, name: str) -> tuple[float, list[float]] | None:
        preset = self._presets.get(name)
        return (preset[0], list(preset[1])) if preset else None

    def save(self, name: str, preamp: float, bands: list[float]) -> str:
        """Store (or overwrite) a preset; built-in names are not allowed."""
        name = name.strip()
        if not name:
            raise ValueError("Preset name is empty")
        if name.lower() in {n.lower() for n in BUILTIN_EQ_PRESETS}:
            raise ValueError(f"“{name}” is a built-in preset name")
        self._presets[name] = (clamp_db(preamp), clean_bands(bands))
        self._write()
        return name

    def delete(self, name: str) -> bool:
        if self._presets.pop(name, None) is None:
            return False
        self._write()
        return True

    def _write(self) -> None:
        save_json(self.path, {n: {"preamp": p, "bands": b} for n, (p, b) in self._presets.items()})


class EffectsStore:
    """Per-file effect settings (used when effects scope is "per file")."""

    MAX_ENTRIES = 500

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (config_dir() / "effects_per_file.json")
        raw = load_json(self.path, {})
        self._data: dict[str, dict] = raw if isinstance(raw, dict) else {}
        self._dirty = False

    def get(self, mrl: str) -> Effects | None:
        entry = self._data.get(mrl)
        return Effects.from_dict(entry["fx"]) if isinstance(entry, dict) and "fx" in entry else None

    def put(self, mrl: str, effects: Effects) -> None:
        self._data[mrl] = {"fx": effects.to_dict(), "t": time.time()}
        self._dirty = True

    def forget(self, mrl: str) -> None:
        if self._data.pop(mrl, None) is not None:
            self._dirty = True

    def save(self) -> None:
        if not self._dirty:
            return
        if len(self._data) > self.MAX_ENTRIES:
            newest = sorted(self._data.items(), key=lambda kv: kv[1].get("t", 0) if isinstance(kv[1], dict)
                            else 0, reverse=True)
            self._data = dict(newest[: self.MAX_ENTRIES])
        save_json(self.path, self._data)
        self._dirty = False
