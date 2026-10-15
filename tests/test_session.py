import json

from vlclone.core.playlist import Playlist
from vlclone.core.session import clear_saved_playlist, restore_playlist, save_playlist


def test_playlist_round_trip(tmp_path):
    path = tmp_path / "pl.json"
    p = Playlist()
    p.add(["http://x/a.mp4", "file:///tmp/b.mkv", "http://x/c.mp3"])
    p.set_title(0, "Nice title")
    p.set_duration(1, 90_000)
    p.set_current(1)
    save_playlist(p, path)

    q = Playlist()
    assert restore_playlist(q, path) == 3
    assert [it.mrl for it in q] == ["http://x/a.mp4", "file:///tmp/b.mkv", "http://x/c.mp3"]
    assert q[0].title == "Nice title" and q[1].duration_ms == 90_000
    assert q.current_index == 1


def test_restore_appends_after_existing_items(tmp_path):
    path = tmp_path / "pl.json"
    p = Playlist()
    p.add(["http://x/a.mp4", "http://x/b.mp4"])
    p.set_current(1)
    save_playlist(p, path)
    q = Playlist()
    q.add(["http://x/cli.mp4"])
    restore_playlist(q, path)
    assert len(q) == 3 and q.current.mrl == "http://x/b.mp4"


def test_restore_tolerates_bad_files(tmp_path):
    path = tmp_path / "pl.json"
    q = Playlist()
    assert restore_playlist(q, path) == 0  # missing
    path.write_text("{oops")
    assert restore_playlist(q, path) == 0
    path.write_text(json.dumps({"current": 9, "items": [{"mrl": "http://x/ok"}, {"mrl": ""}, 3,
                                                        {"mrl": "http://x/2", "duration": "x"}]}))
    assert restore_playlist(q, path) == 2
    assert q.current is None  # out-of-range current ignored
    assert q[1].duration_ms == -1


def test_clear_saved_playlist(tmp_path):
    path = tmp_path / "pl.json"
    save_playlist(Playlist(), path)
    clear_saved_playlist(path)
    assert not path.exists()
    clear_saved_playlist(path)  # no error when already gone
