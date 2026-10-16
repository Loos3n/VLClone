"""Media library storage: watched folders, media metadata and play history in SQLite.

The database lives in the app's data folder (``paths.data_dir()``). Each
thread gets its own connection (the scanner writes from a worker thread,
the UI reads from the main thread); WAL mode lets them work concurrently.
Free of Qt and libVLC: metadata arrives as :class:`MediaMeta` from a probe.
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .media_sources import AUDIO_EXTENSIONS, VIDEO_EXTENSIONS, display_name, extension, mrl_to_path, path_to_mrl
from .paths import data_dir

FOLDER_KINDS = {"video": "Videos", "music": "Music"}
UNKNOWN_ARTIST = "Unknown artist"
UNKNOWN_ALBUM = "Unknown album"

#: sort key -> (label, SQL ORDER BY). Whitelisted: never interpolate user text.
SORTS: dict[str, tuple[str, str]] = {
    "title": ("Title", "m.title COLLATE NOCASE, m.path"),
    "added": ("Recently added", "m.added_at DESC, m.title COLLATE NOCASE"),
    "duration": ("Duration", "m.duration_ms DESC, m.title COLLATE NOCASE"),
    "played": ("Last played", "h.last_played IS NULL, h.last_played DESC, m.title COLLATE NOCASE"),
    "artist": ("Artist", "m.artist COLLATE NOCASE, m.album COLLATE NOCASE, m.track, m.title COLLATE NOCASE"),
    "album": ("Album", "m.album COLLATE NOCASE, m.track, m.title COLLATE NOCASE"),
    "year": ("Year", "m.year DESC, m.title COLLATE NOCASE"),
    "size": ("File size", "m.size DESC"),
    "folder": ("Folder", "m.path COLLATE NOCASE"),
}


@dataclass
class MediaMeta:
    """What a probe found out about a file."""

    title: str = ""
    artist: str = ""
    album: str = ""
    album_artist: str = ""
    track: int = 0
    genre: str = ""
    year: str = ""
    duration_ms: int = -1
    width: int = 0
    height: int = 0
    has_video: bool = False


def _track_number(value) -> int:
    m = re.match(r"\s*(\d+)", str(value or ""))
    return int(m.group(1)) if m else 0


def clean_meta(meta: MediaMeta, path: str) -> MediaMeta:
    """Normalise probe output: real titles only, numeric track, 4-digit year."""
    stem = Path(path).stem
    title = (meta.title or "").strip()
    if not title or title in (Path(path).name, stem) or "://" in title:
        title = stem
    year = re.search(r"\d{4}", meta.year or "")
    return MediaMeta(
        title=title, artist=(meta.artist or "").strip(), album=(meta.album or "").strip(),
        album_artist=(meta.album_artist or "").strip(), track=_track_number(meta.track),
        genre=(meta.genre or "").strip(), year=year.group(0) if year else "",
        duration_ms=int(meta.duration_ms) if meta.duration_ms and meta.duration_ms > 0 else -1,
        width=max(0, int(meta.width or 0)), height=max(0, int(meta.height or 0)), has_video=bool(meta.has_video),
    )


@dataclass
class LibraryItem:
    path: str
    kind: str  # "video" | "audio"
    title: str
    id: int = 0
    artist: str = ""
    album: str = ""
    album_artist: str = ""
    track: int = 0
    genre: str = ""
    year: str = ""
    duration_ms: int = -1
    width: int = 0
    height: int = 0
    size: int = 0
    added_at: float = 0.0
    thumb: str | None = None
    last_played: float | None = None
    play_count: int = 0
    position_ms: int = 0  # resume position (continue watching)
    in_library: bool = True
    mrl: str = ""

    def __post_init__(self) -> None:
        if not self.mrl:
            self.mrl = path_to_mrl(self.path) if self.path else ""

    @property
    def display_artist(self) -> str:
        return self.album_artist or self.artist or UNKNOWN_ARTIST

    @property
    def display_album(self) -> str:
        return self.album or UNKNOWN_ALBUM

    @property
    def resolution(self) -> str:
        return f"{self.width}×{self.height}" if self.width and self.height else ""

    @property
    def progress(self) -> float:
        return min(1.0, self.position_ms / self.duration_ms) if self.duration_ms > 0 and self.position_ms else 0.0


@dataclass
class Album:
    name: str
    artist: str
    year: str = ""
    tracks: list[LibraryItem] = field(default_factory=list)

    @property
    def duration_ms(self) -> int:
        return sum(t.duration_ms for t in self.tracks if t.duration_ms > 0)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS folders (
    id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL, kind TEXT NOT NULL, added_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS media (
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE NOT NULL, mrl TEXT NOT NULL, folder_id INTEGER NOT NULL REFERENCES folders(id),
    kind TEXT NOT NULL, title TEXT NOT NULL, artist TEXT DEFAULT '', album TEXT DEFAULT '',
    album_artist TEXT DEFAULT '', track INTEGER DEFAULT 0, genre TEXT DEFAULT '', year TEXT DEFAULT '',
    duration_ms INTEGER DEFAULT -1, width INTEGER DEFAULT 0, height INTEGER DEFAULT 0,
    size INTEGER DEFAULT 0, mtime REAL DEFAULT 0, added_at REAL NOT NULL,
    thumb TEXT  -- NULL: not tried yet, '': could not be made
);
CREATE INDEX IF NOT EXISTS media_kind ON media(kind);
CREATE INDEX IF NOT EXISTS media_folder ON media(folder_id);
CREATE INDEX IF NOT EXISTS media_mrl ON media(mrl);
CREATE TABLE IF NOT EXISTS history (
    mrl TEXT PRIMARY KEY, title TEXT NOT NULL, last_played REAL NOT NULL, play_count INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS history_time ON history(last_played);
"""

_ITEM_COLUMNS = ("m.id, m.path, m.mrl, m.kind, m.title, m.artist, m.album, m.album_artist, m.track, m.genre, "
                 "m.year, m.duration_ms, m.width, m.height, m.size, m.added_at, m.thumb, h.last_played, "
                 "h.play_count")


def _item(row) -> LibraryItem:
    return LibraryItem(
        id=row[0], path=row[1], mrl=row[2], kind=row[3], title=row[4], artist=row[5], album=row[6],
        album_artist=row[7], track=row[8], genre=row[9], year=row[10], duration_ms=row[11], width=row[12],
        height=row[13], size=row[14], added_at=row[15], thumb=row[16] or None, last_played=row[17],
        play_count=row[18] or 0)


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _inside(child: str, parent: str) -> bool:
    child, parent = _norm(child), _norm(parent)
    return child == parent or child.startswith(parent.rstrip(os.sep) + os.sep)


def kind_for_path(path: str) -> str | None:
    ext = extension(path)
    if ext in VIDEO_EXTENSIONS:
        return "video"
    if ext in AUDIO_EXTENSIONS:
        return "audio"
    return None


def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


class LibraryDB:
    SCHEMA_VERSION = 1
    HISTORY_LIMIT = 1000

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else data_dir() / "library.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._write_lock = threading.Lock()
        conn = self.conn
        with self._write_lock, conn:
            conn.executescript(_SCHEMA)
            conn.execute("INSERT OR IGNORE INTO meta VALUES ('schema', ?)", (str(self.SCHEMA_VERSION),))

    @property
    def conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.path), timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def _write(self, sql: str, params=()) -> sqlite3.Cursor:
        with self._write_lock, self.conn:
            return self.conn.execute(sql, params)

    # -------------------------------------------------------------- folders
    def folders(self) -> list[tuple[str, str]]:
        return [(r[0], r[1]) for r in self.conn.execute("SELECT path, kind FROM folders ORDER BY kind, path")]

    def add_folder(self, path: str, kind: str) -> str | None:
        """Watch a folder. Returns an error message, or None when added."""
        if kind not in FOLDER_KINDS:
            return f"Unknown folder type {kind!r}"
        path = os.path.abspath(path)
        if not os.path.isdir(path):
            return "That folder does not exist"
        for existing, existing_kind in self.folders():
            if _norm(existing) == _norm(path):
                if existing_kind == kind:
                    return "Already in the library"
                return f"That folder is already in the library as {FOLDER_KINDS[existing_kind]}"
            if existing_kind == kind and _inside(path, existing):
                return f"Already in the library (inside {existing})"
        with self._write_lock, self.conn as conn:
            for existing, existing_kind in self.folders():
                if existing_kind == kind and _inside(existing, path):
                    self._remove_folder(conn, existing)  # the new folder covers it
            conn.execute("INSERT INTO folders(path, kind, added_at) VALUES (?, ?, ?)", (path, kind, time.time()))
        return None

    @staticmethod
    def _remove_folder(conn, path: str) -> None:
        row = conn.execute("SELECT id FROM folders WHERE path = ?", (path,)).fetchone()
        if row:
            conn.execute("DELETE FROM media WHERE folder_id = ?", (row[0],))
            conn.execute("DELETE FROM folders WHERE id = ?", (row[0],))

    def remove_folder(self, path: str) -> None:
        with self._write_lock, self.conn as conn:
            self._remove_folder(conn, path)

    def folder_id(self, path: str) -> int | None:
        row = self.conn.execute("SELECT id FROM folders WHERE path = ?", (path,)).fetchone()
        return row[0] if row else None

    # ---------------------------------------------------------------- media
    def known_files(self, folder: str) -> dict[str, tuple[int, float]]:
        """path -> (size, mtime) for files indexed under ``folder``."""
        rows = self.conn.execute(
            "SELECT m.path, m.size, m.mtime FROM media m JOIN folders f ON m.folder_id = f.id WHERE f.path = ?",
            (folder,))
        return {r[0]: (r[1], r[2]) for r in rows}

    def upsert(self, path: str, folder: str, kind: str, size: int, mtime: float, meta: MediaMeta) -> None:
        folder_id = self.folder_id(folder)
        if folder_id is None:
            return  # folder removed while scanning
        m = clean_meta(meta, path)
        self._write(
            """INSERT INTO media(path, mrl, folder_id, kind, title, artist, album, album_artist, track, genre,
                   year, duration_ms, width, height, size, mtime, added_at, thumb)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
               ON CONFLICT(path) DO UPDATE SET
                   folder_id = excluded.folder_id, kind = excluded.kind, title = excluded.title,
                   artist = excluded.artist, album = excluded.album, album_artist = excluded.album_artist,
                   track = excluded.track, genre = excluded.genre, year = excluded.year,
                   duration_ms = excluded.duration_ms, width = excluded.width, height = excluded.height,
                   size = excluded.size, mtime = excluded.mtime, thumb = NULL""",
            (path, path_to_mrl(path), folder_id, kind, m.title, m.artist, m.album, m.album_artist, m.track,
             m.genre, m.year, m.duration_ms, m.width, m.height, int(size), float(mtime), time.time()))

    def remove_paths(self, paths) -> int:
        paths = list(paths)
        with self._write_lock, self.conn as conn:
            for path in paths:
                conn.execute("DELETE FROM media WHERE path = ?", (path,))
        return len(paths)

    def set_thumb(self, path: str, thumb: str) -> None:
        self._write("UPDATE media SET thumb = ? WHERE path = ?", (thumb, path))

    def missing_thumbs(self, limit: int = 1000) -> list[tuple[str, int, float]]:
        """(path, duration, mtime) of videos whose thumbnail was never attempted."""
        rows = self.conn.execute("SELECT path, duration_ms, mtime FROM media WHERE kind = 'video' AND thumb IS NULL "
                                 "ORDER BY added_at DESC LIMIT ?", (limit,))
        return [(r[0], r[1], r[2]) for r in rows]

    def items(self, kind: str | None = None, search: str = "", sort: str = "title",
              limit: int | None = None) -> list[LibraryItem]:
        where, params = [], []
        if kind:
            where.append("m.kind = ?")
            params.append(kind)
        for word in search.split():
            where.append("(m.title LIKE ? ESCAPE '\\' OR m.artist LIKE ? ESCAPE '\\' OR m.album LIKE ? ESCAPE '\\'"
                         " OR m.album_artist LIKE ? ESCAPE '\\' OR m.genre LIKE ? ESCAPE '\\'"
                         " OR m.path LIKE ? ESCAPE '\\')")
            params.extend([_like(word)] * 6)
        order = SORTS.get(sort, SORTS["title"])[1]
        sql = (f"SELECT {_ITEM_COLUMNS} FROM media m LEFT JOIN history h ON h.mrl = m.mrl"
               + (" WHERE " + " AND ".join(where) if where else "") + f" ORDER BY {order}")
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [_item(r) for r in self.conn.execute(sql, params)]

    def item_for_mrl(self, mrl: str) -> LibraryItem | None:
        row = self.conn.execute(f"SELECT {_ITEM_COLUMNS} FROM media m LEFT JOIN history h ON h.mrl = m.mrl "
                                "WHERE m.mrl = ?", (mrl,)).fetchone()
        return _item(row) if row else None

    def albums(self, search: str = "") -> dict[str, list[Album]]:
        """Music grouped as artist -> albums -> tracks (album artist first, then track artist)."""
        artists: dict[str, dict[str, Album]] = {}
        for item in self.items("audio", search, "artist"):
            albums = artists.setdefault(item.display_artist, {})
            album = albums.get(item.display_album)
            if album is None:
                album = albums[item.display_album] = Album(item.display_album, item.display_artist, item.year)
            album.tracks.append(item)
            album.year = album.year or item.year
        result = {}
        for artist in sorted(artists, key=lambda a: (a == UNKNOWN_ARTIST, a.lower())):
            albums = sorted(artists[artist].values(), key=lambda al: (al.name == UNKNOWN_ALBUM, al.year or "9999",
                                                                       al.name.lower()))
            for album in albums:
                album.tracks.sort(key=lambda t: (t.track or 10_000, t.title.lower()))
            result[artist] = albums
        return result

    def counts(self) -> dict[str, int]:
        counts = {"video": 0, "audio": 0}
        for kind, n in self.conn.execute("SELECT kind, COUNT(*) FROM media GROUP BY kind"):
            counts[kind] = n
        return counts

    # -------------------------------------------------------------- history
    def record_play(self, mrl: str, title: str, when: float | None = None) -> None:
        when = time.time() if when is None else when
        with self._write_lock, self.conn as conn:
            conn.execute("""INSERT INTO history(mrl, title, last_played, play_count) VALUES (?, ?, ?, 1)
                            ON CONFLICT(mrl) DO UPDATE SET title = excluded.title,
                            last_played = excluded.last_played, play_count = play_count + 1""",
                         (mrl, title, when))
            conn.execute("DELETE FROM history WHERE mrl NOT IN "
                         "(SELECT mrl FROM history ORDER BY last_played DESC LIMIT ?)", (self.HISTORY_LIMIT,))

    def forget_history(self, mrl: str | None = None) -> None:
        if mrl is None:
            self._write("DELETE FROM history")
        else:
            self._write("DELETE FROM history WHERE mrl = ?", (mrl,))

    def recent(self, limit: int = 100, search: str = "") -> list[LibraryItem]:
        """Recently played media, library or not (streams, discs, loose files)."""
        rows = self.conn.execute("SELECT mrl, title, last_played, play_count FROM history "
                                 "ORDER BY last_played DESC LIMIT ?", (limit,)).fetchall()
        result = []
        needle = search.strip().lower()
        for mrl, title, last_played, count in rows:
            item = self.item_for_mrl(mrl) or _loose_item(mrl, title)
            item.last_played, item.play_count = last_played, count
            if needle and not all(w in f"{item.title} {item.artist} {item.album} {mrl}".lower()
                                  for w in needle.split()):
                continue
            result.append(item)
        return result

    def continue_watching(self, resume: dict[str, tuple[int, int, float]], search: str = "",
                          limit: int = 50) -> list[LibraryItem]:
        """Media with a saved resume position, most recently watched first.

        ``resume`` is ``ResumeStore.entries()``. Local files that no longer
        exist are skipped; library items get their metadata and thumbnail.
        """
        result = []
        needle = search.strip().lower()
        for mrl, (pos, length, stamp) in sorted(resume.items(), key=lambda kv: -kv[1][2]):
            item = self.item_for_mrl(mrl)
            if item is None:
                local = mrl_to_path(mrl)
                if local is not None and not os.path.exists(local):
                    continue
                row = self.conn.execute("SELECT title FROM history WHERE mrl = ?", (mrl,)).fetchone()
                item = _loose_item(mrl, row[0] if row else "")
            item.position_ms = pos
            if length > 0:
                item.duration_ms = length
            item.last_played = stamp
            if needle and not all(w in f"{item.title} {item.artist} {mrl}".lower() for w in needle.split()):
                continue
            result.append(item)
            if len(result) >= limit:
                break
        return result


def _loose_item(mrl: str, title: str = "") -> LibraryItem:
    """A history/resume entry that is not part of the library."""
    local = mrl_to_path(mrl)
    kind = kind_for_path(local or mrl) or "video"
    return LibraryItem(path=local or "", kind=kind, title=title or display_name(mrl), mrl=mrl, in_library=False)
