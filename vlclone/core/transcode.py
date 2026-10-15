"""Convert/Save: presets, libVLC stream-output (sout) chains and output checks.

A conversion is libVLC playing the source with a ``:sout=…`` media option
(``#transcode{…}:std{access=file,mux=…,dst=…}``) in a separate libVLC
instance, so the main player is untouched. This module only builds those
strings, picks output names and checks results; no Qt, no libVLC.

Which presets work depends on the encoders and muxers compiled into the
libVLC in use, so :func:`probe_presets` converts a tiny generated clip
with each one and checks the real output codecs (a file that merely exists
is not enough: ``acodec=mpga`` happily writes MP2 into a ``.mp3``).
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .jsonstore import load_json, save_json
from .paths import config_dir


@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    group: str  # "video" | "audio" | "remux"
    ext: str
    mux: str
    vcodec: str | None = None
    acodec: str | None = None
    vb: int = 0  # default video kbps
    ab: int = 0  # default audio kbps
    samplerate: int = 0
    channels: int = 2
    #: VLC fourccs the output must contain (checked by the probe).
    expect_video: str | None = None
    expect_audio: str | None = None
    note: str = ""

    @property
    def remux(self) -> bool:
        return self.group == "remux"

    @property
    def audio_only(self) -> bool:
        return self.group == "audio"


PRESETS: dict[str, Preset] = {p.key: p for p in [
    Preset("mp4_h264", "MP4 — H.264 + AAC", "video", "mp4", "mp4", "h264", "mp4a", 2500, 160, 44100,
           expect_video="h264", expect_audio="mp4a", note="Plays everywhere: phones, browsers, TVs."),
    Preset("mkv_h264", "MKV — H.264 + AAC", "video", "mkv", "mkv", "h264", "mp4a", 2500, 160, 44100,
           expect_video="h264", expect_audio="mp4a"),
    Preset("mkv_h265", "MKV — H.265 + AAC", "video", "mkv", "mkv", "hevc", "mp4a", 1500, 160, 44100,
           expect_video="hevc", expect_audio="mp4a", note="Smaller files; slower to encode."),
    Preset("webm_vp8", "WebM — VP8 + Vorbis", "video", "webm", "webm", "VP80", "vorb", 2000, 160, 44100,
           expect_video="VP80", expect_audio="vorb"),
    Preset("webm_vp9", "WebM — VP9 + Opus", "video", "webm", "webm", "VP90", "opus", 1500, 128, 48000,
           expect_video="VP90", expect_audio="Opus"),
    # "mp3", not "mpga": libVLC encodes mpga as MPEG-1 layer II.
    Preset("mp3", "MP3", "audio", "mp3", "raw", None, "mp3", 0, 192, 44100, expect_audio="mpga"),
    Preset("flac", "FLAC (lossless)", "audio", "flac", "raw", None, "flac", 0, 0, 44100, expect_audio="flac"),
    Preset("ogg_vorbis", "OGG — Vorbis", "audio", "ogg", "ogg", None, "vorb", 0, 192, 44100, expect_audio="vorb"),
    Preset("ogg_opus", "OGG — Opus", "audio", "opus", "ogg", None, "opus", 0, 128, 48000, expect_audio="Opus"),
    Preset("m4a_aac", "Audio only — AAC (M4A)", "audio", "m4a", "mp4", None, "mp4a", 0, 192, 44100,
           expect_audio="mp4a"),
    Preset("remux_mkv", "Remux to MKV (no re-encode)", "remux", "mkv", "mkv",
           note="Copies the streams as they are: fast and lossless. Trims start at a keyframe."),
    Preset("remux_mp4", "Remux to MP4 (no re-encode)", "remux", "mp4", "mp4",
           note="Only for H.264/H.265/MPEG-4 video with AAC/MP3/AC-3 audio. Trims start at a keyframe."),
    # Used for live recordings when Matroska is unavailable (not offered in the Convert dialog).
    Preset("remux_ts", "Remux to MPEG-TS (no re-encode)", "remux", "ts", "ts"),
    Preset("ts_h264", "MPEG-TS — H.264 + AAC", "video", "ts", "ts", "h264", "mp4a", 2500, 160, 44100,
           expect_video="h264", expect_audio="mp4a"),
]}
#: Offered in the Convert dialog, in this order.
CONVERT_PRESETS = ["mp4_h264", "mkv_h264", "mkv_h265", "webm_vp8", "webm_vp9", "mp3", "flac", "ogg_vorbis",
                   "ogg_opus", "m4a_aac", "remux_mkv", "remux_mp4"]
DEFAULT_PRESET = "mp4_h264"

#: (max height, label); 0 keeps the source size. Only ever scales down.
RESOLUTIONS: list[tuple[int, str]] = [(0, "Same as source"), (2160, "2160p (4K)"), (1440, "1440p"),
                                      (1080, "1080p"), (720, "720p"), (480, "480p"), (360, "360p")]
VIDEO_QUALITY: list[tuple[int, str]] = [(0, "Preset default"), (8000, "Very high (8 Mb/s)"),
                                        (5000, "High (5 Mb/s)"), (2500, "Medium (2.5 Mb/s)"),
                                        (1200, "Low (1.2 Mb/s)"), (600, "Very low (600 kb/s)")]
AUDIO_BITRATES: list[int] = [0, 96, 128, 160, 192, 256, 320]

# Codecs a stream copy into MP4 accepts (VLC fourccs); Matroska takes anything.
MP4_VIDEO = {"h264", "avc1", "hevc", "hvc1", "mp4v", "av01", "mp1v", "mp2v"}
MP4_AUDIO = {"mp4a", "mpga", "mp3 ", "mp3", "a52 ", "a52", "eac3", "alac", "Opus"}


@dataclass
class ConvertSettings:
    preset: str = DEFAULT_PRESET
    max_height: int = 0
    video_kbps: int = 0  # 0: the preset's bitrate
    audio_kbps: int = 0
    start_ms: int = 0
    end_ms: int = 0  # 0: to the end
    subtitles: str = ""  # "" = none, "embedded" = the media's subtitle track, else a subtitle file
    live: bool = False  # capture / live stream: encoder settings that lose nothing when stopped

    def copy(self, **changes) -> "ConvertSettings":
        data = asdict(self)
        data.update(changes)
        return ConvertSettings(**data)


@dataclass
class SourceInfo:
    """Codecs of a file, as VLC fourccs (from the engine)."""

    video: list[str] = field(default_factory=list)
    audio: list[str] = field(default_factory=list)
    subtitles: int = 0
    duration_ms: int = -1


# ------------------------------------------------------------------- chains
def sout_path(path: str) -> str:
    """Quote a path for a sout option chain.

    Forward slashes work on every platform (and avoid VLC's backslash
    escapes on Windows); quotes and backslashes left are escaped.
    """
    if os.name == "nt":
        path = path.replace("\\", "/")
    return "'" + path.replace("\\", "\\\\").replace("'", "\\'") + "'"


def transcode_chain(preset: Preset, s: ConvertSettings) -> str:
    """The ``#transcode{…}`` part (empty for remux presets)."""
    if preset.remux:
        return ""
    parts: list[str] = []
    if preset.vcodec and not preset.audio_only:
        parts.append(f"vcodec={preset.vcodec}")
        parts.append(f"vb={s.video_kbps or preset.vb}")
        if s.live and preset.vcodec == "h264":
            # x264's lookahead holds ~40 frames that VLC 3 drops when a live input stops.
            parts.append("venc=x264{tune=zerolatency}")
        if s.max_height:
            parts.append(f"maxheight={s.max_height}")
        if s.subtitles:
            parts.append("soverlay")
    if preset.acodec:
        parts.append(f"acodec={preset.acodec}")
        ab = s.audio_kbps or preset.ab
        if ab:
            parts.append(f"ab={ab}")
        parts.append(f"channels={preset.channels}")
        if preset.samplerate:
            parts.append(f"samplerate={preset.samplerate}")
    return "#transcode{" + ",".join(parts) + "}"


def build_sout(preset: Preset, s: ConvertSettings, dst: str) -> str:
    std = f"std{{access=file,mux={preset.mux},dst={sout_path(dst)}}}"
    chain = transcode_chain(preset, s)
    return f"{chain}:{std}" if chain else f"#{std}"


def media_options(preset: Preset, s: ConvertSettings, dst: str) -> list[str]:
    """Every libVLC media option for one conversion (sout chain, tracks, trim)."""
    options = [f":sout={build_sout(preset, s, dst)}"]
    if preset.remux:
        options.append(":sout-all")  # keep every audio/subtitle track
        if preset.mux != "mkv":
            options.append(":no-sout-spu")
    elif preset.audio_only:
        options += [":no-sout-video", ":no-sout-spu"]
    elif s.subtitles == "embedded":
        options.append(":sub-track=0")
    elif s.subtitles:
        options.append(f":sub-file={s.subtitles}")
    else:
        options.append(":no-sout-spu")
    if s.start_ms > 0:
        options.append(f":start-time={s.start_ms / 1000:.3f}")
    if s.end_ms > 0:
        options.append(f":stop-time={s.end_ms / 1000:.3f}")
    return options


def validate(preset: Preset, s: ConvertSettings, source: SourceInfo | None = None) -> str | None:
    """A reason this conversion cannot work, or None."""
    if s.end_ms and s.end_ms <= s.start_ms:
        return "The end of the trim must be after its start"
    if s.subtitles and s.subtitles != "embedded" and not os.path.isfile(s.subtitles):
        return "The subtitle file does not exist"
    if source is None:
        return None
    if preset.audio_only and not source.audio:
        return "The source has no audio"
    if preset.group == "video" and not source.video and not source.audio:
        return "The source has no audio or video"
    if preset.key == "remux_mp4":
        bad = [c for c in source.video if c not in MP4_VIDEO] + [c for c in source.audio if c not in MP4_AUDIO]
        if bad:
            return f"{', '.join(c.strip() for c in bad)} cannot be copied into MP4; use Remux to MKV"
    return None


# ------------------------------------------------------------------- naming
def part_path(final: str) -> str:
    """Where a conversion writes until it succeeds (renamed at the end, deleted otherwise)."""
    return final + ".part"


def unique_path(path: str, taken: set[str] | None = None) -> str:
    """``film.mp4`` → ``film (1).mp4`` … when the name is used on disk or by another job."""
    taken = taken or set()
    base, ext = os.path.splitext(path)
    candidate, n = path, 1
    while os.path.exists(candidate) or os.path.exists(part_path(candidate)) or candidate in taken:
        candidate = f"{base} ({n}){ext}"
        n += 1
    return candidate


def safe_name(text: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text).strip(" .")
    return cleaned[:120] or "output"


def output_path(source_name: str, source_dir: str | None, preset: Preset, folder: str = "",
                taken: set[str] | None = None) -> str:
    """Output file for a source. ``folder`` empty = next to the source (or the home folder)."""
    target_dir = folder or source_dir or str(Path.home())
    stem = safe_name(Path(source_name).stem if "." in source_name else source_name)
    path = os.path.join(target_dir, f"{stem}.{preset.ext}")
    if source_dir and os.path.normcase(os.path.abspath(path)) == os.path.normcase(
            os.path.abspath(os.path.join(source_dir, source_name))):
        path = os.path.join(target_dir, f"{stem} (converted).{preset.ext}")
    return unique_path(path, taken)


# ------------------------------------------------------------- verification
def mpeg_audio_layer(path: str) -> int | None:
    """Layer (1–3) of the first MPEG audio frame in a raw .mp3/.mp2 file."""
    try:
        with open(path, "rb") as fh:
            data = fh.read(64 * 1024)
    except OSError:
        return None
    start = 0
    if data[:3] == b"ID3" and len(data) >= 10:
        size = (data[6] << 21) | (data[7] << 14) | (data[8] << 7) | data[9]
        start = 10 + size
    for i in range(start, len(data) - 3):
        if data[i] == 0xFF and data[i + 1] & 0xE0 == 0xE0:
            layer_bits = (data[i + 1] >> 1) & 3
            bitrate = data[i + 2] >> 4
            if layer_bits and bitrate not in (0, 15):
                return 4 - layer_bits
    return None


def check_output(preset: Preset, info: SourceInfo, path: str = "", strict: bool = True) -> str | None:
    """None if the output has the codecs the preset promises, else what is wrong.

    ``strict`` requires every stream type the preset makes (the probe's
    source has both); otherwise only the types present are checked, since a
    real source may have no audio or no video.
    """
    if preset.expect_video and preset.expect_video not in info.video and (strict or info.video):
        found = ", ".join(info.video) or "no video"
        return f"expected {preset.expect_video} video, got {found}"
    if preset.expect_audio and preset.expect_audio not in info.audio and (strict or info.audio):
        found = ", ".join(info.audio) or "no audio"
        return f"expected {preset.expect_audio} audio, got {found}"
    if preset.acodec == "mp3" and path and mpeg_audio_layer(path) != 3:
        return "the MPEG audio encoder wrote layer II, not MP3"
    if not info.video and not info.audio:
        return "the output has no audio or video"
    return None


# ----------------------------------------------------------- capabilities
@dataclass
class PresetSupport:
    ok: bool
    reason: str = ""


class CapsStore:
    """Probe results cached per engine version (probing takes a few seconds)."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (config_dir() / "convert_presets.json")

    def load(self, version: str) -> dict[str, PresetSupport] | None:
        raw = load_json(self.path, {})
        if not isinstance(raw, dict) or raw.get("engine") != version or not isinstance(raw.get("presets"), dict):
            return None
        result = {}
        for key, value in raw["presets"].items():
            if key in PRESETS and isinstance(value, dict) and isinstance(value.get("ok"), bool):
                result[key] = PresetSupport(value["ok"], str(value.get("reason", "")))
        return result if set(CONVERT_PRESETS) <= set(result) else None

    def save(self, version: str, caps: dict[str, PresetSupport]) -> None:
        save_json(self.path, {"engine": version, "time": time.time(),
                              "presets": {k: {"ok": v.ok, "reason": v.reason} for k, v in caps.items()}})
