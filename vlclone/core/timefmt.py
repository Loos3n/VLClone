"""Time formatting / parsing helpers (all times in milliseconds)."""

from __future__ import annotations


def format_ms(ms: int | float | None, force_hours: bool = False) -> str:
    """Format milliseconds as ``M:SS`` or ``H:MM:SS``."""
    if ms is None or ms < 0:
        return "--:--"
    total = int(ms // 1000)
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours or force_hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def parse_time(text: str) -> int | None:
    """Parse ``SS``, ``MM:SS`` or ``HH:MM:SS`` (seconds may be fractional) to ms."""
    text = text.strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) > 3:
        return None
    try:
        values = [float(p) for p in parts]
    except ValueError:
        return None
    if any(v < 0 for v in values):
        return None
    seconds = 0.0
    for v in values:
        seconds = seconds * 60 + v
    return int(round(seconds * 1000))


def format_delay(ms: int) -> str:
    """Format a signed delay such as subtitle or audio offset."""
    sign = "+" if ms > 0 else ("-" if ms < 0 else "")
    return f"{sign}{abs(ms)} ms"
