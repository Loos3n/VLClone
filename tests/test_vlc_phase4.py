"""Phase 4 against the real libVLC (skipped without it): every enabled preset, remux, trim, cancel,
recording (segment and live), HTTP streaming fetched locally, screen recording."""

import os
import shutil
import subprocess
import time
import urllib.request

import pytest

from vlclone.core.backend import PlaybackState
from vlclone.core.controller import PlayerController
from vlclone.core.converter import ConvertQueue, JobState, probe_presets
from vlclone.core.media_sources import path_to_mrl
from vlclone.core.recorder import Recorder
from vlclone.core.settings import Settings
from vlclone.core.streamer import HttpStreamer
from vlclone.core.transcode import CONVERT_PRESETS, PRESETS, ConvertSettings
from vlclone.selftest_media import write_tone, write_y4m

vlc_backend = pytest.importorskip("vlclone.core.vlc_backend")


@pytest.fixture(scope="module")
def engine():
    try:
        e = vlc_backend.VlcTranscoder()
    except vlc_backend.LibVlcNotFound as exc:
        pytest.skip(str(exc))
    yield e
    e.release()


@pytest.fixture(scope="module")
def caps(engine):
    return probe_presets(engine)


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    """A 4 s clip with H.264-friendly video and audio, keyframe every second."""
    folder = tmp_path_factory.mktemp("src")
    if shutil.which("ffmpeg"):
        out = folder / "source.mkv"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25",
                        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100", "-t", "4", "-c:v", "libx264",
                        "-preset", "ultrafast", "-g", "25", "-c:a", "aac", str(out)], check=True)
        return path_to_mrl(str(out)), []
    video, audio = folder / "v.y4m", folder / "a.wav"
    write_y4m(str(video), 160, 120, frames=100)
    write_tone(str(audio), 4)
    return path_to_mrl(str(video)), [f":input-slave={path_to_mrl(str(audio))}"]


@pytest.fixture(scope="module")
def long_source(tmp_path_factory):
    """15 s of video + audio: long enough to still be streaming while a test records it."""
    folder = tmp_path_factory.mktemp("long")
    video, audio = folder / "v.y4m", folder / "a.wav"
    write_y4m(str(video), 160, 120, frames=375)
    write_tone(str(audio), 15)
    return path_to_mrl(str(video)), [f":input-slave={path_to_mrl(str(audio))}"]


def run_queue(queue, job, timeout=60):
    end = time.monotonic() + timeout
    while not job.finished and time.monotonic() < end:
        queue.tick()
        time.sleep(0.05)
    return job


def ffprobe(path):
    if not shutil.which("ffprobe"):
        return None
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,codec_type:format=duration",
                          "-of", "compact", path], capture_output=True, text=True).stdout
    return out


def plays(path):
    try:
        b = vlc_backend.VlcBackend(["--quiet", "--vout=dummy", "--aout=dummy"])
    except vlc_backend.LibVlcNotFound:
        return True
    try:
        b.load(path_to_mrl(path))
        b.play()
        end = time.monotonic() + 8
        while time.monotonic() < end:
            if b.state() is PlaybackState.PLAYING and b.time() > 0:
                return True
            if b.state() in (PlaybackState.ERROR, PlaybackState.ENDED):
                return b.state() is PlaybackState.ENDED  # very short file may end before we look
            time.sleep(0.05)
        return False
    finally:
        b.release()


FFPROBE_NAMES = {"h264": "h264", "hevc": "hevc", "VP80": "vp8", "VP90": "vp9", "mp4a": "aac", "vorb": "vorbis",
                 "Opus": "opus", "flac": "flac", "mpga": "mp3"}


def test_probe_reports_every_preset(caps):
    assert set(caps) == set(CONVERT_PRESETS)
    assert caps["mp4_h264"].ok and caps["mp3"].ok  # the essentials work on any normal VLC 3 build


@pytest.mark.parametrize("key", CONVERT_PRESETS)
def test_preset_converts_and_plays(engine, caps, source, key, tmp_path):
    if not caps[key].ok:
        pytest.skip(f"{key} unavailable in this libVLC: {caps[key].reason}")
    mrl, opts = source
    queue = ConvertQueue(lambda: engine)
    preset = PRESETS[key]
    job = run_queue(queue, queue.add(mrl, "source", ConvertSettings(key), str(tmp_path / f"out.{preset.ext}"), opts))
    assert job.state is JobState.DONE, job.error
    assert os.path.exists(job.output) and not os.path.exists(job.part)
    info = engine.media_info(job.output)
    if preset.expect_video:
        assert preset.expect_video in info.video
    if preset.expect_audio:
        assert preset.expect_audio in info.audio
    if preset.audio_only:
        assert not info.video
    probe = ffprobe(job.output)
    if probe is not None:
        for fourcc in [preset.expect_video, preset.expect_audio]:
            if fourcc:
                assert f"codec_name={FFPROBE_NAMES[fourcc]}" in probe, probe
    assert plays(job.output)


def test_scale_bitrate_and_exact_trim(engine, caps, source, tmp_path):
    mrl, opts = source
    queue = ConvertQueue(lambda: engine)
    s = ConvertSettings("mp4_h264", max_height=96, video_kbps=600, audio_kbps=96, start_ms=1000, end_ms=3000)
    job = run_queue(queue, queue.add(mrl, "s", s, str(tmp_path / "small.mp4"), opts))
    assert job.state is JobState.DONE, job.error
    probe = ffprobe(job.output)
    if probe is not None:
        duration = float(probe.split("format|duration=")[1].split()[0])
        assert 1.7 <= duration <= 2.4, probe  # a transcode trims exactly
        height = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries",
                                 "stream=height", "-of", "csv=p=0", job.output],
                                capture_output=True, text=True).stdout.strip()
        assert int(height) <= 96


def test_remux_both_ways_with_trim(engine, caps, source, tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("needs an H.264 source from ffmpeg")
    mrl, _ = source
    queue = ConvertQueue(lambda: engine)
    mkv = run_queue(queue, queue.add(mrl, "s", ConvertSettings("remux_mkv", start_ms=1000, end_ms=3000),
                                     str(tmp_path / "copy.mkv")))
    assert mkv.state is JobState.DONE, mkv.error
    assert engine.media_info(mkv.output).video == ["h264"]
    mp4 = run_queue(queue, queue.add(mrl, "s", ConvertSettings("remux_mp4"), str(tmp_path / "copy.mp4")))
    assert mp4.state is JobState.DONE, mp4.error
    info = engine.media_info(mp4.output)
    assert info.video == ["h264"] and info.audio == ["mp4a"] and 3500 <= info.duration_ms <= 4500
    probe = ffprobe(mkv.output)
    if probe is not None:  # keyframe every second: the copy starts at 1 s
        duration = float(probe.split("format|duration=")[1].split()[0])
        assert 1.5 <= duration <= 3.2, probe


def test_cancel_removes_partial_output(engine, tmp_path):
    long_src = tmp_path / "long.y4m"
    write_y4m(str(long_src), 640, 480, frames=750)  # 30 s raw video: takes a while to encode
    queue = ConvertQueue(lambda: engine)
    job = queue.add(path_to_mrl(str(long_src)), "long", ConvertSettings("mp4_h264"),
                    str(tmp_path / "long.mp4"))
    queue.tick()
    end = time.monotonic() + 10
    while job.progress < 0.05 and time.monotonic() < end:
        queue.tick()
        time.sleep(0.05)
    assert job.state is JobState.RUNNING
    queue.cancel(job.id)
    assert job.state is JobState.CANCELLED
    assert not os.path.exists(job.part) and not os.path.exists(job.output)
    assert os.listdir(tmp_path) == ["long.y4m"]


def test_unreadable_source_fails_cleanly(engine, tmp_path):
    bad = tmp_path / "bad.mkv"
    bad.write_bytes(b"this is not a video" * 100)
    queue = ConvertQueue(lambda: engine)
    job = run_queue(queue, queue.add(path_to_mrl(str(bad)), "bad", ConvertSettings("mp4_h264"),
                                     str(tmp_path / "bad.mp4")), 20)
    assert job.state is JobState.FAILED and job.error
    assert sorted(os.listdir(tmp_path)) == ["bad.mkv"]


# ---------------------------------------------------------------- recording
@pytest.fixture
def player():
    try:
        b = vlc_backend.VlcBackend(["--quiet", "--vout=dummy", "--aout=dummy"])
    except vlc_backend.LibVlcNotFound as exc:
        pytest.skip(str(exc))
    yield b
    b.release()


def tick_until(ctl, cond, timeout=15.0, extra=()):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        ctl.tick()
        for f in extra:
            f()
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_record_segment_of_a_file(engine, player, source, tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("needs an H.264 source from ffmpeg")
    mrl, _ = source
    ctl = PlayerController(player, Settings(resume_playback=False, record_dir=str(tmp_path)))
    queue = ConvertQueue(lambda: engine)
    rec = Recorder(ctl, queue, ctl.settings)
    ctl.open([mrl])
    assert tick_until(ctl, lambda: ctl.state is PlaybackState.PLAYING and ctl.time() > 300)
    assert rec.start() is None and rec.mode == "segment"
    assert tick_until(ctl, lambda: rec.elapsed_ms() >= 2000, 10, [rec.tick])
    job = rec.stop()
    run_queue(queue, job)
    assert job.state is JobState.DONE, job.error
    info = engine.media_info(job.output)
    assert info.video == ["h264"] and 1500 <= info.duration_ms <= 4200


def test_live_stream_record_and_http_streaming(engine, player, long_source, tmp_path):
    """Stream a file over HTTP (a live channel), fetch it, then record the stream while it plays."""
    import socket

    mrl, opts = long_source
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    streamer = HttpStreamer(lambda: engine)
    assert streamer.start(mrl, opts, "source", 0, "h264", port) is None
    try:
        url = f"http://127.0.0.1:{port}/stream"
        end = time.monotonic() + 10
        data = b""
        while time.monotonic() < end and len(data) < 188 * 50:
            try:
                with urllib.request.urlopen(url, timeout=3) as resp:
                    data += resp.read(188 * 50)
            except OSError:
                time.sleep(0.2)
        assert streamer.poll() in ("starting", "streaming")
        assert len(data) >= 188 * 10 and all(data[i] == 0x47 for i in range(0, 188 * 10, 188))  # MPEG-TS

        ctl = PlayerController(player, Settings(resume_playback=False, record_dir=str(tmp_path)))
        queue = ConvertQueue(lambda: engine)
        rec = Recorder(ctl, queue, ctl.settings)
        ctl.open([url])
        assert tick_until(ctl, lambda: ctl.state is PlaybackState.PLAYING and ctl.time() > 0, 15)
        assert rec.start() is None and rec.mode == "live"
        assert tick_until(ctl, lambda: rec.elapsed_ms() >= 2000, 10, [rec.tick, queue.tick])
        job = rec.stop()
        assert job.state is JobState.DONE, job.error
        assert job.output.endswith(".mkv") and os.path.getsize(job.output) > 0
        info = engine.media_info(job.output)
        assert info.video or info.audio
    finally:
        streamer.stop()


def test_screen_recording(engine, tmp_path):
    if not (engine.has_module("screen") or engine.has_module("xcb_screen")):
        pytest.skip("no screen-capture module in this libVLC")
    if os.name != "nt" and not os.environ.get("DISPLAY"):
        pytest.skip("no X display")
    from vlclone.core.capture import ScreenRequest, screen_mrl

    mrl, opts, title = screen_mrl(ScreenRequest(fps=10, width=320, height=240))
    queue = ConvertQueue(lambda: engine)
    job = queue.add(mrl, title, ConvertSettings("mkv_h264", live=True), str(tmp_path / "screen.mkv"), opts,
                    live=True)
    end = time.monotonic() + 3
    while time.monotonic() < end:
        queue.tick()
        time.sleep(0.05)
    queue.finish(job.id)
    assert job.state is JobState.DONE, job.error
    info = engine.media_info(job.output)
    assert info.video == ["h264"]
    probe = ffprobe(job.output)
    if probe is not None:  # zerolatency: the last seconds are kept
        duration = float(probe.split("format|duration=")[1].split()[0])
        assert duration >= 1.5, probe
