"""Network URLs and history, discs, capture devices, options in playlists."""

import pytest

from vlclone.core.capture import (CaptureRequest, capture_mrl, is_capture_mrl, linux_audio_devices,
                                  linux_video_devices)
from vlclone.core.discs import (DiscRequest, OpticalDrive, _linux_drives, detect_disc_kind, disc_mrl, disc_root,
                                is_disc_mrl)
from vlclone.core.m3u import read_m3u, write_m3u
from vlclone.core.network import (UrlHistory, describe_open_error, is_network_mrl, is_youtube, normalize_url,
                                  stream_kind, validate_url)
from vlclone.core.playlist import Playlist, PlaylistItem
from vlclone.core.session import restore_playlist, save_playlist
from vlclone.core.settings import Settings


# ----------------------------------------------------------------- network
@pytest.mark.parametrize("url,kind", [
    ("https://cdn.example.com/live/master.m3u8", "HLS stream"),
    ("http://example.com/a.m3u8?token=1", "HLS stream"),
    ("https://example.com/manifest.mpd", "DASH stream"),
    ("rtsp://192.168.1.10:554/stream1", "RTSP"),
    ("rtmp://live.example.com/app/key", "RTMP"),
    ("udp://@:1234", "UDP"),
    ("rtp://@239.0.0.1:5004", "RTP"),
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "YouTube (via VLC's Lua script)"),
    ("https://youtu.be/dQw4w9WgXcQ", "YouTube (via VLC's Lua script)"),
    ("https://vimeo.com/123", "Web video (via VLC's Lua scripts)"),
    ("http://radio.example/stations.pls", "Remote playlist"),
    ("https://example.com/video.mp4", "HTTPS"),
])
def test_stream_kind(url, kind):
    assert stream_kind(url) == kind
    assert validate_url(url) is None


def test_normalize_and_validate():
    assert normalize_url("  youtu.be/abc ") == "https://youtu.be/abc"
    assert normalize_url("www.example.com/x.mp4") == "https://www.example.com/x.mp4"
    assert normalize_url("@:1234") == "udp://@:1234"
    assert normalize_url("239.0.0.1:5000") == "udp://@239.0.0.1:5000"
    assert validate_url("") == "Enter a URL"
    assert "spaces" in validate_url("http://example.com/my video.mp4")
    assert "protocol" in validate_url("example.com/video")
    assert "host" in validate_url("http:///nohost")
    assert validate_url("screen://") is None  # unknown schemes are left to libVLC
    assert is_youtube("https://m.youtube.com/watch?v=1") and not is_youtube("https://notyoutube.com")
    assert is_network_mrl("rtsp://x") and not is_network_mrl("file:///x") and not is_network_mrl("dvd:///D:/")


def test_url_history():
    h = UrlHistory(["http://a", "http://b", "http://a", 5, "", "youtu.be/x"], limit=3)
    assert h.entries == ["http://a", "http://b", "https://youtu.be/x"]
    h.add("http://b")
    assert h.entries[0] == "http://b" and len(h.entries) == 3
    h.add("rtsp://new")
    assert h.entries == ["rtsp://new", "http://b", "http://a"]
    assert h.matching("RTSP") == ["rtsp://new"]
    h.remove("http://b")
    assert "http://b" not in h.entries
    h.clear()
    assert h.entries == []


def test_url_history_round_trips_through_settings(tmp_path):
    s = Settings()
    h = UrlHistory(s.recent_urls)
    h.add("https://example.com/live.m3u8")
    s.recent_urls = h.entries
    s.save(tmp_path / "s.json")
    assert UrlHistory(Settings.load(tmp_path / "s.json").recent_urls).entries == ["https://example.com/live.m3u8"]


def test_error_descriptions():
    assert "example.com" in describe_open_error("http://example.com/x")
    assert "timed" in describe_open_error("http://example.com/x", "timed out")
    assert "disc" in describe_open_error("dvd:///D:/").lower()
    assert "capture device" in describe_open_error("dshow://")
    assert describe_open_error("file:///x.mkv").startswith("Could not play")


# ------------------------------------------------------------------- discs
def test_disc_mrls():
    assert disc_mrl(DiscRequest("dvd", "D:\\"))[0] == "dvd:///D:/"
    assert disc_mrl(DiscRequest("dvd", "D:\\", menus=False, title=2, chapter=5))[0] == "dvdsimple:///D:/#2:5"
    assert disc_mrl(DiscRequest("dvd", "/dev/sr0", title=3))[0] == "dvd:///dev/sr0#3"
    mrl, opts, title = disc_mrl(DiscRequest("bluray", "E:\\", menus=False, title=1))
    assert (mrl, opts) == ("bluray:///E:/#1", [":no-bluray-menu"])
    assert "Blu-ray" in title
    mrl, opts, title = disc_mrl(DiscRequest("cd", "D:\\", track=4))
    assert (mrl, opts) == ("cdda:///D:/", [":cdda-track=4"]) and "track 4" in title
    assert disc_mrl(DiscRequest("dvd", "/media/movies/FILM.iso"))[0] == "dvd:///media/movies/FILM.iso"
    with pytest.raises(ValueError):
        disc_mrl(DiscRequest("dvd", ""))
    with pytest.raises(ValueError):
        disc_mrl(DiscRequest("laserdisc", "D:\\"))
    with pytest.raises(ValueError):
        disc_mrl(DiscRequest("dvd", "/odd#path"))
    assert is_disc_mrl("dvdsimple:///D:/") and is_disc_mrl("cdda:///D:/") and not is_disc_mrl("http://x")


def test_detect_disc_kind(tmp_path):
    dvd = tmp_path / "MOVIE"
    (dvd / "VIDEO_TS").mkdir(parents=True)
    (dvd / "VIDEO_TS" / "VIDEO_TS.IFO").write_bytes(b"DVDVIDEO-VMG")
    bd = tmp_path / "BD"
    (bd / "BDMV").mkdir(parents=True)
    cd = tmp_path / "CD"
    cd.mkdir()
    (cd / "Track01.cda").write_bytes(b"x")
    assert detect_disc_kind(str(dvd)) == "dvd"
    assert detect_disc_kind(str(dvd / "VIDEO_TS")) == "dvd"
    assert detect_disc_kind(str(dvd / "VIDEO_TS" / "VIDEO_TS.IFO")) == "dvd"
    assert disc_root(str(dvd / "VIDEO_TS" / "VIDEO_TS.IFO")) == str(dvd)
    assert detect_disc_kind(str(bd)) == "bluray"
    assert disc_root(str(bd / "BDMV")) == str(bd)
    assert detect_disc_kind(str(cd)) == "cd"
    assert detect_disc_kind(str(tmp_path / "missing")) is None
    iso = tmp_path / "film.iso"
    iso.write_bytes(b"\0" * 40000 + b"VIDEO_TS" + b"\0" * 100)
    assert detect_disc_kind(str(iso)) == "dvd"
    bdiso = tmp_path / "bd.iso"
    bdiso.write_bytes(b"\0" * (1024 * 1024 - 2) + b"BDMV" + b"\0" * 10)  # straddles a read boundary
    assert detect_disc_kind(str(bdiso)) == "bluray"
    (tmp_path / "data.iso").write_bytes(b"\0" * 5000)
    assert detect_disc_kind(str(tmp_path / "data.iso")) is None


def test_linux_drive_listing(tmp_path):
    dev = tmp_path / "dev"
    dev.mkdir()
    for name in ("sr0", "sr1", "sda", "srx"):
        (dev / name).write_text("")
    mount = tmp_path / "mnt" / "MY_DVD"
    (mount / "VIDEO_TS").mkdir(parents=True)
    mounts = tmp_path / "mounts"
    mounts.write_text(f"{dev / 'sr0'} {mount} udf ro 0 0\n/dev/sda1 / ext4 rw 0 0\n")
    drives = _linux_drives(str(dev), str(mounts))
    assert [d.path for d in drives] == [str(dev / "sr0"), str(dev / "sr1")]
    assert drives[0].kind == "dvd" and drives[0].label == "MY_DVD" and drives[1].kind is None
    assert OpticalDrive("D:\\", "MOVIE", "dvd").display == "D: — MOVIE — DVD"


# ----------------------------------------------------------------- capture
def test_capture_mrls_windows():
    mrl, opts, title = capture_mrl(CaptureRequest("Integrated Camera", "Microphone Array", 1280, 720, 30),
                                   platform="win32")
    assert mrl == "dshow://"
    assert opts == [":dshow-vdev=Integrated Camera", ":dshow-adev=Microphone Array", ":dshow-size=1280x720",
                    ":dshow-fps=30", ":live-caching=300"]
    assert title == "Capture: Integrated Camera + Microphone Array"
    mrl, opts, _ = capture_mrl(CaptureRequest(None, ""), platform="win32")  # microphone only
    assert opts[:2] == [":dshow-vdev=none", ":dshow-adev="]
    with pytest.raises(ValueError):
        capture_mrl(CaptureRequest(None, None), platform="win32")


def test_capture_mrls_linux():
    mrl, opts, _ = capture_mrl(CaptureRequest("/dev/video2", "hw:1,0", 640, 480, caching_ms=500), platform="linux")
    assert mrl == "v4l2:///dev/video2"
    assert opts == [":v4l2-width=640", ":v4l2-height=480", ":input-slave=alsa://hw:1,0", ":live-caching=500"]
    mrl, opts, _ = capture_mrl(CaptureRequest(None, "hw:0,0"), platform="linux")
    assert mrl == "alsa://hw:0,0"
    assert is_capture_mrl("dshow://") and is_capture_mrl("v4l2:///dev/video0") and not is_capture_mrl("http://x")


def test_linux_device_discovery(tmp_path):
    dev = tmp_path / "dev"
    sysfs = tmp_path / "sys"
    dev.mkdir()
    for n, name, index in ((0, "HD Webcam", "0"), (1, "HD Webcam", "1"), (10, "Capture card", "0")):
        (dev / f"video{n}").write_text("")
        (sysfs / f"video{n}").mkdir(parents=True)
        (sysfs / f"video{n}" / "name").write_text(name + "\n")
        (sysfs / f"video{n}" / "index").write_text(index)
    videos = linux_video_devices(str(dev), str(sysfs))
    assert [(d.id, d.name) for d in videos] == [(str(dev / "video0"), "HD Webcam (video0)"),
                                               (str(dev / "video10"), "Capture card (video10)")]
    pcm = tmp_path / "pcm"
    pcm.write_text("00-00: ALC892 Analog : ALC892 Analog : playback 1 : capture 1\n"
                   "00-01: ALC892 Digital : ALC892 Digital : playback 1\n"
                   "01-00: USB Audio : USB Audio : capture 1\n")
    audio = linux_audio_devices(str(pcm))
    assert [d.id for d in audio] == ["default", "hw:0,0", "hw:1,0"]


def test_fake_backend_lists_devices():
    from vlclone.core.fake_backend import FakeBackend

    devices = FakeBackend().capture_devices()
    assert devices["video"][0].name == "Integrated Camera" and len(devices["audio"]) == 2


# ------------------------------------------------------- options in lists
def test_m3u_keeps_vlc_options(tmp_path):
    path = tmp_path / "list.m3u"
    write_m3u(str(path), [PlaylistItem("http://x/live.m3u8", "Live", options=[":network-caching=3000"]),
                          PlaylistItem("dshow://", "Cam", options=[":dshow-vdev=Cam", ":live-caching=300"])])
    text = path.read_text()
    assert "#EXTVLCOPT:network-caching=3000" in text
    items = read_m3u(str(path))
    assert items[0].options == [":network-caching=3000"]
    assert items[1].options == [":dshow-vdev=Cam", ":live-caching=300"]


def test_session_keeps_options(tmp_path):
    pl = Playlist()
    pl.add([PlaylistItem("http://x/a", options=[":network-caching=200"]), PlaylistItem("http://x/b")])
    save_playlist(pl, tmp_path / "s.json")
    again = Playlist()
    restore_playlist(again, tmp_path / "s.json")
    assert again[0].options == [":network-caching=200"] and again[1].options == []


def test_playlist_replace_keeps_current_and_shuffle():
    pl = Playlist()
    pl.add(["a", "b", "c"])
    pl.set_current(1)
    pl.shuffle = True
    pl.replace(1, ["b1", "b2"])
    assert [it.mrl for it in pl] == ["a", "b1", "b2", "c"]
    assert pl.current.mrl == "b1"
    assert sorted(pl.index_of(u) for u in pl._order) == [0, 1, 2, 3]
    pl.set_current(3)
    pl.replace(0, ["a1", "a2"])
    assert pl.current.mrl == "c" and pl.current_index == 4
    assert pl.replace(9, ["x"]) == []
