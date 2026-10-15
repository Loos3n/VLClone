"""Playlist persistence between sessions."""

from __future__ import annotations

from pathlib import Path

from .jsonstore import load_json, save_json
from .paths import config_dir
from .playlist import Playlist, PlaylistItem

MAX_ITEMS = 5000


def default_path() -> Path:
    return config_dir() / "playlist_session.json"


def save_playlist(playlist: Playlist, path: Path | None = None) -> None:
    """Write the playlist and the current item (without autoplay state)."""
    items = []
    for it in list(playlist)[:MAX_ITEMS]:
        entry = {"mrl": it.mrl, "title": it.title, "duration": it.duration_ms}
        if it.options:
            entry["options"] = list(it.options)
        items.append(entry)
    save_json(path or default_path(), {"version": 1, "current": playlist.current_index, "items": items})


def restore_playlist(playlist: Playlist, path: Path | None = None) -> int:
    """Append the saved items to ``playlist`` and select the saved current one.

    Returns the number of restored items. Malformed entries are skipped.
    """
    raw = load_json(path or default_path(), {})
    if not isinstance(raw, dict) or not isinstance(raw.get("items"), list):
        return 0
    items = []
    for entry in raw["items"][:MAX_ITEMS]:
        if not isinstance(entry, dict) or not isinstance(entry.get("mrl"), str) or not entry["mrl"]:
            continue
        title = entry.get("title") if isinstance(entry.get("title"), str) else ""
        duration = entry.get("duration")
        duration = int(duration) if isinstance(duration, (int, float)) and not isinstance(duration, bool) else -1
        options = entry.get("options")
        options = [o for o in options if isinstance(o, str)] if isinstance(options, list) else []
        items.append(PlaylistItem(entry["mrl"], title, duration, options=options))
    if not items:
        return 0
    offset = len(playlist)
    playlist.add(items)
    current = raw.get("current")
    if isinstance(current, int) and not isinstance(current, bool) and 0 <= current < len(items):
        playlist.set_current(offset + current)
    return len(items)


def clear_saved_playlist(path: Path | None = None) -> None:
    target = path or default_path()
    try:
        target.unlink()
    except OSError:
        pass
