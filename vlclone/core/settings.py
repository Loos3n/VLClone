"""User settings: a flat dataclass with sane defaults, persisted as JSON.

Adding a setting = adding a field here (and optionally a row on the
settings page). Unknown or mistyped keys in the file are ignored, so old
and new versions can share a config file.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .jsonstore import load_json, save_json
from .paths import config_dir, default_snapshot_dir


@dataclass
class Settings:
    # Playback
    resume_playback: bool = True
    resume_min_seconds: int = 15
    remember_volume: bool = True
    volume: int = 100
    max_volume: int = 200
    seek_short_s: int = 3
    seek_medium_s: int = 10
    seek_long_s: int = 60
    seek_very_long_s: int = 300
    volume_step: int = 5
    pause_on_click: bool = False
    play_on_open: bool = True
    recursive_folders: bool = True

    # Interface
    controls_hide_delay_ms: int = 2200
    playlist_visible: bool = True
    playlist_width: int = 320
    always_on_top: bool = False
    show_osd: bool = True

    # Engine (applied when libVLC starts: restart required)
    hardware_decoding: bool = True
    subtitle_text_scale: int = 100
    preferred_audio_language: str = ""
    preferred_subtitle_language: str = ""
    extra_vlc_args: str = ""

    # Snapshots
    snapshot_dir: str = field(default_factory=default_snapshot_dir)
    snapshot_format: str = "png"

    # Saved by the app, not shown on the settings page
    repeat: str = "none"
    shuffle: bool = False
    last_open_dir: str = ""
    recent_urls: list = field(default_factory=list)
    window_geometry: str = ""
    shortcuts: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = path or default_path()
        raw = load_json(path, {})
        settings = cls()
        if isinstance(raw, dict):
            for f in fields(cls):
                if f.name in raw:
                    value = raw[f.name]
                    default = getattr(settings, f.name)
                    if isinstance(default, bool):
                        if isinstance(value, bool):
                            setattr(settings, f.name, value)
                    elif isinstance(default, int):
                        if isinstance(value, (int, float)) and not isinstance(value, bool):
                            setattr(settings, f.name, int(value))
                    elif isinstance(value, type(default)):
                        setattr(settings, f.name, value)
        settings.clamp()
        return settings

    def clamp(self) -> None:
        self.max_volume = min(max(self.max_volume, 100), 200)
        self.volume = min(max(self.volume, 0), self.max_volume)
        self.volume_step = min(max(self.volume_step, 1), 25)
        self.controls_hide_delay_ms = min(max(self.controls_hide_delay_ms, 500), 30000)
        self.subtitle_text_scale = min(max(self.subtitle_text_scale, 10), 500)
        if self.snapshot_format not in ("png", "jpg"):
            self.snapshot_format = "png"
        if self.repeat not in ("none", "all", "one"):
            self.repeat = "none"

    def save(self, path: Path | None = None) -> None:
        save_json(path or default_path(), asdict(self))

    def vlc_args(self) -> list[str]:
        """Command line options for the libVLC instance."""
        args = ["--no-video-title-show", "--quiet"]
        args.append("--avcodec-hw=any" if self.hardware_decoding else "--avcodec-hw=none")
        if self.subtitle_text_scale != 100:
            args.append(f"--sub-text-scale={self.subtitle_text_scale}")
        if self.preferred_audio_language.strip():
            args.append(f"--audio-language={self.preferred_audio_language.strip()}")
        if self.preferred_subtitle_language.strip():
            args.append(f"--sub-language={self.preferred_subtitle_language.strip()}")
        if self.extra_vlc_args.strip():
            args.extend(self.extra_vlc_args.split())
        return args


def default_path() -> Path:
    return config_dir() / "settings.json"
