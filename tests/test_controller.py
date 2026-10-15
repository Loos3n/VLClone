import pytest

from vlclone.core.abloop import ABState
from vlclone.core.backend import PlaybackState
from vlclone.core.controller import PlayerController
from vlclone.core.fake_backend import FakeBackend
from vlclone.core.playlist import RepeatMode
from vlclone.core.resume import ResumeStore
from vlclone.core.settings import Settings


@pytest.fixture
def ctl(tmp_path):
    backend = FakeBackend(default_length_ms=120_000)
    store = ResumeStore(tmp_path / "resume.json", min_position_ms=10_000)
    c = PlayerController(backend, Settings(snapshot_dir=str(tmp_path / "snaps")), store)
    return c


def run(ctl, ms, step=200):
    for _ in range(ms // step):
        ctl.backend.advance(step)
        ctl.tick()


def urls(n):
    return [f"http://x/{i}.mp4" for i in range(n)]


def test_open_starts_first_item(ctl):
    changed = []
    ctl.media_changed.connect(changed.append)
    assert ctl.open(urls(3)) == 3
    ctl.tick()
    assert ctl.state is PlaybackState.PLAYING
    assert ctl.backend.mrl == "http://x/0.mp4"
    assert changed[0].title == "0.mp4"


def test_open_without_play(ctl):
    ctl.open(urls(2), play=False)
    assert ctl.backend.mrl is None
    ctl.toggle_play()
    assert ctl.backend.mrl == "http://x/0.mp4"


def test_settings_applied_when_playback_starts(ctl):
    ctl.set_volume(150)
    ctl.set_rate(1.5)
    ctl.set_aspect_ratio("16:9")
    ctl.open(urls(1))
    ctl.backend._volume = 100  # libVLC forgets volume before audio starts
    ctl.tick()
    assert ctl.backend.volume() == 150
    assert ctl.backend.rate() == 1.5
    assert ctl.backend.aspect_ratio == "16:9"


def test_pause_resume_stop(ctl):
    ctl.open(urls(1))
    ctl.tick()
    ctl.toggle_play()
    assert ctl.backend.state() is PlaybackState.PAUSED
    ctl.toggle_play()
    assert ctl.backend.state() is PlaybackState.PLAYING
    ctl.stop()
    assert ctl.state is PlaybackState.STOPPED
    ctl.toggle_play()
    ctl.tick()
    assert ctl.state is PlaybackState.PLAYING


def test_auto_advance_and_stop_at_end(ctl):
    ctl.backend.default_length_ms = 1_000
    ctl.open(urls(2))
    run(ctl, 1_200)
    assert ctl.backend.mrl == "http://x/1.mp4"
    run(ctl, 1_200)
    assert ctl.state is PlaybackState.STOPPED


def test_repeat_one_and_all(ctl):
    ctl.backend.default_length_ms = 1_000
    ctl.open(urls(2))
    ctl.set_repeat(RepeatMode.ONE)
    run(ctl, 1_200)
    assert ctl.backend.loads == ["http://x/0.mp4", "http://x/0.mp4"]
    ctl.set_repeat(RepeatMode.ALL)
    run(ctl, 2_400)
    assert ctl.backend.loads[-1] == "http://x/0.mp4"
    assert ctl.state is PlaybackState.PLAYING
    assert ctl.settings.repeat == "all"


def test_next_previous(ctl):
    ctl.open(urls(3))
    ctl.next()
    ctl.next()
    assert ctl.backend.mrl == "http://x/2.mp4"
    ctl.next()  # end, no repeat: stays
    assert ctl.backend.mrl == "http://x/2.mp4"
    ctl.previous()
    assert ctl.backend.mrl == "http://x/1.mp4"


def test_seek_and_relative(ctl):
    ctl.open(urls(1))
    ctl.tick()
    ctl.seek(30_000)
    assert ctl.time() == 30_000
    ctl.seek_relative(-40_000)
    assert ctl.time() == 0
    ctl.seek(999_999)
    assert ctl.time() < 120_000
    ctl.seek_fraction(0.5)
    assert ctl.time() == 60_000


def test_volume_boost_and_mute(ctl):
    events = []
    ctl.volume_changed.connect(lambda v, m: events.append((v, m)))
    ctl.set_volume(250)
    assert ctl.volume == 200
    ctl.settings.max_volume = 125
    ctl.change_volume(+1)
    assert ctl.volume == 125
    ctl.toggle_mute()
    assert ctl.backend.muted is True and events[-1] == (125, True)
    ctl.change_volume(-1)  # changing volume unmutes, like VLC
    assert ctl.muted is False


def test_rate_steps(ctl):
    ctl.faster()
    assert ctl.rate == 1.25
    ctl.slower()
    ctl.slower()
    assert ctl.rate == 0.75
    ctl.normal_rate()
    assert ctl.backend.rate() == 1.0


def test_ab_loop_seeks_back(ctl):
    ctl.open(urls(1))
    ctl.tick()
    ctl.seek(10_000)
    assert ctl.toggle_ab() is ABState.A_SET
    run(ctl, 5_000)
    assert ctl.toggle_ab() is ABState.ACTIVE
    run(ctl, 5_200)
    assert 10_000 <= ctl.time() < 15_000


def test_ab_cleared_on_new_media(ctl):
    ctl.open(urls(2))
    ctl.tick()
    ctl.toggle_ab()
    ctl.next()
    assert ctl.ab.state is ABState.OFF


def test_resume_position(ctl):
    ctl.open(["http://x/movie.mp4"])
    run(ctl, 1_000)
    ctl.seek(50_000)
    ctl.tick()
    ctl.stop()
    assert ctl.resume.get("http://x/movie.mp4") == 50_000
    resumed = []
    ctl.resumed.connect(resumed.append)
    ctl.play_index(0)
    ctl.tick()
    assert resumed == [50_000]
    assert ctl.time() == 50_000


def test_resume_forgotten_at_end(ctl):
    ctl.backend.default_length_ms = 30_000
    ctl.open(["http://x/short.mp4"])
    run(ctl, 1_000)
    ctl.seek(20_000)
    ctl.pause()
    assert ctl.resume.get("http://x/short.mp4") == 20_000
    ctl.play()
    run(ctl, 12_000)  # plays to the end
    assert ctl.resume.get("http://x/short.mp4") is None


def test_resume_disabled(ctl):
    ctl.settings.resume_playback = False
    ctl.resume.update("http://x/a.mp4", 50_000, 120_000)
    ctl.open(["http://x/a.mp4"])
    ctl.tick()
    assert ctl.time() < 1_000


def test_tracks_and_delays(ctl):
    ctl.open(urls(1))
    ctl.tick()
    assert [t.id for t in ctl.audio_tracks()] == [-1, 1, 2]
    ctl.cycle_audio_track()
    assert ctl.audio_track() == 2
    ctl.cycle_subtitle()
    assert ctl.subtitle_track() == 3
    ctl.toggle_subtitles()
    assert ctl.subtitle_track() == -1
    ctl.toggle_subtitles()
    assert ctl.subtitle_track() == 3
    assert ctl.load_subtitle("/tmp/extra.srt")
    assert ctl.subtitle_track() == 4
    ctl.change_subtitle_delay(+1)
    ctl.change_subtitle_delay(+1)
    ctl.change_audio_delay(-1)
    assert ctl.backend.subtitle_delay == 100
    assert ctl.backend.audio_delay == -50


def test_delays_reset_for_new_media(ctl):
    ctl.open(urls(2))
    ctl.tick()
    ctl.set_subtitle_delay(500)
    ctl.next()
    assert ctl.subtitle_delay == 0


def test_video_cycles_and_rotation_reload(ctl):
    ctl.open(urls(1))
    run(ctl, 1_000)
    ctl.cycle_aspect_ratio()
    assert ctl.backend.aspect_ratio == "16:9"
    ctl.cycle_crop()
    ctl.cycle_zoom()
    ctl.cycle_deinterlace()
    assert ctl.backend.crop == "16:9" and ctl.backend.zoom == 0.25 and ctl.backend.deinterlace == "blend"
    ctl.seek(40_000)
    ctl.rotate()
    assert ctl.backend.rotation == 90
    ctl.tick()
    assert ctl.time() == 40_000  # reloaded and restored position
    assert ctl.backend.crop == "16:9"  # options re-applied after reload
    ctl.reset_video()
    assert ctl.backend.aspect_ratio is None and ctl.video.rotation == 0


def test_snapshot(ctl, tmp_path):
    ctl.open(urls(1))
    ctl.tick()
    path = ctl.snapshot()
    assert path and path.endswith(".png")
    assert ctl.backend.snapshots == [path]


def test_frame_step_pauses(ctl):
    ctl.open(urls(1))
    ctl.tick()
    ctl.frame_step()
    assert ctl.backend.state() is PlaybackState.PAUSED and ctl.backend.frames_stepped == 1


def test_error_skips_to_next(ctl):
    ctl.open(urls(3))
    errors = []
    ctl.error.connect(errors.append)
    ctl.backend._state = PlaybackState.ERROR
    ctl.tick()
    assert errors and ctl.backend.mrl == "http://x/1.mp4"


def test_open_playlist_file(ctl, tmp_path):
    m3u = tmp_path / "list.m3u"
    m3u.write_text("#EXTM3U\n#EXTINF:10,First\nhttp://x/a.mp4\nhttp://x/b.mp4\n")
    assert ctl.open([str(m3u)], play=False) == 2
    assert ctl.playlist[0].title == "First"


def test_shuffle_setting_persisted(ctl):
    ctl.toggle_shuffle()
    assert ctl.settings.shuffle is True and ctl.playlist.shuffle is True


def test_rotation_reload_keeps_subtitles_tracks_and_delays(ctl):
    ctl.open(urls(1))
    ctl.tick()
    ctl.load_subtitle("/tmp/extra.srt")
    ctl.set_audio_track(2)
    ctl.set_subtitle_delay(300)
    ctl.seek(10_000)
    ctl.toggle_ab()
    ctl.rotate()
    # the fake backend forgets slaves on reload, like libVLC
    ctl.backend._subs = ctl.backend._subs[:2]
    ctl.backend._audio = 1
    ctl.tick()
    assert ctl.audio_track() == 2
    assert any(t.name == "extra.srt" for t in ctl.subtitle_tracks())
    assert ctl.backend.subtitle_delay == 300
    assert ctl.ab.state is ABState.A_SET


def test_rotate_while_paused_stays_paused(ctl):
    ctl.open(urls(1))
    ctl.tick()
    ctl.pause()
    ctl.rotate()
    ctl.tick()
    assert ctl.backend.state() is PlaybackState.PAUSED
