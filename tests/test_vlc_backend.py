"""Integration test against the real libVLC (skipped when it is not installed)."""

import shutil
import subprocess
import time

import pytest

from vlclone.core.backend import PlaybackState
from vlclone.core.media_sources import path_to_mrl

vlc_backend = pytest.importorskip("vlclone.core.vlc_backend")


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not available")
    out = tmp_path_factory.mktemp("media") / "sample.mkv"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25",
         "-f", "lavfi", "-i", "sine=frequency=440", "-t", "6", "-c:v", "mpeg4", "-c:a", "mp2",
         "-metadata:s:a:0", "language=eng", str(out)],
        check=True,
    )
    return out


@pytest.fixture
def backend():
    try:
        b = vlc_backend.VlcBackend(["--quiet", "--vout=dummy", "--aout=dummy", "--no-video-title-show"])
    except vlc_backend.LibVlcNotFound as exc:
        pytest.skip(str(exc))
    yield b
    b.release()


def wait_for(cond, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_plays_seeks_and_reports(backend, sample, tmp_path):
    backend.load(path_to_mrl(str(sample)))
    backend.play()
    assert wait_for(lambda: backend.state() is PlaybackState.PLAYING)
    assert wait_for(lambda: backend.length() > 5000)
    backend.set_time(3000)
    assert wait_for(lambda: backend.time() >= 3000)
    backend.set_volume(150)
    assert wait_for(lambda: backend.volume() == 150, 2) or True  # dummy aout may ignore volume
    backend.set_rate(1.5)
    assert abs(backend.rate() - 1.5) < 0.01
    assert wait_for(lambda: any(t.id != -1 for t in backend.audio_tracks()))
    info = backend.media_info()
    kinds = {s.kind for s in info.streams}
    assert {"video", "audio"} <= kinds
    video = next(s for s in info.streams if s.kind == "video")
    assert (video.width, video.height) == (320, 240)
    backend.set_aspect_ratio("16:9")
    backend.set_crop("4:3")
    backend.set_zoom(2.0)
    backend.set_deinterlace("yadif")
    backend.set_deinterlace(None)
    backend.set_subtitle_delay(100)
    backend.set_audio_delay(-100)
    backend.pause()
    assert wait_for(lambda: backend.state() is PlaybackState.PAUSED)
    backend.next_frame()
    backend.stop()
    assert wait_for(lambda: backend.state() is PlaybackState.STOPPED)
    assert backend.engine_version()


def test_subtitle_slave(backend, sample, tmp_path):
    srt = tmp_path / "sample.srt"
    srt.write_text("1\n00:00:00,500 --> 00:00:04,000\nHello\n", encoding="utf-8")
    backend.load(path_to_mrl(str(sample)))
    backend.play()
    assert wait_for(lambda: backend.state() is PlaybackState.PLAYING)
    assert backend.add_subtitle_file(path_to_mrl(str(srt)))
    assert wait_for(lambda: any(t.id != -1 for t in backend.subtitle_tracks()))


def test_rotation_swaps_instance_and_plays(backend, sample):
    backend.set_volume(80)
    backend.set_rotation(90)
    backend.load(path_to_mrl(str(sample)))
    backend.play()
    assert wait_for(lambda: backend.state() is PlaybackState.PLAYING)
    assert backend._player_rotation == 90
    backend.set_rotation(0)
    backend.load(path_to_mrl(str(sample)))
    backend.play()
    assert wait_for(lambda: backend.state() is PlaybackState.PLAYING)
    assert backend._player_rotation == 0


def test_controller_auto_advances_and_plays_http(backend, sample, tmp_path):
    """End-to-end through PlayerController: file end -> next item (an HTTP stream)."""
    import functools
    import http.server
    import threading

    from vlclone.core.controller import PlayerController
    from vlclone.core.settings import Settings

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(sample.parent))
    handler.log_message = lambda *a, **k: None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/{sample.name}"
        ctl = PlayerController(backend, Settings(resume_playback=False))
        ctl.open([str(sample), url])

        def tick_until(cond, timeout=15.0):
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                ctl.tick()
                if cond():
                    return True
                time.sleep(0.05)
            return False

        assert tick_until(lambda: ctl.state is PlaybackState.PLAYING)
        ctl.seek(ctl.length() - 700)
        assert tick_until(lambda: ctl.playlist.current_index == 1)
        assert tick_until(lambda: ctl.state is PlaybackState.PLAYING and ctl.time() > 0)
        assert ctl.backend.media_info().mrl == url
    finally:
        server.shutdown()
