"""Glue between the player and the OS: media overlay + media keys, taskbar buttons, keep-awake.

``MainWindow`` calls :meth:`DesktopIntegration.tick` from its 100 ms timer
and forwards native window messages; decisions come from ``core/desktop``,
OS calls from ``vlclone/integration``. Every piece is optional and switched
by a setting (System section).
"""

from __future__ import annotations

import logging
import sys
from typing import Callable

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage, QPainter

from ..core.desktop import CommandDebouncer, SessionState, should_inhibit_sleep, taskbar_state
from ..core.settings import Settings
from ..integration.power import SleepInhibitor
from .icons import icon

log = logging.getLogger("vlclone.desktop")

class DesktopIntegration:
    def __init__(self, settings: Settings, run: Callable[[str], None]) -> None:
        self.settings = settings
        self.run = run  # executes a command: "play", "pause", "play_pause", "next", "previous", "stop"
        self.debounce = CommandDebouncer()
        self.sleep = SleepInhibitor()
        self.taskbar = None
        self.media = None
        self.session = SessionState()
        self._hwnd = 0

    # ----------------------------------------------------------- lifecycle
    def attach(self, hwnd: int) -> None:
        """Create the Windows pieces for the main window (call once it has a native handle)."""
        self._hwnd = hwnd
        if sys.platform != "win32":
            return
        self.apply_settings()

    def apply_settings(self) -> None:
        if sys.platform == "win32" and self._hwnd:
            if self.settings.taskbar_controls and self.taskbar is None:
                try:
                    from ..integration.win_taskbar import TaskbarControls

                    self.taskbar = TaskbarControls(self._hwnd, self._from_os, taskbar_icon)
                except Exception as exc:
                    log.warning("taskbar controls unavailable: %s", exc)
            if self.settings.media_keys and self.media is None:
                from ..integration.win_media import create_media_overlay

                self.media = create_media_overlay()
            if not self.settings.media_keys and self.media is not None:
                self.media.close()
                self.media = None
        if not self.settings.prevent_sleep:
            self.sleep.set_inhibited(False)

    def close(self) -> None:
        self.sleep.release()
        if self.media is not None:
            self.media.close()
            self.media = None
        if self.taskbar is not None:
            self.taskbar.close()
            self.taskbar = None

    # --------------------------------------------------------------- input
    def handle_native(self, msg) -> bool:
        return self.taskbar is not None and self.taskbar.handle_message(msg)

    def shortcut_used(self, command: str) -> None:
        """A Qt shortcut / menu ran ``command``; a copy from the media overlay right after is dropped."""
        self.debounce.accept(command, "shortcut")

    def _from_os(self, command: str) -> None:
        key = "play_pause" if command in ("play", "pause") else command
        if self.debounce.accept(key, "os"):
            self.run(command)

    # ---------------------------------------------------------------- tick
    def tick(self, session: SessionState) -> None:
        self.session = session
        if self.media is not None:
            for command in self.media.poll():
                self._from_os(command)
            if self.settings.media_keys:
                self.media.update(session)
        if self.taskbar is not None:
            if self.settings.taskbar_controls:
                self.taskbar.update(taskbar_state(session))
            else:
                self.taskbar.update(taskbar_state(SessionState()))  # clears progress
        self.sleep.set_inhibited(should_inhibit_sleep(session, self.settings.prevent_sleep))


def taskbar_is_light() -> bool:
    """The taskbar and its thumbnail flyout follow Windows' *system* theme, not the app's."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return winreg.QueryValueEx(key, "SystemUsesLightTheme")[0] == 1
    except (ImportError, OSError):
        return False


def taskbar_icon(name: str) -> QImage:
    """A thumbnail-toolbar icon (16 px logical, at the screen's scale) readable on the taskbar."""
    from PySide6.QtGui import QGuiApplication

    screen = QGuiApplication.primaryScreen()
    scale = screen.devicePixelRatio() if screen else 1.0
    size = max(16, int(round(16 * scale)))
    img = QImage(QSize(size, size), QImage.Format.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    icon(name, "#1f1f1f" if taskbar_is_light() else "#ffffff").paint(p, 0, 0, size, size)
    p.end()
    return img
