"""Media library: SQLite storage, scanner, metadata, history, continue watching (fake probe, temp dirs)."""

import os
import sqlite3
import threading
import time

import pytest

from vlclone.core.fake_backend import FakeProbe
from vlclone.core.library import LibraryDB, LibraryItem, MediaMeta, clean_meta
from vlclone.core.library_scanner import FilenameProbe, LibraryService, folder_signature, scan_folder
from vlclone.core.media_sources import path_to_mrl
from vlclone.core.png import encode_png, png_size, write_png
from vlclone.core.resume import ResumeStore


def touch(path, data=b"x", mtime=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


@pytest.fixture
def db(tmp_path):
    database = LibraryDB(tmp_path / "lib.db")
    yield database
    database.close()


@pytest.fixture
def media(tmp_path):
    root = tmp_path / "media"
    videos = root / "Videos"
    music = root / "Music"
    touch(videos / "Holiday 2023.mp4")
    touch(videos / "Series" / "Show S01E02.mkv")
    touch(videos / "notes.txt")
    touch(videos / ".hidden" / "secret.mkv")
    touch(music / "Band" / "Album X" / "01 Opening.mp3")
    touch(music / "Band" / "Album X" / "02 Closing.flac")
    touch(music / "Other - Single.ogg")
    touch(music / "cover.jpg")
    return videos, music


def meta_for(music):
    return {
        str(music / "Band" / "Album X" / "01 Opening.mp3"): MediaMeta("Opening", "Band", "Album X", track=1,
                                                                    duration_ms=200_000, year="2001-04-01"),
        str(music / "Band" / "Album X" / "02 Closing.flac"): MediaMeta("Closing", "Band", "Album X", track="2/9",
                                                                     duration_ms=250_000),
    }


# ----------------------------------------------------------------- storage
def test_schema_and_folders(db, media, tmp_path):
    videos, music = media
    assert db.add_folder(str(videos), "video") is None
    assert db.add_folder(str(music), "music") is None
    assert "Already" in db.add_folder(str(videos / "Series"), "video")
    assert db.add_folder(str(tmp_path / "nope"), "video") == "That folder does not exist"
    assert "Unknown" in db.add_folder(str(videos), "photos")
    assert db.folders() == [(str(music), "music"), (str(videos), "video")]
    con = sqlite3.connect(str(tmp_path / "lib.db"))
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"folders", "media", "history", "meta"} <= tables
    con.close()


def test_parent_folder_replaces_nested_one(db, media):
    videos, _ = media
    db.add_folder(str(videos / "Series"), "video")
    db.upsert(str(videos / "Series" / "Show S01E02.mkv"), str(videos / "Series"), "video", 1, 1, MediaMeta())
    assert db.add_folder(str(videos), "video") is None
    assert db.folders() == [(str(videos), "video")]
    assert db.items() == []  # rescanned under the new folder


def test_clean_meta():
    m = clean_meta(MediaMeta(title="clip.mp4", track="7/12", year="Released 1999-01-01", duration_ms=0),
                   "/x/clip.mp4")
    assert (m.title, m.track, m.year, m.duration_ms) == ("clip", 7, "1999", -1)
    assert clean_meta(MediaMeta(title="  Real  "), "/x/a.mp3").title == "Real"
    assert clean_meta(MediaMeta(title="http://x/a"), "/x/a.mp3").title == "a"


# ----------------------------------------------------------------- scanner
def test_scan_indexes_by_folder_kind(db, media):
    videos, music = media
    db.add_folder(str(videos), "video")
    db.add_folder(str(music), "music")
    probe = FakeProbe(meta_for(music))
    cancel = threading.Event()
    assert scan_folder(db, str(videos), "video", probe, cancel) == (2, 0)
    assert scan_folder(db, str(music), "music", probe, cancel) == (3, 0)
    titles = [i.title for i in db.items("video")]
    assert titles == ["Holiday 2023", "Show S01E02"]  # .txt and hidden folders skipped
    v = db.items("video")[0]
    assert (v.width, v.height, v.resolution, v.duration_ms) == (1280, 720, "1280×720", 90_000)
    assert v.mrl == path_to_mrl(v.path)
    tracks = db.items("audio", sort="artist")
    assert [(t.artist, t.title, t.track) for t in tracks] == [("Band", "Opening", 1), ("Band", "Closing", 2),
                                                              ("Other", "Single", 0)]
    assert tracks[0].year == "2001"
    assert db.counts() == {"video": 2, "audio": 3}


def test_rescan_is_incremental(db, media):
    videos, _ = media
    db.add_folder(str(videos), "video")
    probe = FakeProbe()
    cancel = threading.Event()
    scan_folder(db, str(videos), "video", probe, cancel)
    assert len(probe.probed) == 2
    probe.probed.clear()
    assert scan_folder(db, str(videos), "video", probe, cancel) == (0, 0)
    assert probe.probed == []  # unchanged files are not parsed again
    touch(videos / "Holiday 2023.mp4", b"changed!", mtime=time.time() + 10)
    touch(videos / "New.webm")
    os.remove(videos / "Series" / "Show S01E02.mkv")
    assert scan_folder(db, str(videos), "video", probe, cancel) == (2, 1)
    assert sorted(os.path.basename(p) for p in probe.probed) == ["Holiday 2023.mp4", "New.webm"]
    assert [i.title for i in db.items("video")] == ["Holiday 2023", "New"]


def test_cancelled_scan_removes_nothing(db, media):
    videos, _ = media
    db.add_folder(str(videos), "video")
    scan_folder(db, str(videos), "video", FakeProbe(), threading.Event())
    cancel = threading.Event()
    cancel.set()
    assert scan_folder(db, str(videos), "video", FakeProbe(), cancel) == (0, 0)
    assert len(db.items("video")) == 2


def test_probe_errors_do_not_stop_the_scan(db, media):
    videos, _ = media

    class Broken(FakeProbe):
        def probe(self, path):
            raise RuntimeError("corrupt file")

    db.add_folder(str(videos), "video")
    assert scan_folder(db, str(videos), "video", Broken(), threading.Event()) == (2, 0)
    assert [i.title for i in db.items("video")] == ["Holiday 2023", "Show S01E02"]  # file names as titles


def test_filename_probe():
    m = FilenameProbe().probe("/music/Some Album/Artist Name - Song.mp3")
    assert (m.artist, m.title, m.album, m.has_video) == ("Artist Name", "Song", "Some Album", False)
    assert FilenameProbe().probe("/v/clip.mkv").has_video


def test_search_and_sort(db, media):
    videos, music = media
    db.add_folder(str(videos), "video")
    db.add_folder(str(music), "music")
    probe = FakeProbe(meta_for(music))
    for folder, kind in db.folders():
        scan_folder(db, folder, kind, probe, threading.Event())
    assert [i.title for i in db.items(search="holiday")] == ["Holiday 2023"]
    assert [i.title for i in db.items("audio", search="band clos")] == ["Closing"]
    assert [i.title for i in db.items(search="Series")] == ["Show S01E02"]  # folder names match too
    assert db.items(search="100%_") == []  # LIKE wildcards are escaped
    assert [i.title for i in db.items("audio", sort="duration")][:2] == ["Closing", "Opening"]
    assert [i.title for i in db.items("audio", sort="bogus'; DROP TABLE media; --")]  # falls back to title
    assert len(db.items()) == 5


def test_music_grouping(db, media):
    _, music = media
    db.add_folder(str(music), "music")
    scan_folder(db, str(music), "music", FakeProbe(meta_for(music)), threading.Event())
    tree = db.albums()
    assert list(tree) == ["Band", "Other"]
    album = tree["Band"][0]
    assert (album.name, album.year, [t.title for t in album.tracks]) == ("Album X", "2001", ["Opening", "Closing"])
    assert album.duration_ms == 450_000
    assert tree["Other"][0].name == "Music"  # FilenameProbe: folder as album
    assert list(db.albums("closing")) == ["Band"]


def test_history_and_recent(db, media):
    videos, _ = media
    db.add_folder(str(videos), "video")
    scan_folder(db, str(videos), "video", FakeProbe(), threading.Event())
    holiday = path_to_mrl(str(videos / "Holiday 2023.mp4"))
    db.record_play(holiday, "Holiday 2023.mp4", when=100)
    db.record_play("https://example.com/live.m3u8", "Live TV", when=200)
    db.record_play(holiday, "Holiday 2023.mp4", when=300)
    recent = db.recent()
    assert [(i.title, i.play_count, i.in_library) for i in recent] == [("Holiday 2023", 2, True),
                                                                       ("Live TV", 1, False)]
    assert recent[0].thumb is None and recent[1].kind == "video"
    assert [i.title for i in db.recent(search="live")] == ["Live TV"]
    assert db.items("video", sort="played")[0].title == "Holiday 2023"
    db.forget_history("https://example.com/live.m3u8")
    assert len(db.recent()) == 1
    db.forget_history()
    assert db.recent() == []


def test_history_is_capped(db):
    db.HISTORY_LIMIT = 5
    for n in range(8):
        db.record_play(f"http://x/{n}", str(n), when=n)
    assert [i.title for i in db.recent()] == ["7", "6", "5", "4", "3"]


def test_continue_watching_uses_resume_positions(db, media, tmp_path):
    videos, _ = media
    db.add_folder(str(videos), "video")
    scan_folder(db, str(videos), "video", FakeProbe(), threading.Event())
    resume = ResumeStore(tmp_path / "resume.json", min_position_ms=1000)
    holiday = path_to_mrl(str(videos / "Holiday 2023.mp4"))
    loose = touch(tmp_path / "elsewhere" / "Movie.mkv")
    resume.update(holiday, 30_000, 90_000)
    time.sleep(0.01)
    resume.update(path_to_mrl(str(loose)), 600_000, 6_000_000)
    resume.update(path_to_mrl(str(tmp_path / "deleted.mkv")), 50_000, 100_000)
    items = db.continue_watching(resume.entries())
    assert [(i.title, i.in_library) for i in items] == [("Movie.mkv", False), ("Holiday 2023", True)]
    assert items[1].position_ms == 30_000 and abs(items[1].progress - 1 / 3) < 1e-6
    assert abs(items[0].progress - 0.1) < 1e-6
    assert [i.title for i in db.continue_watching(resume.entries(), search="holi")] == ["Holiday 2023"]


def test_resume_entries_skip_garbage(tmp_path):
    path = tmp_path / "r.json"
    path.write_text('{"a": {"pos": 5000, "len": 9000, "t": 1}, "b": {"pos": "x"}, "c": 3}')
    assert ResumeStore(path).entries() == {"a": (5000, 9000, 1.0)}


def test_library_item_helpers():
    item = LibraryItem(path="/m/a.mp3", kind="audio", title="A")
    assert item.display_artist == "Unknown artist" and item.display_album == "Unknown album"
    assert item.progress == 0.0 and item.mrl.startswith("file://")


# ----------------------------------------------------------------- service
def test_service_scans_in_background_and_makes_thumbnails(db, media, tmp_path):
    videos, music = media
    probes = []

    def factory():
        probes.append(FakeProbe(fail_thumbnails={str(videos / "Series" / "Show S01E02.mkv")}))
        return probes[-1]

    svc = LibraryService(db, factory, thumbs_dir=tmp_path / "thumbs", watch=False)
    gen = svc.generation
    assert svc.add_folder(str(videos), "video") is None
    assert svc.add_folder(str(music), "music") is None  # queued behind the running scan
    assert svc.wait(10)
    assert svc.generation > gen and not svc.status.busy
    assert svc.db.counts() == {"video": 2, "audio": 3}
    assert all(p.closed for p in probes)
    by_title = {i.title: i for i in svc.videos()}
    thumb = by_title["Holiday 2023"].thumb
    assert thumb and png_size(thumb) == (4, 2) and os.path.dirname(thumb) == str(tmp_path / "thumbs")
    assert by_title["Show S01E02"].thumb is None  # failed: not retried every scan
    svc.rescan()
    svc.wait(10)
    assert sum(len(p.thumbnailed) for p in probes) == 2
    svc.shutdown()


def test_service_watches_folders(db, media, tmp_path):
    videos, _ = media
    now = [0.0]
    svc = LibraryService(db, FakeProbe, thumbs_dir=tmp_path / "t", watch=True, watch_interval_s=30,
                         clock=lambda: now[0])
    svc.add_folder(str(videos), "video")
    svc.wait(10)
    assert len(svc.videos()) == 2
    gen = svc.generation
    now[0] = 31
    svc.tick()  # nothing changed: a quick check, no rescan
    svc.wait(10)
    assert svc.generation == gen
    touch(videos / "Series" / "Show S01E03.mkv")
    now[0] = 62
    svc.tick()
    svc.wait(10)
    assert [i.title for i in svc.videos()] == ["Holiday 2023", "Show S01E02", "Show S01E03"]
    assert svc.generation > gen
    os.remove(videos / "Holiday 2023.mp4")
    now[0] = 93
    svc.tick()
    svc.wait(10)
    assert [i.title for i in svc.videos()] == ["Show S01E02", "Show S01E03"]
    svc.shutdown()


def test_service_remove_folder_and_history(db, media, tmp_path):
    videos, music = media
    svc = LibraryService(db, FakeProbe, thumbs_dir=tmp_path / "t", watch=False, thumbnails=False)
    svc.add_folder(str(videos), "video")
    svc.add_folder(str(music), "music")
    svc.wait(10)
    svc.remove_folder(str(music))
    assert svc.db.counts() == {"video": 2, "audio": 0}
    gen = svc.generation
    svc.record_play("http://x/a", "A")
    assert svc.generation == gen + 1 and svc.recent()[0].title == "A"
    svc.forget_history()
    assert svc.recent() == []
    svc.shutdown()


def test_service_reports_missing_folder(db, media, tmp_path):
    videos, _ = media
    svc = LibraryService(db, FakeProbe, thumbs_dir=tmp_path / "t", watch=False)
    svc.add_folder(str(videos), "video")
    svc.wait(10)
    os.rename(videos, videos.parent / "Moved")
    svc.rescan()
    svc.wait(10)
    assert "Folder not found" in svc.status.error
    assert len(svc.videos()) == 2  # a missing (unplugged) folder keeps its entries
    svc.shutdown()


def test_folder_signature_changes(tmp_path):
    touch(tmp_path / "a" / "x.mkv")
    sig = folder_signature(str(tmp_path))
    assert folder_signature(str(tmp_path)) == sig
    time.sleep(0.01)
    touch(tmp_path / "a" / "y.mkv")
    assert folder_signature(str(tmp_path)) != sig


def test_png_writer(tmp_path):
    data = encode_png(2, 1, bytes([255, 0, 0, 0, 0, 255]))
    assert data.startswith(b"\x89PNG") and b"IEND" in data
    write_png(tmp_path / "p.png", 3, 2, bytes(18))
    assert png_size(tmp_path / "p.png") == (3, 2)
    assert png_size(tmp_path / "missing.png") is None
    with pytest.raises(ValueError):
        encode_png(2, 2, b"short")
