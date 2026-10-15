"""Phase 3 against the real libVLC (skipped without it): chapters, local HTTP/HLS streams, errors,
remote playlists, metadata probe, thumbnails and a real library scan."""

import functools
import http.server
import shutil
import subprocess
import threading
import time

import pytest

from vlclone.core.backend import PlaybackState
from vlclone.core.controller import PlayerController
from vlclone.core.media_sources import path_to_mrl
from vlclone.core.settings import Settings
from vlclone.selftest_media import write_chapter_mkv, write_tone, write_y4m

vlc_backend = pytest.importorskip("vlclone.core.vlc_backend")


@pytest.fixture
def backend():
    try:
        b = vlc_backend.VlcBackend(["--quiet", "--vout=dummy", "--aout=dummy", "--no-video-title-show"])
    except vlc_backend.LibVlcNotFound as exc:
        pytest.skip(str(exc))
    yield b
    b.release()


@pytest.fixture
def probe():
    try:
        p = vlc_backend.VlcMediaProbe()
    except vlc_backend.LibVlcNotFound as exc:
        pytest.skip(str(exc))
    yield p
    p.close()


def need_ffmpeg():
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not available")


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@pytest.fixture
def served(tmp_path):
    """An HTTP server over tmp_path/www; yields (folder, base URL)."""
    root = tmp_path / "www"
    root.mkdir()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Quiet, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield root, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def tick_until(ctl, cond, timeout=15.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        ctl.tick()
        if cond():
            return True
        time.sleep(0.05)
    return False


def controller(backend, **settings):
    return PlayerController(backend, Settings(resume_playback=False, **settings))


# ------------------------------------------------------------------ chapters
def test_generated_mkv_chapters_through_controller(backend, tmp_path):
    mkv = tmp_path / "chapters.mkv"
    write_chapter_mkv(str(mkv), seconds=6, chapters=[("Intro", 0), ("Middle", 2000), ("End", 4000)])
    ctl = controller(backend)
    seen = []
    ctl.chapters_changed.connect(lambda ch, titles, cur: seen.append(len(ch)))
    ctl.open([str(mkv)])
    assert tick_until(ctl, lambda: ctl.state is PlaybackState.PLAYING and ctl.chapters)
    assert [c.title for c in ctl.chapters] == ["Intro", "Middle", "End"]
    assert [c.start_ms for c in ctl.chapters] == [0, 2000, 4000] and 3 in seen
    ctl.next_chapter()
    assert tick_until(ctl, lambda: ctl.time() >= 2000 and ctl.current_chapter() == 1, 5)
    ctl.set_chapter(2)
    assert tick_until(ctl, lambda: ctl.current_chapter() == 2, 5)
    ctl.stop()


def test_ffmpeg_mp4_chapters(backend, tmp_path):
    need_ffmpeg()
    meta = tmp_path / "meta.txt"
    meta.write_text(";FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=3000\ntitle=One\n"
                    "[CHAPTER]\nTIMEBASE=1/1000\nSTART=3000\nEND=6000\ntitle=Two\n")
    out = tmp_path / "chapters.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=25",
                    "-i", str(meta), "-map_metadata", "1", "-map_chapters", "1", "-map", "0", "-t", "6",
                    "-c:v", "mpeg4", str(out)], check=True)
    backend.load(path_to_mrl(str(out)))
    backend.play()
    deadline = time.monotonic() + 8
    while not backend.chapters() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert [c.title for c in backend.chapters()] == ["One", "Two"]
    assert backend.titles() and not backend.titles()[0].is_menu
    backend.stop()


# ------------------------------------------------------------------- streams
def test_local_http_stream_and_caching_option(backend, served):
    root, base = served
    write_tone(str(root / "tone.wav"), seconds=4)
    ctl = controller(backend, network_caching_ms=1500)
    ctl.open([f"{base}/tone.wav"])
    assert tick_until(ctl, lambda: ctl.state is PlaybackState.PLAYING and ctl.time() > 0)
    assert ctl.media_options(ctl.playlist.current) == [":network-caching=1500"]
    ctl.stop()


def test_hls_stream(backend, served):
    need_ffmpeg()
    root, base = served
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=25",
                    "-f", "lavfi", "-i", "sine=frequency=440", "-t", "6", "-c:v", "mpeg4", "-c:a", "mp2",
                    "-f", "hls", "-hls_time", "2", "-hls_playlist_type", "vod",
                    str(root / "stream.m3u8")], check=True)
    ctl = controller(backend)
    ctl.open([f"{base}/stream.m3u8"])
    assert tick_until(ctl, lambda: ctl.state is PlaybackState.PLAYING and ctl.time() > 0, 20)
    assert ctl.playlist.current.mrl.endswith("stream.m3u8")  # HLS plays directly, no expansion
    ctl.stop()


def test_missing_url_reports_error_quickly(backend, served):
    _, base = served
    ctl = controller(backend)
    failed = []
    ctl.open_failed.connect(lambda item, msg: failed.append(msg))
    started = time.monotonic()
    ctl.open([f"{base}/missing.mp4"])
    assert tick_until(ctl, lambda: failed, 10)
    assert time.monotonic() - started < 10
    assert "127.0.0.1" in failed[0] and ctl.state is PlaybackState.STOPPED


def test_refused_connection_reports_error(backend):
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()  # nothing listens there now
    ctl = controller(backend, network_timeout_s=15)
    failed = []
    ctl.open_failed.connect(lambda item, msg: failed.append(msg))
    ctl.open([f"http://127.0.0.1:{port}/stream.ts"])
    assert tick_until(ctl, lambda: failed, 20)


def test_remote_playlist_expands_into_items(backend, served):
    root, base = served
    write_tone(str(root / "a.wav"), seconds=2)
    write_tone(str(root / "b.wav"), seconds=2)
    (root / "list.m3u").write_text(f"#EXTM3U\n#EXTINF:2,First\n{base}/a.wav\n#EXTINF:2,Second\n{base}/b.wav\n")
    ctl = controller(backend)
    ctl.open([f"{base}/list.m3u"], options=[":network-caching=500"])
    assert tick_until(ctl, lambda: len(ctl.playlist) == 2 and ctl.state is PlaybackState.PLAYING, 15)
    assert [it.title for it in ctl.playlist] == ["First", "Second"]
    assert ctl.playlist.current.mrl == f"{base}/a.wav"
    assert ctl.playlist[1].options == [":network-caching=500"]
    ctl.stop()


# ------------------------------------------------------------ library probe
def test_probe_reads_tags_and_video_size(probe, tmp_path):
    need_ffmpeg()
    song = tmp_path / "song.mp3"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440", "-t", "3",
                    "-metadata", "title=Song A", "-metadata", "artist=Band", "-metadata", "album=Album X",
                    "-metadata", "track=3/10", "-metadata", "date=2021", str(song)], check=True)
    meta = probe.probe(str(song))
    assert (meta.title, meta.artist, meta.album, meta.track) == ("Song A", "Band", "Album X", 3)
    assert meta.duration_ms > 2500 and not meta.has_video
    clip = tmp_path / "clip.mkv"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25",
                    "-t", "4", "-c:v", "mpeg4", str(clip)], check=True)
    meta = probe.probe(str(clip))
    assert (meta.width, meta.height, meta.has_video) == (320, 240, True)
    assert 3500 <= meta.duration_ms <= 4500


def test_thumbnail_is_a_real_frame(probe, tmp_path):
    from vlclone.core.png import png_size

    video = tmp_path / "bars.y4m"
    write_y4m(str(video), 64, 48, frames=50)
    out = tmp_path / "thumb.png"
    assert probe.thumbnail(str(video), str(out), 320)
    assert png_size(out) == (64, 48)  # never upscaled
    tone = tmp_path / "tone.wav"
    write_tone(str(tone), 1)
    assert not probe.thumbnail(str(tone), str(tmp_path / "none.png"))  # audio has no frame


def test_library_scan_with_real_probe(tmp_path):
    from vlclone.core.library import LibraryDB
    from vlclone.core.library_scanner import LibraryService

    videos = tmp_path / "Videos"
    music = tmp_path / "Music"
    videos.mkdir()
    music.mkdir()
    write_y4m(str(videos / "Bars.y4m"))
    write_chapter_mkv(str(music / "Tone.mka"), seconds=3, title="Tone Title")
    try:
        vlc_backend.VlcMediaProbe().close()  # the service creates its probe on the worker thread
    except vlc_backend.LibVlcNotFound as exc:
        pytest.skip(str(exc))
    svc = LibraryService(LibraryDB(tmp_path / "lib.db"), vlc_backend.VlcMediaProbe,
                         thumbs_dir=tmp_path / "thumbs", watch=False)
    svc.add_folder(str(videos), "video")
    svc.add_folder(str(music), "music")
    assert svc.wait(60)
    vids = svc.videos()
    assert [(v.title, v.width, v.height) for v in vids] == [("Bars", 64, 48)]
    assert vids[0].thumb and vids[0].duration_ms == 2000
    tracks = [t for albums in svc.music().values() for al in albums for t in al.tracks]
    assert [(t.title, t.duration_ms) for t in tracks] == [("Tone Title", 3000)]
    svc.shutdown()


def test_capture_device_listing_does_not_crash(backend):
    devices = backend.capture_devices()
    assert set(devices) == {"video", "audio"}
    # The libvlccore choices hook VLC's preferences use (DirectShow on Windows) works here too.
    choices = vlc_backend.config_choices(backend.vlc, backend.instance, "alsa-audio-device")
    if choices is not None:
        assert ("default", "Default") in choices
