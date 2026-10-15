"""Remembering where the window was: size, position, monitor, maximized/fullscreen.

Geometry is stored in Qt's logical (device-independent) pixels together with
the monitor's name, so a change of display scaling or a missing monitor can
be handled instead of restoring a window off screen. ``plan_restore`` is
pure so the multi-monitor / high-DPI cases are unit-tested; the window only
feeds it the current screens.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

DEFAULT_SIZE = (1280, 760)
MIN_SIZE = (640, 400)
MINI_DEFAULT = (420, 236)
MINI_MIN = (280, 158)


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h

    def intersection_area(self, other: "Rect") -> int:
        w = min(self.right, other.right) - max(self.x, other.x)
        h = min(self.bottom, other.bottom) - max(self.y, other.y)
        return max(0, w) * max(0, h)

    def contains_point(self, x: int, y: int) -> bool:
        return self.x <= x < self.right and self.y <= y < self.bottom


@dataclass(frozen=True)
class ScreenInfo:
    name: str
    available: Rect       # work area (no taskbar), logical pixels
    dpr: float = 1.0
    primary: bool = False


@dataclass
class SavedWindow:
    rect: Rect | None = None   # normal (not maximized / fullscreen) geometry
    screen: str = ""
    screen_rect: Rect | None = None  # that screen's work area when saved
    dpr: float = 1.0
    maximized: bool = False
    fullscreen: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        d["rect"] = list(asdict(self.rect).values()) if self.rect else None
        d["screen_rect"] = list(asdict(self.screen_rect).values()) if self.screen_rect else None
        return d

    @classmethod
    def from_dict(cls, data) -> "SavedWindow | None":
        if not isinstance(data, dict):
            return None

        def rect(value) -> Rect | None:
            if (isinstance(value, list) and len(value) == 4
                    and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)):
                x, y, w, h = (int(v) for v in value)
                return Rect(x, y, w, h) if w > 0 and h > 0 else None
            return None

        dpr = data.get("dpr", 1.0)
        return cls(rect(data.get("rect")), str(data.get("screen") or ""), rect(data.get("screen_rect")),
                   float(dpr) if isinstance(dpr, (int, float)) and dpr > 0 else 1.0,
                   data.get("maximized") is True, data.get("fullscreen") is True)


@dataclass(frozen=True)
class RestorePlan:
    rect: Rect
    screen: str
    maximized: bool
    fullscreen: bool


def _clamp_into(rect: Rect, area: Rect, min_size: tuple[int, int]) -> Rect:
    w = max(min(rect.w, area.w), min(min_size[0], area.w))
    h = max(min(rect.h, area.h), min(min_size[1], area.h))
    x = min(max(rect.x, area.x), area.right - w)
    y = min(max(rect.y, area.y), area.bottom - h)
    return Rect(x, y, w, h)


def _centered(size: tuple[int, int], area: Rect, min_size: tuple[int, int]) -> Rect:
    w = max(min(size[0], area.w), min(min_size[0], area.w))
    h = max(min(size[1], area.h), min(min_size[1], area.h))
    return Rect(area.x + (area.w - w) // 2, area.y + (area.h - h) // 2, w, h)


def primary_screen(screens: list[ScreenInfo]) -> ScreenInfo:
    return next((s for s in screens if s.primary), screens[0])


def plan_restore(saved: SavedWindow | None, screens: list[ScreenInfo],
                 default_size: tuple[int, int] = DEFAULT_SIZE,
                 min_size: tuple[int, int] = MIN_SIZE) -> RestorePlan:
    """Where to put the window now, given what was saved and the monitors present.

    * Same monitor still there: the saved rectangle, pulled fully onto its work
      area (after a resolution or scaling change it may no longer fit).
    * Monitor gone (laptop undocked): the monitor the rectangle overlaps most,
      else the primary, with the window centred at its saved size.
    * Nothing saved / garbage: default size centred on the primary monitor.
    Maximized and fullscreen are kept, on the chosen monitor.
    """
    if not screens:
        raise ValueError("no screens")
    primary = primary_screen(screens)
    if saved is None or saved.rect is None:
        return RestorePlan(_centered(default_size, primary.available, min_size), primary.name,
                           bool(saved and saved.maximized), bool(saved and saved.fullscreen))
    rect = saved.rect
    by_name = next((s for s in screens if saved.screen and s.name == saved.screen), None)
    if by_name is not None:
        target = by_name
        if (saved.screen_rect is not None and saved.screen_rect != by_name.available
                and by_name.available.intersection_area(rect) == 0):
            # Same monitor, moved in the virtual desktop (arrangement changed): same relative spot.
            rect = _translate(rect, saved.screen_rect, by_name.available)
        placed = _clamp_into(rect, target.available, min_size)
    else:
        best = max(screens, key=lambda s: s.available.intersection_area(rect))
        if best.available.intersection_area(rect) >= rect.w * rect.h // 4:
            target = best
            placed = _clamp_into(rect, target.available, min_size)
        else:
            target = primary
            placed = _centered((rect.w, rect.h), target.available, min_size)
    return RestorePlan(placed, target.name, saved.maximized, saved.fullscreen)


def _translate(rect: Rect, old_area: Rect, new_area: Rect) -> Rect:
    """Keep the window at the same relative spot when its monitor moved or changed size."""
    fx = (rect.x - old_area.x) / max(1, old_area.w)
    fy = (rect.y - old_area.y) / max(1, old_area.h)
    return Rect(new_area.x + int(fx * new_area.w), new_area.y + int(fy * new_area.h), rect.w, rect.h)


def plan_mini(saved, screens: list[ScreenInfo], margin: int = 24) -> Rect:
    """The mini player's rectangle: where it was, else the bottom-right corner of the primary monitor."""
    if not screens:
        raise ValueError("no screens")
    rect = None
    if isinstance(saved, list) and len(saved) == 4 and all(isinstance(v, int) for v in saved):
        rect = Rect(*saved) if saved[2] > 0 and saved[3] > 0 else None
    if rect is not None:
        best = max(screens, key=lambda s: s.available.intersection_area(rect))
        if best.available.intersection_area(rect) > 0:
            return _clamp_into(rect, best.available, MINI_MIN)
    area = primary_screen(screens).available
    w, h = MINI_DEFAULT
    return Rect(area.right - w - margin, area.bottom - h - margin, w, h)
