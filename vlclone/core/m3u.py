"""Read and write .m3u / .m3u8 playlists (extended M3U)."""

from __future__ import annotations

import os
from pathlib import Path

from .media_sources import is_url, mrl_to_path, path_to_mrl
from .playlist import PlaylistItem


def read_m3u(path: str) -> list[PlaylistItem]:
    raw = Path(path).read_bytes()
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    base = Path(path).resolve().parent
    items: list[PlaylistItem] = []
    pending_title = ""
    pending_duration = -1
    pending_options: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#EXTINF:"):
            info = line[len("#EXTINF:"):]
            duration, _, title = info.partition(",")
            try:
                seconds = float(duration.split()[0]) if duration.strip() else -1
            except ValueError:
                seconds = -1
            pending_duration = int(seconds * 1000) if seconds >= 0 else -1
            pending_title = title.strip()
            continue
        if line.startswith("#EXTVLCOPT:"):
            option = line[len("#EXTVLCOPT:"):].strip()
            if option:
                pending_options.append(option if option.startswith(":") else ":" + option)
            continue
        if line.startswith("#"):
            continue
        if is_url(line):
            mrl = line
        else:
            local = Path(line.replace("\\", os.sep) if os.sep != "\\" else line)
            if not local.is_absolute():
                local = base / local
            mrl = path_to_mrl(str(local))
        items.append(PlaylistItem(mrl, pending_title, pending_duration, options=pending_options))
        pending_options = []
        pending_title = ""
        pending_duration = -1
    return items


def write_m3u(path: str, items: list[PlaylistItem]) -> None:
    """Write an extended M3U (UTF-8). Local files are stored relative when possible."""
    base = Path(path).resolve().parent
    lines = ["#EXTM3U"]
    for item in items:
        seconds = item.duration_ms // 1000 if item.duration_ms and item.duration_ms > 0 else -1
        lines.append(f"#EXTINF:{seconds},{item.title}")
        lines.extend(f"#EXTVLCOPT:{opt.lstrip(':')}" for opt in item.options)
        local = mrl_to_path(item.mrl)
        if local is None:
            lines.append(item.mrl)
        else:
            try:
                lines.append(os.path.relpath(local, base))
            except ValueError:  # different drive on Windows
                lines.append(local)
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
