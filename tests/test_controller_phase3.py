"""Controller: chapters/titles, disc menus, playlist URLs, open errors, options, geometry persistence."""

import pytest

from vlclone.core.backend import Chapter, PlaybackState, TitleInfo
from vlclone.core.controller import PlayerController
from vlclone.core.effects import Effects, EffectsStore
from vlclone.core.fake_backend import FakeBackend
from vlclone.core.settings import Settings


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def ctl(clock):
    return PlayerController(FakeBackend(), Settings(), clock=clock)


CHAPTERS = [Chapter(0, "Intro", 0), Chapter(1, "Middle", 20_000), Chapter(2, "End", 40_000)]


def start(ctl, mrl, **kwargs):
    ctl.open([mrl], **kwargs)
    ctl.tick()
    return ctl


def test_chapters_are_read_and_announced(ctl):
    seen = []
    ctl.chapters_changed.connect(lambda ch, titles, cur: seen.append([c.title for c in ch]))
    ctl.backend.chapter_map["http://x/film.mkv"] = CHAPTERS
    start(ctl, "http://x/film.mkv")
    assert [c.title for c in ctl.chapters] == ["Intro", "Middle", "End"]
    assert seen[-1] == ["Intro", "Middle", "End"]
    assert ctl.current_chapter() == 0


def test_next_previous_chapter(ctl, clock):
    osd = []
    ctl.osd.connect(osd.append)
    ctl.backend.chapter_map["http://x/film.mkv"] = CHAPTERS
    start(ctl, "http://x/film.mkv")
    ctl.next_chapter()
    assert ctl.backend.time() == 20_000
    assert osd[-1] == "Chapter 2/3: Middle"
    ctl.next_chapter()
    ctl.next_chapter()  # already on the last one
    assert ctl.backend.time() == 40_000 and osd[-1] == "Last chapter"
    ctl.backend.advance(10_000)  # 10 s into "End": previous restarts it
    ctl.previous_chapter()
    assert ctl.backend.time() == 40_000
    ctl.previous_chapter()  # right at the start: go to the one before
    assert ctl.backend.time() == 20_000
    ctl.set_chapter(0)
    assert ctl.current_chapter() == 0


def test_no_chapters_is_harmless(ctl):
    start(ctl, "http://x/plain.mp4")
    ctl.next_chapter()
    ctl.previous_chapter()
    assert ctl.chapters == [] and ctl.backend.time() == 0


DVD = "dvd:///D:/"
DVD_TITLES = [TitleInfo(0, "Menu", -1, True), TitleInfo(1, "Feature", 5_400_000), TitleInfo(2, "Extras", 600_000)]


def test_disc_titles_menu_navigation(ctl):
    b = ctl.backend
    b.title_map[DVD] = DVD_TITLES
    b.chapter_map[DVD] = CHAPTERS
    start(ctl, DVD)
    assert ctl.in_menu and ctl.chapters == []  # menus have no chapters
    ctl.navigate("down")
    ctl.navigate("activate")  # fake: activating leaves the menu for the feature
    assert b.navigations == ["down", "activate"]
    assert not ctl.in_menu and ctl.current_title == 1
    assert [c.title for c in ctl.chapters] == ["Intro", "Middle", "End"]
    ctl.next_title()
    assert ctl.current_title == 2
    ctl.disc_menu()
    assert ctl.in_menu
    ctl.navigate("bogus")
    assert b.navigations == ["down", "activate"]


def test_last_chapter_moves_to_next_title(ctl):
    b = ctl.backend
    b.title_map[DVD] = [TitleInfo(0, "A", 1000), TitleInfo(1, "B", 1000)]
    b.chapter_map[DVD] = [Chapter(0, "Only", 0)]
    start(ctl, DVD)
    ctl.next_chapter()
    assert ctl.current_title == 1


def test_disc_menu_without_menu_title_uses_popup(ctl):
    start(ctl, "bluray:///E:/")
    ctl.disc_menu()
    assert ctl.backend.navigations == ["popup"]


def test_playlist_url_expands_into_subitems(ctl):
    b = ctl.backend
    page = "https://www.youtube.com/watch?v=abc"
    b.subitem_map[page] = [("https://rr1.googlevideo.com/videoplayback?id=1", "A great video")]
    ctl.open([page], options=[":network-caching=3000"])
    ctl.tick()  # page "ended" with sub-items: replaced and started
    assert len(ctl.playlist) == 1
    item = ctl.playlist.current
    assert item.mrl.startswith("https://rr1.googlevideo.com") and item.title == "A great video"
    assert item.options == [":network-caching=3000"]
    ctl.tick()
    assert ctl.state is PlaybackState.PLAYING


def test_remote_playlist_expands_in_place(ctl):
    b = ctl.backend
    b.subitem_map["http://radio/list.m3u"] = [("http://radio/a.mp3", "A"), ("http://radio/b.mp3", "B")]
    ctl.open(["http://x/first.mp4", "http://radio/list.m3u", "http://x/last.mp4"], play=False)
    ctl.play_index(1)
    ctl.tick()
    assert [it.title for it in ctl.playlist] == ["first.mp4", "A", "B", "last.mp4"]
    assert ctl.playlist.current_index == 1


def test_failed_stream_reports_a_clear_error(ctl):
    errors, failed = [], []
    ctl.error.connect(errors.append)
    ctl.open_failed.connect(lambda item, msg: (failed.append(item.mrl), errors.append(msg)))
    ctl.backend.fail_mrls.add("rtsp://cam.local/stream")
    start(ctl, "rtsp://cam.local/stream")
    assert ctl.state is PlaybackState.STOPPED
    assert failed == ["rtsp://cam.local/stream"]
    assert "cam.local" in errors[-1] and "connection" in errors[-1]


def test_youtube_failure_mentions_the_lua_script(ctl):
    errors = []
    ctl.open_failed.connect(lambda item, msg: errors.append(msg))
    ctl.backend.fail_mrls.add("https://youtu.be/xyz")
    start(ctl, "https://youtu.be/xyz")
    assert "youtube.lua" in errors[-1]


def test_hanging_stream_times_out(ctl, clock):
    failed = []
    ctl.open_failed.connect(lambda item, msg: failed.append(msg))
    ctl.settings.network_timeout_s = 15
    ctl.backend.hang_mrls.add("http://slow.example/live.m3u8")
    start(ctl, "http://slow.example/live.m3u8")
    clock.t += 10
    ctl.tick()
    assert ctl.state is PlaybackState.OPENING and not failed
    clock.t += 6
    ctl.tick()
    assert ctl.state is PlaybackState.STOPPED
    assert failed and "15 s" in failed[0]
    assert ctl.backend.state() is PlaybackState.STOPPED


def test_local_files_never_time_out(ctl, clock):
    ctl.backend.hang_mrls.add("file:///slow/disk.mkv")
    start(ctl, "file:///slow/disk.mkv")
    clock.t += 1000
    ctl.tick()
    assert ctl.state is PlaybackState.OPENING


def test_item_options_and_default_caching(ctl):
    b = ctl.backend
    start(ctl, "dshow://", options=[":dshow-vdev=Cam"], title="Capture: Cam")
    assert b.options == [":dshow-vdev=Cam"] and ctl.playlist.current.title == "Capture: Cam"
    start(ctl, "http://x/a.mp4")
    assert b.options == []  # VLC's default caching is not repeated
    ctl.settings.network_caching_ms = 5000
    start(ctl, "http://x/b.mp4")
    assert b.options == [":network-caching=5000"]
    start(ctl, "http://x/c.mp4", options=[":network-caching=200"])
    assert b.options == [":network-caching=200"]  # explicit value wins
    start(ctl, "file:///local.mkv")
    assert b.options == []


# ------------------------------------------------------------ geometry
def test_geometry_persists_globally(tmp_path):
    settings = Settings()
    ctl = PlayerController(FakeBackend(), settings)
    start(ctl, "file:///a.mkv")
    ctl.set_aspect_ratio("16:9")
    ctl.set_crop("4:3")
    ctl.set_zoom(2.0)
    ctl.set_deinterlace("yadif")
    ctl.set_rotation(90)
    assert settings.effects["geometry"] == {"aspect_ratio": "16:9", "crop": "4:3", "zoom": 2.0, "rotation": 90,
                                            "deinterlace": "yadif"}
    path = tmp_path / "settings.json"
    settings.save(path)
    again = PlayerController(FakeBackend(), Settings.load(path))
    v = again.video
    assert (v.aspect_ratio, v.crop, v.zoom, v.rotation, v.deinterlace) == ("16:9", "4:3", 2.0, 90, "yadif")
    start(again, "file:///b.mkv")
    b = again.backend
    assert (b.aspect_ratio, b.crop, b.zoom, b.deinterlace, b.rotation) == ("16:9", "4:3", 2.0, "yadif", 90)


def test_geometry_per_file(tmp_path):
    store = EffectsStore(tmp_path / "fx.json")
    ctl = PlayerController(FakeBackend(), Settings(effects_scope="per_file"), effects_store=store)
    seen = []
    ctl.video_changed.connect(lambda v: seen.append(v.aspect_ratio))
    ctl.open(["file:///a.mkv", "file:///b.mkv"])
    ctl.tick()
    ctl.set_aspect_ratio("4:3")
    ctl.set_rotation(180)
    ctl.next()
    ctl.tick()
    assert ctl.video.aspect_ratio is None and ctl.video.rotation == 0  # b starts from the global set
    assert ctl.backend.aspect_ratio is None and ctl.backend.rotation == 0
    ctl.set_zoom(0.5)
    ctl.previous()
    ctl.tick()
    assert ctl.video.aspect_ratio == "4:3" and ctl.backend.rotation == 180
    assert ctl.backend.zoom == 0.0
    assert seen[-1] == "4:3"
    ctl.shutdown()
    reloaded = EffectsStore(tmp_path / "fx.json")
    assert reloaded.get("file:///a.mkv").geometry.aspect_ratio == "4:3"
    assert reloaded.get("file:///b.mkv").geometry.zoom == 0.5


def test_reset_all_effects_keeps_geometry_and_reset_video_clears_it(ctl):
    start(ctl, "file:///a.mkv")
    ctl.set_crop("16:9")
    ctl.set_flip(horizontal=True)
    ctl.reset_all_effects()
    assert ctl.video.crop == "16:9" and ctl.effects.geometry.crop == "16:9"
    ctl.reset_video()
    assert ctl.video.crop is None and ctl.settings.effects["geometry"]["crop"] is None


def test_geometry_validation():
    fx = Effects.from_dict({"geometry": {"aspect_ratio": "7:3", "crop": 5, "zoom": 3.3, "rotation": True,
                                         "deinterlace": "yadif2x"}})
    g = fx.geometry
    assert (g.aspect_ratio, g.crop, g.zoom, g.rotation, g.deinterlace) == (None, None, 0.0, 0, "yadif2x")
    assert Effects.from_dict({"audio": {}}).geometry.is_default  # phase-2 files load fine
