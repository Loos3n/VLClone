"""User settings: a flat dataclass with sane defaults, persisted as JSON.

Adding a setting = adding a field here (and optionally a row on the
settings page, listed in ``SECTIONS`` so search and "Reset to defaults"
know it). Unknown or mistyped keys in the file are ignored, so old and new
versions can share a config file. Files written by older versions are
upgraded by ``migrate()`` (``settings_version`` says which steps ran).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from .jsonstore import load_json, save_json
from .paths import config_dir, default_snapshot_dir

# 1: phases 1-4 (no version field in the file); 2: phase 5 (themes, single instance, window state…)
SETTINGS_VERSION = 2
THEMES = ("dark", "light", "system")
DEFAULT_ACCENT = "#ff8a2a"
OPEN_MODES = ("play", "enqueue")


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
    restore_playlist: bool = True

    # Network streams
    network_caching_ms: int = 1000  # VLC's default; sent per stream as :network-caching
    network_timeout_s: int = 20     # give up on streams that never start (0 = wait forever)

    # Media library
    library_watch: bool = True      # rescan when watched folders change
    library_thumbnails: bool = True

    # Convert / record / stream ("" folder = the Videos folder)
    convert_output_dir: str = ""
    convert_next_to_source: bool = False
    convert_preset: str = "mp4_h264"
    record_dir: str = ""
    stream_port: int = 8080
    stream_mode: str = "copy"

    # Effects ("global": one set for everything, "per_file": each file remembers its own)
    effects_scope: str = "global"
    effects: dict = field(default_factory=dict)

    # Appearance and interface
    theme: str = "dark"              # "dark", "light" or "system"
    accent_color: str = DEFAULT_ACCENT
    controls_hide_delay_ms: int = 2200
    playlist_visible: bool = True
    playlist_width: int = 320
    always_on_top: bool = False
    show_osd: bool = True
    seek_previews: bool = True       # thumbnails above the seek bar for local video files

    # System integration
    single_instance: bool = True     # files opened from Explorer go to the running window
    instance_open_mode: str = "play"  # "play" (play now) or "enqueue"
    prevent_sleep: bool = True       # keep the screen on while a video plays
    media_keys: bool = True          # media keys + Windows media overlay (SMTC)
    taskbar_controls: bool = True    # taskbar thumbnail buttons and progress
    check_updates: bool = True       # at most once a day, notify only

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
    library_section: str = "videos"
    library_sort: str = "title"
    capture_video_device: str = ""
    capture_audio_device: str = ""
    capture_size: str = ""
    window_geometry: str = ""        # Qt saveGeometry blob (phases 1-4; still written for downgrades)
    window_state: dict = field(default_factory=dict)  # see core.windowstate
    shortcuts: dict = field(default_factory=dict)
    last_update_check: float = 0.0
    first_run_done: bool = False
    settings_version: int = SETTINGS_VERSION

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = path or default_path()
        existed = path.exists()
        raw = load_json(path, {})
        if not isinstance(raw, dict):
            raw = {}
        if existed:
            # A file from before versioning is version 1; a fresh install has no file.
            raw = migrate(raw)
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict) -> "Settings":
        settings = cls()
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
                elif isinstance(default, float):
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        setattr(settings, f.name, float(value))
                elif isinstance(value, type(default)):
                    setattr(settings, f.name, value)
        settings.settings_version = SETTINGS_VERSION
        settings.clamp()
        return settings

    def reset_section(self, section: str) -> None:
        """Put every field of a settings-page section back to its default."""
        defaults = type(self)()
        for name in SECTIONS[section][1]:
            setattr(self, name, getattr(defaults, name))

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
        self.network_caching_ms = min(max(self.network_caching_ms, 0), 60000)
        self.network_timeout_s = min(max(self.network_timeout_s, 0), 600)
        self.stream_port = min(max(self.stream_port, 1024), 65535)
        if self.stream_mode not in ("copy", "h264"):
            self.stream_mode = "copy"
        from .transcode import CONVERT_PRESETS, DEFAULT_PRESET

        if self.convert_preset not in CONVERT_PRESETS:
            self.convert_preset = DEFAULT_PRESET
        if self.effects_scope not in ("global", "per_file"):
            self.effects_scope = "global"
        if self.theme not in THEMES:
            self.theme = "dark"
        if not is_hex_color(self.accent_color):
            self.accent_color = DEFAULT_ACCENT
        if self.instance_open_mode not in OPEN_MODES:
            self.instance_open_mode = "play"

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


def is_hex_color(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 7 or not value.startswith("#"):
        return False
    try:
        int(value[1:], 16)
    except ValueError:
        return False
    return True


# Settings-page sections: key -> (title, fields). Used by the settings dialog
# (search, "Reset to defaults") and by Settings.reset_section.
SECTIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    "playback": ("Playback", (
        "play_on_open", "recursive_folders", "restore_playlist", "resume_playback", "resume_min_seconds",
        "seek_short_s", "seek_medium_s", "seek_long_s", "seek_very_long_s",
        "network_caching_ms", "network_timeout_s", "pause_on_click")),
    "audio_video": ("Audio & video", (
        "remember_volume", "max_volume", "volume_step", "effects_scope", "hardware_decoding",
        "preferred_audio_language", "preferred_subtitle_language", "subtitle_text_scale", "extra_vlc_args")),
    "appearance": ("Appearance", (
        "theme", "accent_color", "controls_hide_delay_ms", "show_osd", "seek_previews", "always_on_top")),
    "library": ("Library & files", (
        "library_watch", "library_thumbnails", "snapshot_dir", "snapshot_format", "record_dir")),
    "system": ("System", (
        "single_instance", "instance_open_mode", "prevent_sleep", "media_keys", "taskbar_controls",
        "check_updates")),
}


# Shortcut defaults added by a settings version: {version: [action ids]}. A user
# who already bound one of the new keys to something else keeps their binding.
NEW_SHORTCUTS: dict[int, list[str]] = {2: ["mini_player", "always_on_top", "play_pause"]}


def _migrate_1_to_2(raw: dict) -> dict:
    raw["first_run_done"] = True  # an existing user is not shown the welcome screen
    overrides = raw.get("shortcuts")
    if isinstance(overrides, dict):
        raw["shortcuts"] = migrate_shortcuts(overrides, NEW_SHORTCUTS[2])
    return raw


MIGRATIONS = {1: _migrate_1_to_2}


def migrate(raw: dict) -> dict:
    """Upgrade a settings dict from any older version; newer or unknown versions pass through."""
    raw = dict(raw)
    version = raw.get("settings_version", 1)
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        version = 1
    while version < SETTINGS_VERSION:
        raw = MIGRATIONS[version](raw)
        version += 1
    raw["settings_version"] = max(version, SETTINGS_VERSION)
    return raw


def migrate_shortcuts(overrides: dict, new_actions: list[str]) -> dict:
    """Keep user bindings when new default keys arrive: a new action's default key that
    the user already gave to another action is dropped from the new action."""
    from .shortcuts import DEFAULT_SHORTCUTS, resolve_shortcuts

    result = {k: list(v) for k, v in overrides.items() if isinstance(v, list)}
    user_keys: dict[str, str] = {}
    for action, keys in result.items():
        for key in keys:
            if isinstance(key, str):
                user_keys[key.lower()] = action
    bindings = resolve_shortcuts(result)
    for action in new_actions:
        if action not in DEFAULT_SHORTCUTS or action in result:
            continue
        keys = bindings[action]
        kept = [k for k in keys if user_keys.get(k.lower(), action) == action]
        if kept != keys:
            result[action] = kept
    return result
