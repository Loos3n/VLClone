"""Windows file associations and Explorer context-menu entries, as data.

One description serves two writers:

* ``packaging/make_associations.py`` turns it into Inno Setup ``[Registry]``
  lines for the installer (opt-in tasks, removed on uninstall);
* ``integration/winreg_assoc.py`` writes the same keys for the current user
  from the app (Settings → System, the welcome screen) for the portable zip.

Windows does not let a program make itself the default player silently
(the choice is protected by a hash in ``UserChoice``). So VLClone registers
the proper way: ProgIDs, ``OpenWithProgids`` and *Capabilities*, so it shows
up in "Open with" and in Settings → Default apps; it only becomes the
default for an extension that has no default yet. Everything is relative to
``HKCU``/``HKLM`` (Inno's ``HKA``).
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import APP_NAME

CLASSES = r"Software\Classes"
CAPABILITIES = rf"Software\{APP_NAME}\Capabilities"

# The common types, not every extension VLClone can play (.ps, .rec, .txt… are not worth claiming).
ASSOCIATION_TYPES: dict[str, tuple[str, str, tuple[str, ...]]] = {
    # category: (ProgID, friendly type name, extensions)
    "video": (f"{APP_NAME}.Video", "Video file", (
        "3g2", "3gp", "asf", "avi", "divx", "f4v", "flv", "m2ts", "m4v", "mkv", "mov", "mp4",
        "mpeg", "mpg", "mts", "ogm", "ogv", "ts", "vob", "webm", "wmv")),
    "audio": (f"{APP_NAME}.Audio", "Audio file", (
        "aac", "ac3", "aif", "aiff", "ape", "dts", "flac", "m4a", "m4b", "mka", "mp2", "mp3",
        "oga", "ogg", "opus", "wav", "wma", "wv")),
    "playlist": (f"{APP_NAME}.Playlist", "Playlist", ("m3u", "m3u8")),
}
CATEGORY_LABELS = {"video": "Video files", "audio": "Audio files", "playlist": "Playlists (.m3u, .m3u8)"}

PLAY_VERB = f"{APP_NAME}.play"
ENQUEUE_VERB = f"{APP_NAME}.enqueue"


@dataclass(frozen=True)
class RegEntry:
    """One registry value. ``owned`` keys are ours and deleted whole on removal;
    otherwise only this value is removed (the key belongs to Windows or other apps)."""
    key: str           # relative to HKCU/HKLM
    name: str          # "" = the key's default value
    value: str
    owned_key: str = ""  # the top-most key VLClone owns, deleted on removal ("" = shared key)
    category: str = ""   # "video" / "audio" / "playlist" / "context" / "" (always)


def _cmd(exe: str, flag: str = "") -> str:
    return f'"{exe}" {flag + " " if flag else ""}"%1"'


def progid_entries(category: str, exe: str) -> list[RegEntry]:
    progid, type_name, _ = ASSOCIATION_TYPES[category]
    base = rf"{CLASSES}\{progid}"
    own = base
    return [
        RegEntry(base, "", f"{type_name} ({APP_NAME})", own, category),
        RegEntry(base, "FriendlyTypeName", f"{type_name} ({APP_NAME})", own, category),
        RegEntry(rf"{base}\DefaultIcon", "", f"{exe},0", own, category),
        RegEntry(rf"{base}\shell", "", "open", own, category),
        RegEntry(rf"{base}\shell\open", "", f"Play with {APP_NAME}", own, category),
        RegEntry(rf"{base}\shell\open", "MultiSelectModel", "Player", own, category),
        RegEntry(rf"{base}\shell\open\command", "", _cmd(exe), own, category),
        RegEntry(rf"{base}\shell\enqueue", "", f"Add to {APP_NAME} playlist", own, category),
        RegEntry(rf"{base}\shell\enqueue", "MultiSelectModel", "Player", own, category),
        RegEntry(rf"{base}\shell\enqueue\command", "", _cmd(exe, "--enqueue"), own, category),
    ]


def association_entries(categories, exe: str) -> list[RegEntry]:
    """ProgIDs, Open-with lists and Capabilities for the chosen categories."""
    entries: list[RegEntry] = []
    cats = [c for c in ASSOCIATION_TYPES if c in set(categories)]
    if not cats:
        return entries
    app_key = rf"Software\{APP_NAME}"
    entries += [
        RegEntry(CAPABILITIES, "ApplicationName", APP_NAME, app_key),
        RegEntry(CAPABILITIES, "ApplicationDescription",
                 f"{APP_NAME}, a media player built on VLC's engine", app_key),
        RegEntry(r"Software\RegisteredApplications", APP_NAME, CAPABILITIES),
    ]
    for cat in cats:
        progid, _, exts = ASSOCIATION_TYPES[cat]
        entries += progid_entries(cat, exe)
        for ext in exts:
            entries.append(RegEntry(rf"{CLASSES}\.{ext}\OpenWithProgids", progid, "", "", cat))
            entries.append(RegEntry(rf"{CAPABILITIES}\FileAssociations", f".{ext}", progid, app_key, cat))
    return entries


def context_menu_entries(exe: str) -> list[RegEntry]:
    """"Play with VLClone" / "Add to VLClone playlist" on media files and folders (any default player)."""
    entries: list[RegEntry] = []
    targets = [rf"{CLASSES}\SystemFileAssociations\.{ext}"
               for _, _, exts in ASSOCIATION_TYPES.values() for ext in exts]
    targets.append(rf"{CLASSES}\Directory")
    for target in targets:
        for verb, label, flag in ((PLAY_VERB, f"Play with {APP_NAME}", "--play"),
                                  (ENQUEUE_VERB, f"Add to {APP_NAME} playlist", "--enqueue")):
            key = rf"{target}\shell\{verb}"
            entries += [
                RegEntry(key, "", label, key, "context"),
                RegEntry(key, "Icon", f"{exe},0", key, "context"),
                RegEntry(key, "MultiSelectModel", "Player", key, "context"),
                RegEntry(rf"{key}\command", "", _cmd(exe, flag), key, "context"),
            ]
    return entries


def default_candidates(categories) -> list[tuple[str, str]]:
    """(extension key, ProgID) pairs VLClone may claim as default when nothing else has."""
    out = []
    for cat in ASSOCIATION_TYPES:
        if cat in set(categories):
            progid, _, exts = ASSOCIATION_TYPES[cat]
            out += [(rf"{CLASSES}\.{ext}", progid) for ext in exts]
    return out


def all_extensions() -> list[str]:
    return sorted({e for _, _, exts in ASSOCIATION_TYPES.values() for e in exts})
