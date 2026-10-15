"""Value stepping helpers for speed and volume (mirrors VLC behaviour)."""

from __future__ import annotations

#: Speeds offered in the UI; also the ladder used by "faster"/"slower".
RATE_PRESETS = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 3.0, 4.0]
MIN_RATE = 0.03125
MAX_RATE = 32.0
FINE_RATE_STEP = 0.1

MAX_VOLUME = 200  # percent; >100 is software boost, like VLC
VOLUME_STEP = 5


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def step_rate(rate: float, direction: int) -> float:
    """Move to the next preset above (direction > 0) or below the current rate."""
    eps = 1e-6
    if direction > 0:
        for preset in RATE_PRESETS:
            if preset > rate + eps:
                return preset
        return clamp(rate * 2, MIN_RATE, MAX_RATE)
    for preset in reversed(RATE_PRESETS):
        if preset < rate - eps:
            return preset
    return clamp(rate / 2, MIN_RATE, MAX_RATE)


def fine_step_rate(rate: float, direction: int) -> float:
    return round(clamp(rate + FINE_RATE_STEP * direction, 0.1, MAX_RATE), 2)


def step_volume(volume: int, direction: int, step: int = VOLUME_STEP, maximum: int = MAX_VOLUME) -> int:
    """Step volume, snapping to multiples of ``step``."""
    if direction > 0:
        value = (volume // step + 1) * step
    else:
        value = (volume // step) * step if volume % step else volume - step
    return int(clamp(value, 0, maximum))


def cycle(options: list, current, direction: int = 1):
    """Return the option after ``current`` (wrapping); first if not present."""
    if not options:
        return current
    try:
        idx = options.index(current)
    except ValueError:
        return options[0]
    return options[(idx + direction) % len(options)]
