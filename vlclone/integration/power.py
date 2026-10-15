"""Keep the display awake while a video plays.

Windows: ``SetThreadExecutionState(ES_CONTINUOUS | ES_DISPLAY_REQUIRED |
ES_SYSTEM_REQUIRED)`` — the same request every video player makes; it is
per thread, so it is always called from the Qt main thread.
Linux: the freedesktop ScreenSaver D-Bus inhibit (GNOME, KDE, Xfce…), when
QtDBus and a session bus are available. Elsewhere: nothing.
"""

from __future__ import annotations

import logging
import sys

log = logging.getLogger("vlclone.power")

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002


class SleepInhibitor:
    def __init__(self) -> None:
        self.active = False
        self.supported = sys.platform == "win32" or sys.platform.startswith("linux")
        self._cookie: int | None = None
        self._iface = None

    def set_inhibited(self, on: bool) -> bool:
        """Request or release; returns True if the OS accepted the request."""
        if on == self.active:
            return True
        ok = False
        try:
            if sys.platform == "win32":
                ok = self._windows(on)
            elif sys.platform.startswith("linux"):
                ok = self._dbus(on)
        except Exception as exc:  # never let power management break playback
            log.warning("keep-awake %s failed: %s", "request" if on else "release", exc)
        self.active = on if ok else False
        if ok:
            log.info("keep-awake %s", "on" if on else "off")
        return ok

    @staticmethod
    def _windows(on: bool) -> bool:
        import ctypes

        flags = ES_CONTINUOUS | (ES_DISPLAY_REQUIRED | ES_SYSTEM_REQUIRED if on else 0)
        kernel32 = ctypes.windll.kernel32
        kernel32.SetThreadExecutionState.restype = ctypes.c_uint32
        return kernel32.SetThreadExecutionState(ctypes.c_uint32(flags)) != 0

    def _dbus(self, on: bool) -> bool:
        from PySide6.QtDBus import QDBusConnection, QDBusInterface

        if self._iface is None:
            bus = QDBusConnection.sessionBus()
            if not bus.isConnected():
                return False
            self._iface = QDBusInterface("org.freedesktop.ScreenSaver", "/org/freedesktop/ScreenSaver",
                                         "org.freedesktop.ScreenSaver", bus)
        if not self._iface.isValid():
            return False
        if on:
            reply = self._iface.call("Inhibit", "VLClone", "Playing a video")
            args = reply.arguments()
            if not args:
                return False
            self._cookie = int(args[0])
            return True
        if self._cookie is not None:
            self._iface.call("UnInhibit", self._cookie)
            self._cookie = None
        return True

    def release(self) -> None:
        self.set_inhibited(False)
