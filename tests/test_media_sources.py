import os

from vlclone.core import media_sources as ms


def test_is_url():
    assert ms.is_url("http://example.com/a.mp4")
    assert ms.is_url("rtsp://cam/stream")
    assert ms.is_url("file:///tmp/a.mp4")
    assert not ms.is_url("/tmp/a.mp4")
    assert not ms.is_url(r"C:\videos\a.mp4")
    assert not ms.is_url("C:/videos/a.mp4")


def test_extension_and_kinds():
    assert ms.extension("/a/b/Movie.MKV") == "mkv"
    assert ms.extension("noext") == ""
    assert ms.is_media_file("x.mp3")
    assert not ms.is_media_file("x.txt")
    assert ms.is_subtitle_file("x.srt")
    assert ms.is_playlist_file("x.m3u8")


def test_mrl_round_trip(tmp_path):
    f = tmp_path / "my file #1.mkv"
    f.write_bytes(b"")
    mrl = ms.path_to_mrl(str(f))
    assert mrl.startswith("file://")
    assert " " not in mrl and "#" not in mrl
    assert os.path.samefile(ms.mrl_to_path(mrl), f)
    assert ms.display_name(mrl) == "my file #1.mkv"


def test_display_name_url():
    assert ms.display_name("http://host/path/clip%20one.mp4") == "clip one.mp4"
    assert ms.display_name("rtsp://camera.local") == "camera.local"
    assert ms.mrl_to_path("http://host/x") is None


def test_scan_folder_natural_sort(tmp_path):
    for name in ["ep10.mkv", "ep2.mkv", "ep1.mkv", "notes.txt", "cover.jpg"]:
        (tmp_path / name).write_bytes(b"")
    sub = tmp_path / "extras"
    sub.mkdir()
    (sub / "bonus.mp4").write_bytes(b"")
    names = [os.path.basename(p) for p in ms.scan_folder(str(tmp_path))]
    assert names == ["ep1.mkv", "ep2.mkv", "ep10.mkv", "bonus.mp4"]
    flat = [os.path.basename(p) for p in ms.scan_folder(str(tmp_path), recursive=False)]
    assert flat == ["ep1.mkv", "ep2.mkv", "ep10.mkv"]


def test_expand_inputs(tmp_path):
    (tmp_path / "a.mp4").write_bytes(b"")
    (tmp_path / "b.mp3").write_bytes(b"")
    result = ms.expand_inputs([str(tmp_path), "http://x/stream", "", ms.path_to_mrl(str(tmp_path / "a.mp4"))])
    assert len(result) == 4
    assert result[2] == "http://x/stream"
    assert result[0].endswith("a.mp4") and result[3].endswith("a.mp4")
