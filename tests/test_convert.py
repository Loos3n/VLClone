"""Convert/record/stream core: sout chains, naming, queue, cleanup, probe, recorder, streamer (fake engine)."""

import os
import socket

import pytest

from vlclone.core.capture import ScreenRequest, is_capture_mrl, screen_mrl
from vlclone.core.controller import PlayerController
from vlclone.core.converter import ConvertQueue, JobState, PresetProber, probe_presets, run_to_end
from vlclone.core.fake_backend import FakeBackend, FakeTranscoder
from vlclone.core.media_sources import path_to_mrl
from vlclone.core.recorder import Recorder, recording_name
from vlclone.core.settings import Settings
from vlclone.core.streamer import HttpStreamer, port_available, stream_sout, stream_url
from vlclone.core.transcode import (CONVERT_PRESETS, PRESETS, CapsStore, ConvertSettings, PresetSupport, SourceInfo,
                                    build_sout, check_output, media_options, mpeg_audio_layer, output_path, part_path,
                                    sout_path, transcode_chain, unique_path, validate)


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


# ------------------------------------------------------------------ chains
def test_mp4_chain():
    s = ConvertSettings("mp4_h264", max_height=720, video_kbps=1200, audio_kbps=128)
    assert transcode_chain(PRESETS["mp4_h264"], s) == (
        "#transcode{vcodec=h264,vb=1200,maxheight=720,acodec=mp4a,ab=128,channels=2,samplerate=44100}")
    assert build_sout(PRESETS["mp4_h264"], s, "/out/a.mp4") == (
        "#transcode{vcodec=h264,vb=1200,maxheight=720,acodec=mp4a,ab=128,channels=2,samplerate=44100}"
        ":std{access=file,mux=mp4,dst='/out/a.mp4'}")


@pytest.mark.parametrize("key,fragment", [
    ("mkv_h265", "vcodec=hevc,vb=1500"), ("webm_vp8", "vcodec=VP80"), ("webm_vp9", "acodec=opus,ab=128,"
                                                                                    "channels=2,samplerate=48000"),
    ("mp3", "#transcode{acodec=mp3,ab=192,"), ("flac", "#transcode{acodec=flac,channels=2,samplerate=44100}"),
    ("ogg_vorbis", "acodec=vorb"), ("m4a_aac", "mux=mp4"),
])
def test_preset_chains(key, fragment):
    assert fragment in build_sout(PRESETS[key], ConvertSettings(key), "/o/x")


def test_mp3_uses_the_mp3_encoder_not_mpga():
    # acodec=mpga makes libVLC write MPEG layer II.
    assert "acodec=mp3," in build_sout(PRESETS["mp3"], ConvertSettings("mp3"), "/o/x.mp3")


def test_remux_and_options():
    opts = media_options(PRESETS["remux_mkv"], ConvertSettings("remux_mkv", start_ms=5000, end_ms=9500), "/o/r.mkv")
    assert opts == [":sout=#std{access=file,mux=mkv,dst='/o/r.mkv'}", ":sout-all", ":start-time=5.000",
                    ":stop-time=9.500"]
    assert ":no-sout-spu" in media_options(PRESETS["remux_mp4"], ConvertSettings("remux_mp4"), "/o/r.mp4")
    audio = media_options(PRESETS["mp3"], ConvertSettings("mp3"), "/o/a.mp3")
    assert ":no-sout-video" in audio and ":no-sout-spu" in audio


def test_subtitle_burn_in_and_live_encoder():
    burn = media_options(PRESETS["mp4_h264"], ConvertSettings("mp4_h264", subtitles="embedded"), "/o/a.mp4")
    assert "soverlay" in burn[0] and ":sub-track=0" in burn and ":no-sout-spu" not in burn
    sub = media_options(PRESETS["mp4_h264"], ConvertSettings("mp4_h264", subtitles="/s/a.srt"), "/o/a.mp4")
    assert ":sub-file=/s/a.srt" in sub
    live = build_sout(PRESETS["mkv_h264"], ConvertSettings("mkv_h264", live=True), "/o/a.mkv")
    assert "venc=x264{tune=zerolatency}" in live


def test_sout_path_escaping(monkeypatch):
    assert sout_path("/home/me/it's a test.mp4") == "'/home/me/it\\'s a test.mp4'"
    monkeypatch.setattr(os, "name", "nt")
    assert sout_path("C:\\Users\\Me\\Out\\film.mp4") == "'C:/Users/Me/Out/film.mp4'"


def test_validate():
    mp4 = PRESETS["remux_mp4"]
    assert validate(mp4, ConvertSettings("remux_mp4"), SourceInfo(["h264"], ["mp4a"])) is None
    assert "MKV" in validate(mp4, ConvertSettings("remux_mp4"), SourceInfo(["VP90"], ["Opus"]))
    assert "end" in validate(PRESETS["mp3"], ConvertSettings("mp3", start_ms=5000, end_ms=4000))
    assert "no audio" in validate(PRESETS["mp3"], ConvertSettings("mp3"), SourceInfo(["h264"], []))
    assert "subtitle" in validate(PRESETS["mp4_h264"], ConvertSettings("mp4_h264", subtitles="/nope.srt"))


# ------------------------------------------------------------------ naming
def test_output_names(tmp_path):
    src = tmp_path / "Film.mkv"
    src.write_bytes(b"x")
    p = PRESETS["mp4_h264"]
    assert output_path("Film.mkv", str(tmp_path), p) == str(tmp_path / "Film.mp4")
    (tmp_path / "Film.mp4").write_bytes(b"x")
    assert output_path("Film.mkv", str(tmp_path), p) == str(tmp_path / "Film (1).mp4")
    assert output_path("Film.mkv", str(tmp_path), PRESETS["remux_mkv"]) == str(tmp_path / "Film (converted).mkv")
    taken = {str(tmp_path / "Film (1).mp4")}
    assert output_path("Film.mkv", str(tmp_path), p, taken=taken) == str(tmp_path / "Film (2).mp4")
    assert output_path("Live: TV/news?", None, p, str(tmp_path)) == str(tmp_path / "Live_ TV_news_.mp4")
    (tmp_path / "x.mp3.part").write_bytes(b"")
    assert unique_path(str(tmp_path / "x.mp3")) == str(tmp_path / "x (1).mp3")
    assert part_path("/a/b.mp4") == "/a/b.mp4.part"


def test_check_output_and_mp3_layer(tmp_path):
    p = PRESETS["mp4_h264"]
    assert check_output(p, SourceInfo(["h264"], ["mp4a"])) is None
    assert "expected mp4a" in check_output(p, SourceInfo(["h264"], []))
    assert check_output(p, SourceInfo(["h264"], []), strict=False) is None  # video-only source
    assert "h264" in check_output(p, SourceInfo(["mp4v"], ["mp4a"]), strict=False)
    mp3 = tmp_path / "a.mp3"
    mp3.write_bytes(b"ID3\x03\x00\x00\x00\x00\x00\x02xx" + b"\xff\xfb\x90\x00" + b"\0" * 100)
    assert mpeg_audio_layer(str(mp3)) == 3
    mp2 = tmp_path / "b.mp3"
    mp2.write_bytes(b"\xff\xfd\x90\x00" + b"\0" * 100)
    assert mpeg_audio_layer(str(mp2)) == 2
    assert "layer II" in check_output(PRESETS["mp3"], SourceInfo([], ["mpga"]), str(mp2))
    assert check_output(PRESETS["mp3"], SourceInfo([], ["mpga"]), str(mp3)) is None


# ------------------------------------------------------------------- queue
@pytest.fixture
def engine():
    return FakeTranscoder()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def queue(engine, clock):
    return ConvertQueue(lambda: engine, clock)


def add(queue, tmp_path, name="a", preset="mp4_h264", **kw):
    p = PRESETS[preset]
    return queue.add(f"file:///src/{name}.mkv", name, ConvertSettings(preset, **{k: v for k, v in kw.items()
                                                                                   if k != "live"}),
                     str(tmp_path / f"{name}.{p.ext}"), live=kw.get("live", False))


def test_queue_runs_jobs_one_by_one_with_progress_and_eta(queue, engine, clock, tmp_path):
    a, b = add(queue, tmp_path, "a"), add(queue, tmp_path, "b")
    queue.tick()
    assert a.state is JobState.RUNNING and b.state is JobState.QUEUED
    assert os.path.exists(a.part) and not os.path.exists(a.output)  # writes the .part only
    clock.t += 4
    engine.advance(2500)
    queue.tick()
    assert abs(a.progress - 0.25) < 1e-6 and abs(a.eta_s - 12.0) < 1e-6
    engine.advance(7500)
    queue.tick()
    assert a.state is JobState.DONE and os.path.exists(a.output) and not os.path.exists(a.part)
    assert a.progress == 1.0
    queue.tick()
    assert b.state is JobState.RUNNING


def test_trim_progress_uses_the_trimmed_span(queue, engine, clock, tmp_path):
    job = add(queue, tmp_path, "t", start_ms=2000, end_ms=6000)
    queue.tick()
    engine.advance(2000)  # fake starts at start-time: now at 4 s of 2–6 s
    queue.tick()
    assert abs(job.progress - 0.5) < 1e-6


def test_cancel_deletes_the_partial_file(queue, engine, tmp_path):
    job = add(queue, tmp_path)
    queue.tick()
    engine.advance(3000)
    queue.tick()
    queue.cancel(job.id)
    assert job.state is JobState.CANCELLED
    assert not os.path.exists(job.part) and not os.path.exists(job.output)
    assert engine.handles[0]["state"] == "stopped"
    queued = add(queue, tmp_path, "q")
    queue.cancel(queued.id)
    assert queued.state is JobState.CANCELLED


def test_engine_error_fails_and_cleans_up(queue, engine, tmp_path):
    engine.fail_mrls.add("file:///src/bad.mkv")
    job = add(queue, tmp_path, "bad")
    queue.tick()
    queue.tick()
    assert job.state is JobState.FAILED and "could not convert" in job.error
    assert not os.path.exists(job.part)


def test_missing_encoder_is_reported_not_saved(queue, engine, tmp_path):
    engine.unsupported.add("VP90")
    engine.unsupported.add("opus")
    job = add(queue, tmp_path, "w", "webm_vp9")
    queue.tick()
    engine.advance(10_000)
    queue.tick()
    assert job.state is JobState.FAILED and "Nothing was written" in job.error
    assert not os.path.exists(job.part) and not os.path.exists(job.output)


def test_wrong_codec_is_caught(queue, engine, tmp_path):
    engine.CODEC_FOURCC = dict(engine.CODEC_FOURCC, mp4a="mpga")  # an engine that writes the wrong audio
    job = add(queue, tmp_path, "w")
    queue.tick()
    engine.advance(10_000)
    queue.tick()
    assert job.state is JobState.FAILED and "mp4a" in job.error and not os.path.exists(job.output)


def test_never_overwrites_an_existing_file(queue, engine, tmp_path):
    job = add(queue, tmp_path)
    queue.tick()
    (tmp_path / "a.mp4").write_bytes(b"precious")  # appears while converting
    engine.advance(10_000)
    queue.tick()
    assert job.state is JobState.DONE and job.output == str(tmp_path / "a (1).mp4")
    assert (tmp_path / "a.mp4").read_bytes() == b"precious"


def test_same_output_name_in_queue_gets_a_new_name(queue, tmp_path):
    a = add(queue, tmp_path, "same")
    b = add(queue, tmp_path, "same")
    assert a.output != b.output and b.output.endswith("same (1).mp4")


def test_live_jobs_run_alongside_and_keep_their_file(queue, engine, clock, tmp_path):
    a = add(queue, tmp_path, "a")
    queue.tick()
    rec = queue.add("screen://", "Screen", ConvertSettings("mkv_h264", live=True), str(tmp_path / "s.mkv"),
                    [":screen-fps=10"], live=True)
    assert rec.state is JobState.RUNNING and a.state is JobState.RUNNING
    assert engine.started[-1][1][0] == ":screen-fps=10"
    clock.t += 5
    queue.tick()
    assert rec.elapsed_s == 5 and rec.state is JobState.RUNNING
    queue.finish(rec.id)
    assert rec.state is JobState.DONE and os.path.exists(rec.output)


def test_stalled_job_fails(queue, engine, clock, tmp_path):
    job = add(queue, tmp_path)
    queue.tick()
    clock.t += queue.STALL_S + 1
    queue.tick()
    assert job.state is JobState.FAILED and "No progress" in job.error and not os.path.exists(job.part)


def test_remove_retry_clear_and_shutdown(queue, engine, clock, tmp_path):
    a = add(queue, tmp_path, "a")
    queue.tick()
    queue.cancel(a.id)
    queue.retry(a.id)
    assert a.state is JobState.QUEUED
    queue.tick()
    engine.advance(10_000)
    queue.tick()
    b = add(queue, tmp_path, "b")
    queue.tick()
    queue.clear_finished()
    assert queue.jobs == [b]
    rec = queue.add("screen://", "S", ConvertSettings("mkv_h264", live=True), str(tmp_path / "r.mkv"), live=True)
    queue.shutdown()
    assert b.state is JobState.CANCELLED and not os.path.exists(b.part)
    assert rec.state is JobState.DONE  # recordings are kept on exit
    assert engine.released
    gen = queue.generation
    queue.remove(rec.id)
    assert queue.generation > gen and rec not in queue.jobs


def test_engine_start_failure(tmp_path):
    class Broken(FakeTranscoder):
        def start(self, mrl, options):
            raise RuntimeError("no libVLC")

    q = ConvertQueue(Broken)
    job = add(q, tmp_path)
    q.tick()
    assert job.state is JobState.FAILED and "no libVLC" in job.error


def test_run_to_end(engine, tmp_path):
    engine.instant = True
    dst = (tmp_path / "out.mkv").as_posix()
    assert run_to_end(engine, "file:///a.mkv", [f":sout=#std{{access=file,mux=mkv,dst='{dst}'}}"], 2) == "ended"


# ------------------------------------------------------------------- probe
def test_probe_marks_unsupported_presets():
    engine = FakeTranscoder(instant=True)
    engine.unsupported |= {"hevc", "webm"}
    caps = probe_presets(engine)
    assert set(caps) == set(CONVERT_PRESETS)
    assert not caps["mkv_h265"].ok and not caps["webm_vp8"].ok and not caps["webm_vp9"].ok
    assert caps["mp4_h264"].ok and caps["mp3"].ok and caps["remux_mkv"].ok


def test_probe_catches_mp2_written_as_mp3():
    engine = FakeTranscoder(instant=True)
    engine.MP3_FRAME = b"\xff\xfd\x90\x00"  # layer II
    caps = probe_presets(engine, ["mp3"])
    assert not caps["mp3"].ok and "layer II" in caps["mp3"].reason


def test_prober_caches_by_engine_version(tmp_path):
    store = CapsStore(tmp_path / "caps.json")
    calls = []

    def factory():
        e = FakeTranscoder(instant=True)
        calls.append(e)
        return e

    p = PresetProber(factory, store)
    p.start()
    assert p.wait(30) and p.support("mp4_h264").ok
    assert calls[0].released
    first = len(calls[0].started)
    assert first >= len(CONVERT_PRESETS)
    p2 = PresetProber(factory, store)
    p2.start()
    assert p2.wait(30) and len(calls[1].started) == 0  # from the cache
    store.save("other", {k: PresetSupport(True) for k in CONVERT_PRESETS})
    assert store.load("fake 1.0") is None


def test_prober_failure_enables_everything(tmp_path):
    def factory():
        raise RuntimeError("libVLC missing")

    p = PresetProber(factory, CapsStore(tmp_path / "c.json"))
    p.start()
    assert p.wait(10) and p.support("webm_vp9").ok and "libVLC missing" in p.error


# ---------------------------------------------------------------- recorder
@pytest.fixture
def ctl():
    return PlayerController(FakeBackend(), Settings(resume_playback=False))


def test_record_file_segment(ctl, queue, engine, tmp_path):
    ctl.settings.record_dir = str(tmp_path)
    rec = Recorder(ctl, queue, ctl.settings)
    ctl.open([path_to_mrl("/films/movie.mkv")])
    ctl.tick()
    ctl.backend.advance(10_000)
    assert rec.start() is None and rec.mode == "segment" and rec.active
    ctl.backend.advance(5000)
    rec.tick()
    assert rec.elapsed_ms() == 5000
    job = rec.stop()
    assert not rec.active
    assert job.settings.preset == "remux_mkv" and (job.settings.start_ms, job.settings.end_ms) == (10_000, 15_000)
    assert job.output.startswith(str(tmp_path)) and job.output.endswith(".mkv") and "movie" in job.output
    assert queue.jobs == [job] and not job.live


def test_record_too_short_saves_nothing(ctl, queue):
    rec = Recorder(ctl, queue, ctl.settings)
    ctl.open(["file:///films/movie.mkv"])
    ctl.tick()
    rec.start()
    assert rec.stop() is None and queue.jobs == []


def test_record_live_stream(ctl, queue, engine, tmp_path):
    ctl.settings.record_dir = str(tmp_path)
    ctl.backend.lengths["udp://@:1234"] = -1  # live: no length
    rec = Recorder(ctl, queue, ctl.settings)
    ctl.open(["udp://@:1234"], options=[":network-caching=2000"])
    ctl.tick()
    assert rec.start() is None and rec.mode == "live"
    job = queue.jobs[0]
    assert job.live and job.state is JobState.RUNNING and job.settings.live
    assert engine.started[0][0] == "udp://@:1234" and ":network-caching=2000" in engine.started[0][1]
    saved = rec.stop()
    assert saved is job and job.state is JobState.DONE and os.path.exists(job.output)


def test_record_live_uses_fallback_muxer(ctl, queue, tmp_path):
    ctl.settings.record_dir = str(tmp_path)
    ctl.backend.lengths["udp://@:1234"] = -1
    rec = Recorder(ctl, queue, ctl.settings, live_preset=lambda: "remux_ts")
    ctl.open(["udp://@:1234"])
    ctl.tick()
    rec.start()
    assert queue.jobs[0].output.endswith(".ts")


def test_record_refuses_capture_and_idle(ctl, queue):
    rec = Recorder(ctl, queue, ctl.settings)
    assert "Play something" in rec.start()
    ctl.open(["dshow://"])
    ctl.tick()
    assert "Capture" in rec.start() and not rec.active


def test_record_stops_when_media_changes(ctl, queue, tmp_path):
    ctl.settings.record_dir = str(tmp_path)
    events = []
    rec = Recorder(ctl, queue, ctl.settings)
    rec.changed.connect(events.append)
    ctl.open(["file:///a.mkv", "file:///b.mkv"])
    ctl.tick()
    rec.start()
    ctl.backend.advance(3000)
    rec.tick()
    ctl.next()
    ctl.tick()
    rec.tick()
    assert not rec.active and events == [True, False]
    assert queue.jobs[0].settings.end_ms == 3000  # the last position seen on the recorded file


def test_recording_name():
    from datetime import datetime

    assert recording_name("My: Show.mkv", "ts", datetime(2026, 1, 2, 3, 4, 5)) == "My_ Show 2026-01-02 03.04.05.ts"


# ---------------------------------------------------------------- streamer
def test_stream_chains():
    assert stream_sout("copy", 8080) == "#std{access=http,mux=ts,dst=:8080/stream}"
    assert stream_sout("h264", 9000).startswith("#transcode{vcodec=h264")
    assert stream_url(8080, "192.168.1.5") == "http://192.168.1.5:8080/stream"


def test_streamer_start_stop(engine):
    s = HttpStreamer(lambda: engine)
    port = _free_port()
    assert s.start("file:///a.mkv", [":network-caching=100", ":start-time=3"], "A", 12_000, "copy", port) is None
    mrl, opts = engine.started[-1]
    assert opts[0] == ":network-caching=100" and ":start-time=12.000" in opts and ":start-time=3" not in opts
    assert f":sout=#std{{access=http,mux=ts,dst=:{port}/stream}}" in opts
    assert s.poll() == "streaming" and s.active
    s.stop()
    assert not s.active and s.poll() == "idle"


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_streamer_rejects_busy_port(engine):
    with socket.socket() as sock:
        sock.bind(("0.0.0.0", 0))
        sock.listen()
        port = sock.getsockname()[1]
        assert not port_available(port)
        assert "in use" in HttpStreamer(lambda: engine).start("file:///a", [], port=port)
    assert not port_available(0) and not port_available(70000)


def test_streamer_reports_engine_error(engine):
    engine.fail_mrls.add("file:///bad")
    s = HttpStreamer(lambda: engine)
    s.start("file:///bad", [], port=_free_port())
    assert s.poll() == "error" and s.error and not s.active


# ------------------------------------------------------------- screen/misc
def test_screen_mrl():
    mrl, opts, title = screen_mrl(ScreenRequest(fps=10))
    assert (mrl, title) == ("screen://", "Screen") and opts == [":screen-fps=10", ":live-caching=300"]
    _, opts, title = screen_mrl(ScreenRequest(30, 100, 50, 1280, 720, follow_mouse=True))
    assert ":screen-width=1280" in opts and ":screen-follow-mouse" in opts and title == "Screen 1280×720"
    with pytest.raises(ValueError):
        screen_mrl(ScreenRequest(width=100))
    with pytest.raises(ValueError):
        screen_mrl(ScreenRequest(fps=0))
    assert is_capture_mrl("screen://")


def test_settings_round_trip(tmp_path):
    s = Settings(convert_output_dir="/out", convert_preset="flac", record_dir="/rec", stream_port=9000,
                 stream_mode="h264", convert_next_to_source=True)
    s.save(tmp_path / "s.json")
    again = Settings.load(tmp_path / "s.json")
    assert (again.convert_output_dir, again.convert_preset, again.record_dir, again.stream_port,
            again.stream_mode, again.convert_next_to_source) == ("/out", "flac", "/rec", 9000, "h264", True)
    bad = Settings(convert_preset="laserdisc", stream_port=80, stream_mode="rtsp")
    bad.clamp()
    assert (bad.convert_preset, bad.stream_port, bad.stream_mode) == ("mp4_h264", 1024, "copy")
