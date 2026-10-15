"""Register VLClone's file types and Explorer entries for the current user (no admin rights).

The installer writes the same entries (generated from ``core/associations``)
for whoever installs it; this is for the portable zip and for changing the
choice later from Settings → System or the welcome screen. Windows then
lists VLClone under "Open with" and Settings → Default apps, where the user
confirms the default (Windows protects that choice from programs).
"""

from __future__ import annotations

import os
import sys

from ..core.associations import (ASSOCIATION_TYPES, association_entries, context_menu_entries,
                                 default_candidates)


class Registry:
    """The few HKCU operations needed, so tests can use a dict instead (``MemoryRegistry``)."""

    def __init__(self) -> None:
        import winreg

        self.w = winreg
        self.root = winreg.HKEY_CURRENT_USER

    def get(self, key: str, name: str = "", merged: bool = False) -> str | None:
        root = self.w.HKEY_CLASSES_ROOT if merged else self.root
        if merged:
            key = key.split("\\", 2)[2]  # Software\Classes\.mkv -> .mkv
        try:
            with self.w.OpenKey(root, key) as k:
                value = self.w.QueryValueEx(k, name)[0]
                return value if isinstance(value, str) else None
        except OSError:
            return None

    def set(self, key: str, name: str, value: str) -> None:
        with self.w.CreateKeyEx(self.root, key, 0, self.w.KEY_SET_VALUE) as k:
            self.w.SetValueEx(k, name, 0, self.w.REG_SZ, value)

    def delete_value(self, key: str, name: str) -> None:
        try:
            with self.w.OpenKey(self.root, key, 0, self.w.KEY_SET_VALUE) as k:
                self.w.DeleteValue(k, name)
        except OSError:
            pass

    def delete_tree(self, key: str) -> None:
        try:
            self.w.DeleteTree(self.root, key) if hasattr(self.w, "DeleteTree") else self._delete_tree(key)
            self.w.DeleteKey(self.root, key)
        except OSError:
            pass

    def _delete_tree(self, key: str) -> None:
        with self.w.OpenKey(self.root, key, 0, self.w.KEY_ALL_ACCESS) as k:
            while True:
                try:
                    sub = self.w.EnumKey(k, 0)
                except OSError:
                    break
                self._delete_tree(f"{key}\\{sub}")
                self.w.DeleteKey(self.root, f"{key}\\{sub}")

    def notify(self) -> None:
        import ctypes

        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)  # SHCNE_ASSOCCHANGED


class MemoryRegistry:
    """In-memory stand-in for tests: {key_lower: {name: value}}, ``merged`` = other apps' defaults."""

    def __init__(self, merged: dict | None = None) -> None:
        self.data: dict[str, dict[str, str]] = {}
        self.merged = {k.lower(): v for k, v in (merged or {}).items()}
        self.notified = 0

    def get(self, key, name="", merged=False):
        if merged:
            ext = key.split("\\", 2)[2].lower()
            own = self.data.get(key.lower(), {}).get(name)
            return own if own is not None else self.merged.get(ext)
        return self.data.get(key.lower(), {}).get(name)

    def set(self, key, name, value):
        self.data.setdefault(key.lower(), {})[name] = value

    def delete_value(self, key, name):
        self.data.get(key.lower(), {}).pop(name, None)

    def delete_tree(self, key):
        prefix = key.lower()
        for k in [k for k in self.data if k == prefix or k.startswith(prefix + "\\")]:
            del self.data[k]

    def notify(self):
        self.notified += 1


def default_registry():
    return Registry() if sys.platform == "win32" else None


def can_register() -> bool:
    """Only the packaged VLClone.exe can be registered (from source there is no exe to point at)."""
    return sys.platform == "win32" and bool(getattr(sys, "frozen", False))


def exe_path() -> str:
    if not getattr(sys, "frozen", False):
        raise OSError("file types can only be registered from the packaged VLClone.exe")
    return sys.executable


def register(categories, context_menu: bool, exe: str | None = None, reg=None) -> None:
    reg = reg or default_registry()
    if reg is None:
        raise OSError("file associations are only available on Windows")
    exe = exe or exe_path()
    unregister(reg, notify=False)
    for e in association_entries(categories, exe) + (context_menu_entries(exe) if context_menu else []):
        reg.set(e.key, e.name, e.value)
    # Become the default only where no program is the default yet; Windows keeps the user's choice.
    for key, progid in default_candidates(categories):
        if not reg.get(key, "", merged=True):
            reg.set(key, "", progid)
    reg.notify()


def unregister(reg=None, notify: bool = True) -> None:
    """Remove everything VLClone may have written for this user (other apps' values are kept)."""
    reg = reg or default_registry()
    if reg is None:
        return
    exe = "x"
    all_cats = list(ASSOCIATION_TYPES)
    for key, progid in default_candidates(all_cats):
        if reg.get(key, "") == progid:
            reg.delete_value(key, "")
    owned = set()
    for e in association_entries(all_cats, exe) + context_menu_entries(exe):
        if e.owned_key:
            owned.add(e.owned_key)
        else:
            reg.delete_value(e.key, e.name)
    for key in sorted(owned, key=len, reverse=True):
        reg.delete_tree(key)
    if notify:
        reg.notify()


def registered(reg=None) -> tuple[set[str], bool]:
    """(categories with a ProgID, context menu present) for the current user."""
    reg = reg or default_registry()
    if reg is None:
        return set(), False
    cats = {c for c, (progid, _, _) in ASSOCIATION_TYPES.items()
            if reg.get(rf"Software\Classes\{progid}\shell\open\command", "")}
    ctx = bool(reg.get(r"Software\Classes\Directory\shell\VLClone.play\command", ""))
    return cats, ctx


def open_default_apps() -> None:
    """Windows Settings → Default apps, on VLClone's page where supported."""
    if sys.platform == "win32":
        try:
            os.startfile("ms-settings:defaultapps?registeredAppUser=VLClone")
        except OSError:
            os.startfile("ms-settings:defaultapps")
