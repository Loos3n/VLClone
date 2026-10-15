import json

from vlclone.core.m3u import read_m3u, write_m3u
from vlclone.core.media_sources import mrl_to_path, path_to_mrl
from vlclone.core.playlist import PlaylistItem
from vlclone.core.resume import ResumeStore
from vlclone.core.settings import Settings
from vlclone.core.shortcuts import DEFAULT_SHORTCUTS, find_conflicts, resolve_shortcuts


def test_settings_round_trip_and_validation(tmp_path):
    path = tmp_path / "s.json"
    s = Settings()
    s.volume = 150
    s.hardware_decoding = False
    s.save(path)
    loaded = Settings.load(path)
    assert loaded.volume == 150 and loaded.hardware_decoding is False

    path.write_text(json.dumps({"volume": "loud", "max_volume": 999, "unknown": 1,
                                "resume_playback": 1, "repeat": "bogus", "volume_step": 3.0}))
    loaded = Settings.load(path)
    assert loaded.volume == 100          # wrong type ignored
    assert loaded.max_volume == 200      # clamped
    assert loaded.resume_playback is True  # int is not accepted for bool
    assert loaded.repeat == "none"
    assert loaded.volume_step == 3


def test_settings_corrupt_file(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("{not json")
    assert Settings.load(path).volume == 100


def test_vlc_args():
    s = Settings(hardware_decoding=False, subtitle_text_scale=150, preferred_audio_language="ja",
                 extra_vlc_args="--foo --bar=1")
    args = s.vlc_args()
    assert "--avcodec-hw=none" in args
    assert "--sub-text-scale=150" in args
    assert "--audio-language=ja" in args
    assert args[-2:] == ["--foo", "--bar=1"]


def test_resume_store(tmp_path):
    path = tmp_path / "resume.json"
    store = ResumeStore(path, min_position_ms=10_000)
    store.update("file:///a.mkv", 60_000, 600_000)
    store.update("file:///b.mkv", 5_000, 600_000)       # too early
    store.update("file:///c.mkv", 595_000, 600_000)     # basically finished
    store.update("http://live", 60_000, 0)              # live stream
    store.update("file:///short.mkv", 20_000, 30_000)   # short clip: 5% margin
    store.save()
    again = ResumeStore(path)
    assert again.get("file:///a.mkv") == 60_000
    assert again.get("file:///b.mkv") is None
    assert again.get("file:///c.mkv") is None
    assert again.get("http://live") is None
    assert again.get("file:///short.mkv") == 20_000
    again.update("file:///a.mkv", 1_000, 600_000)       # rewound to start -> forget
    assert again.get("file:///a.mkv") is None


def test_m3u_round_trip(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    (media / "one.mkv").write_bytes(b"")
    items = [
        PlaylistItem(path_to_mrl(str(media / "one.mkv")), "One", 125_000),
        PlaylistItem("http://example.com/live.m3u8", "Live"),
    ]
    out = tmp_path / "list.m3u"
    write_m3u(str(out), items)
    text = out.read_text(encoding="utf-8")
    assert text.startswith("#EXTM3U")
    assert "#EXTINF:125,One" in text
    assert "media" in text and str(tmp_path) not in text  # relative path
    loaded = read_m3u(str(out))
    assert [i.title for i in loaded] == ["One", "Live"]
    assert loaded[0].duration_ms == 125_000
    assert mrl_to_path(loaded[0].mrl) == str(media / "one.mkv")
    assert loaded[1].mrl == "http://example.com/live.m3u8"


def test_m3u_plain_and_cp1252(tmp_path):
    out = tmp_path / "plain.m3u"
    out.write_bytes("# comment\nsong\xe9.mp3\n\nhttp://x/y.mp3\n".encode("cp1252"))
    loaded = read_m3u(str(out))
    assert len(loaded) == 2
    assert loaded[0].title == "song\u00e9.mp3"


def test_default_shortcuts_have_no_conflicts():
    assert find_conflicts(resolve_shortcuts()) == {}
    assert "play_pause" in DEFAULT_SHORTCUTS


def test_shortcut_overrides():
    b = resolve_shortcuts({"play_pause": ["K"], "nope": ["X"], "stop": "bad"})
    assert b["play_pause"] == ["K"]
    assert "nope" not in b
    assert b["stop"] == ["S", "Media Stop"]
    assert "k" in find_conflicts(b)
