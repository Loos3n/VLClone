"""Default keyboard shortcuts, matching VLC 3's defaults where VLC has one.

Keys are Qt key-sequence strings ("Ctrl+O"), but this module does not
import Qt. Users can override bindings via ``Settings.shortcuts``.
"""

from __future__ import annotations

# action id -> (label, default key sequences)
DEFAULT_SHORTCUTS: dict[str, tuple[str, list[str]]] = {
    # Media
    "open_file": ("Open file", ["Ctrl+O"]),
    "open_folder": ("Open folder", ["Ctrl+F"]),
    "open_url": ("Open network stream", ["Ctrl+N"]),
    "open_media": ("Open media (all sources)", ["Ctrl+Shift+O"]),
    "open_disc": ("Open disc", ["Ctrl+D"]),
    "open_capture": ("Open capture device", ["Ctrl+C"]),
    "media_library": ("Media library", ["Ctrl+Shift+L"]),
    "convert": ("Convert / save", ["Ctrl+R"]),
    "stream": ("Stream", ["Ctrl+S"]),
    "conversions": ("Conversion queue", []),
    "load_subtitle": ("Load subtitle file", []),
    "quit": ("Quit", ["Ctrl+Q"]),
    # Playback
    # The hardware play/pause key arrives as "Toggle Media Play/Pause" on Windows.
    "play_pause": ("Play / pause", ["Space", "Media Play", "Toggle Media Play/Pause", "Media Pause"]),
    "stop": ("Stop", ["S", "Media Stop"]),
    "next": ("Next", ["N", "Media Next"]),
    "previous": ("Previous", ["P", "Media Previous"]),
    "frame_step": ("Next frame", ["E"]),
    "jump_back_short": ("Very short jump back", ["Shift+Left"]),
    "jump_fwd_short": ("Very short jump forward", ["Shift+Right"]),
    "jump_back_medium": ("Short jump back", ["Alt+Left", "Left"]),
    "jump_fwd_medium": ("Short jump forward", ["Alt+Right", "Right"]),
    "jump_back_long": ("Medium jump back", ["Ctrl+Left"]),
    "jump_fwd_long": ("Medium jump forward", ["Ctrl+Right"]),
    "jump_back_very_long": ("Long jump back", ["Ctrl+Alt+Left"]),
    "jump_fwd_very_long": ("Long jump forward", ["Ctrl+Alt+Right"]),
    "goto_time": ("Jump to time", ["Ctrl+T"]),
    "faster": ("Faster", ["]", "+"]),
    "slower": ("Slower", ["[", "-"]),
    "normal_speed": ("Normal speed", ["="]),
    "ab_loop": ("A-B loop", ["Shift+L"]),
    "cycle_repeat": ("Repeat mode", ["L"]),
    "toggle_shuffle": ("Shuffle", ["R"]),
    "record": ("Record", ["Shift+R"]),
    # Chapters, titles and disc menus
    "next_chapter": ("Next chapter", ["Shift+N"]),
    "previous_chapter": ("Previous chapter", ["Shift+P"]),
    "next_title": ("Next title", ["Shift+B"]),
    "previous_title": ("Previous title", ["Shift+O"]),
    "disc_menu": ("Disc menu", ["Shift+M"]),
    # Only active while a disc menu is shown; ← → navigate menus too.
    "nav_up": ("Disc menu: up", ["Up"]),
    "nav_down": ("Disc menu: down", ["Down"]),
    "nav_left": ("Disc menu: left", []),
    "nav_right": ("Disc menu: right", []),
    "nav_activate": ("Disc menu: select", ["Return", "Enter"]),
    # Audio
    "volume_up": ("Volume up", ["Ctrl+Up", "Volume Up"]),
    "volume_down": ("Volume down", ["Ctrl+Down", "Volume Down"]),
    "mute": ("Mute", ["M", "Volume Mute"]),
    "cycle_audio_track": ("Cycle audio track", ["B"]),
    "audio_delay_down": ("Audio delay -50 ms", ["J"]),
    "audio_delay_up": ("Audio delay +50 ms", ["K"]),
    # Subtitles
    "cycle_subtitle": ("Cycle subtitle track", ["V"]),
    "toggle_subtitles": ("Toggle subtitles", ["Shift+V"]),
    "subtitle_delay_down": ("Subtitle delay -50 ms", ["G"]),
    "subtitle_delay_up": ("Subtitle delay +50 ms", ["H"]),
    # Video
    "fullscreen": ("Fullscreen", ["F", "F11"]),
    "leave_fullscreen": ("Leave fullscreen", ["Esc"]),
    "cycle_aspect": ("Cycle aspect ratio", ["A"]),
    "cycle_crop": ("Cycle crop", ["C"]),
    "cycle_zoom": ("Cycle zoom", ["Z"]),
    "cycle_deinterlace": ("Cycle deinterlace mode", ["D"]),
    "rotate": ("Rotate 90°", ["Ctrl+Alt+R"]),
    "flip_horizontal": ("Flip horizontally", []),
    "flip_vertical": ("Flip vertically", []),
    "snapshot": ("Take snapshot", ["Shift+S"]),
    # Interface
    "toggle_playlist": ("Toggle playlist", ["Ctrl+L"]),
    "quick_settings": ("Quick settings", ["Ctrl+E"]),
    "effects": ("Effects & filters", ["Ctrl+Shift+E"]),
    "media_info": ("Media information", ["Ctrl+I", "Ctrl+J"]),
    "settings": ("Settings", ["Ctrl+P"]),
    "show_time": ("Show position", ["T"]),
    "save_playlist": ("Save playlist", ["Ctrl+Y"]),
    "mini_player": ("Mini player", ["Ctrl+Shift+M"]),
    "always_on_top": ("Always on top (picture-in-picture)", ["Ctrl+Shift+T"]),
}


def resolve_shortcuts(overrides: dict | None = None) -> dict[str, list[str]]:
    """Merge user overrides (action -> list of keys) onto the defaults."""
    result = {action: list(keys) for action, (_, keys) in DEFAULT_SHORTCUTS.items()}
    for action, keys in (overrides or {}).items():
        if action in result and isinstance(keys, list) and all(isinstance(k, str) for k in keys):
            result[action] = list(keys)
    return result


def find_conflicts(bindings: dict[str, list[str]]) -> dict[str, list[str]]:
    """Return key -> actions for keys bound to more than one action."""
    seen: dict[str, list[str]] = {}
    for action, keys in bindings.items():
        for key in keys:
            seen.setdefault(key.lower(), []).append(action)
    return {k: v for k, v in seen.items() if len(v) > 1}
