"""Offscreen UI tests for phase 3: Open Media dialog, chapters, disc menus, errors, media library."""

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402

from vlclone.core.backend import Chapter, PlaybackState, TitleInfo  # noqa: E402
from vlclone.core.controller import PlayerController  # noqa: E402
from vlclone.core.fake_backend import FakeBackend, FakeProbe  # noqa: E402
from vlclone.core.library import LibraryDB  # noqa: E402
from vlclone.core.library_scanner import LibraryService  # noqa: E402
from vlclone.core.media_sources import path_to_mrl  # noqa: E402
from vlclone.core.resume import ResumeStore  # noqa: E402
from vlclone.core.settings import Settings  # noqa: E402


@pytest.fixture(scope="module")
def app():
    from vlclone.ui.theme import apply_theme

    application = QApplication.instance() or QApplication([])
    apply_theme(application)
    return application


@pytest.fixture(autouse=True)
def _no_blocking_dialogs(monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    def boom(*args, **kwargs):
        raise AssertionError(f"unexpected message box: {args[1:3]}")

    for name in ("warning", "critical", "information", "question"):
        monkeypatch.setattr(QMessageBox, name, boom)


@pytest.fixture
def library(tmp_path):
    svc = LibraryService(LibraryDB(tmp_path / "lib.db"), FakeProbe, thumbs_dir=tmp_path / "thumbs", watch=False)
    yield svc
    svc.shutdown()


@pytest.fixture
def window(app, tmp_path, library):
    from vlclone.core.effects import EffectsStore, EqPresetStore
    from vlclone.ui.main_window import MainWindow

    settings = Settings(snapshot_dir=str(tmp_path))
    ctl = PlayerController(FakeBackend(), settings, ResumeStore(tmp_path / "resume.json", 1000),
                           effects_store=EffectsStore(tmp_path / "fx.json"),
                           eq_presets=EqPresetStore(tmp_path / "eq.json"))
    win = MainWindow(ctl, settings, library)
    win.show()
    app.processEvents()
    yield win
    win.close()
    app.processEvents()


CHAPTERS = [Chapter(0, "Opening", 0), Chapter(1, "The Heist", 20_000), Chapter(2, "Credits", 50_000)]


# ------------------------------------------------------------- shortcuts
def test_phase3_shortcuts_registered_without_conflicts(window):
    from vlclone.core.shortcuts import DEFAULT_SHORTCUTS, find_conflicts, resolve_shortcuts

    expected = {"open_media": "Ctrl+Shift+O", "open_disc": "Ctrl+D", "open_capture": "Ctrl+C",
                "media_library": "Ctrl+Shift+L", "next_chapter": "Shift+N", "previous_chapter": "Shift+P",
                "next_title": "Shift+B", "previous_title": "Shift+O", "disc_menu": "Shift+M"}
    for action, key in expected.items():
        assert window.actions.get(action).shortcuts()[0].toString() == key
        assert action in DEFAULT_SHORTCUTS  # listed in the shortcut editor
    assert find_conflicts(resolve_shortcuts()) == {}


def test_shortcut_editor_lists_new_actions(window, app):
    from vlclone.ui.settings_dialog import SettingsDialog

    dlg = SettingsDialog(window.settings, lambda: None, window)
    assert "next_chapter" in dlg.shortcuts.editors and "open_capture" in dlg.shortcuts.editors
    assert dlg.shortcuts.warning.text() == ""
    dlg.close()


# -------------------------------------------------------------- chapters
def test_chapter_marks_chip_and_page(window, app):
    ctl = window.ctl
    ctl.backend.chapter_map["http://x/film.mkv"] = CHAPTERS
    ctl.open(["http://x/film.mkv"])
    window._tick()
    seek = window.overlay.controls.seek
    assert seek._chapters == [(0, "Opening"), (20_000, "The Heist"), (50_000, "Credits")]
    assert seek.chapter_at(25_000) == "The Heist"
    chip = window.overlay.controls.chapter_chip
    assert not chip.isHidden() and chip.text() == "Ch 1/3"
    window.actions.trigger("next_chapter")
    window._tick()
    assert ctl.backend.time() == 20_000 and chip.text() == "Ch 2/3"
    window.actions.trigger("quick_settings_chapters")
    app.processEvents()
    page = window.quick.pages["chapters"]
    assert window.quick.isVisible() and window.quick.stack.currentWidget() is page
    assert len(page.chapters.buttons) == 3 and page.chapters.buttons[1].isChecked()
    page.chapters.buttons[2].click()
    assert ctl.backend.time() == 50_000
    assert not page.disc_pad.isVisibleTo(page)  # plain file: no disc controls
    window.quick.hide()
    menu = window.build_menu()
    playback = next(a.menu() for a in menu.actions() if a.text() == "Playback")
    chapter_menu = next(a.menu() for a in playback.actions() if a.text() == "Chapter")
    assert [a.text().split("   ")[0] for a in chapter_menu.actions()] == ["1. Opening", "2. The Heist", "3. Credits"]


def test_files_without_chapters_hide_the_chip(window):
    window.ctl.open(["http://x/plain.mp4"])
    window._tick()
    assert window.overlay.controls.chapter_chip.isHidden()
    assert window.overlay.controls.seek._chapters == []


def test_disc_menu_keys_navigate_only_in_menus(window, app):
    ctl = window.ctl
    b = ctl.backend
    dvd = "dvd:///D:/"
    b.title_map[dvd] = [TitleInfo(0, "Menu", -1, True), TitleInfo(1, "Feature", 5_400_000)]
    b.chapter_map[dvd] = CHAPTERS
    ctl.open([dvd])
    window._tick()
    assert ctl.in_menu and window.actions.get("nav_up").isEnabled()
    assert window.overlay.controls.chapter_chip.text() == "Disc menu"
    window.actions.get("nav_down").trigger()
    window.actions.get("jump_fwd_medium").trigger()  # → in a menu navigates instead of seeking
    assert b.navigations == ["down", "right"] and b.time() == 0
    window.actions.get("nav_activate").trigger()
    window._tick()
    assert not ctl.in_menu and not window.actions.get("nav_up").isEnabled()
    window.actions.get("jump_fwd_medium").trigger()
    assert b.time() == 10_000  # back to seeking
    window.open_quick_settings("chapters")
    app.processEvents()
    page = window.quick.pages["chapters"]
    assert page.disc_pad.isVisibleTo(page) and page.titles.isVisibleTo(page)
    window.quick.hide()


# ------------------------------------------------------------------ errors
def test_failed_stream_shows_toast_with_retry(window, app):
    ctl = window.ctl
    ctl.backend.fail_mrls.add("rtsp://cam.local/live")
    ctl.open(["rtsp://cam.local/live"])
    window._tick()
    toast = window.overlay.toast
    assert toast.isVisible() and "cam.local" in toast.label.text() and toast.button.text() == "Retry"
    loads = len(ctl.backend.loads)
    ctl.backend.fail_mrls.clear()
    toast.button.click()
    window._tick()
    assert len(ctl.backend.loads) == loads + 1 and ctl.state is PlaybackState.PLAYING


# ------------------------------------------------------------- open media
def test_open_media_network_tab(window, app, monkeypatch):
    from vlclone.ui.open_media import OpenMediaDialog

    dlg = OpenMediaDialog(window.ctl.backend, window.settings, "network", "", window)
    net = dlg.tabs["network"]
    net.edit.setText("example.com/x")
    assert "protocol" in net.kind.text()
    dlg._finish(play=True)
    assert "protocol" in dlg.error.text() and dlg.request is None
    net.edit.setText("https://cdn.example.com/live/master.m3u8")
    assert net.kind.text() == "Detected: HLS stream"
    net.caching.setValue(3000)
    dlg.start_at.setText("1:30")
    dlg._finish(play=True)
    r = dlg.request
    assert r.inputs == ["https://cdn.example.com/live/master.m3u8"]
    assert r.options == [":network-caching=3000", ":start-time=90"]
    assert window.settings.recent_urls[0] == "https://cdn.example.com/live/master.m3u8"
    dlg2 = OpenMediaDialog(window.ctl.backend, window.settings, "network", "", window)
    assert dlg2.tabs["network"].recent.count() == 1
    dlg2.tabs["network"]._clear_history()
    assert window.settings.recent_urls == []


def test_open_media_dialog_feeds_the_controller(window, app, monkeypatch):
    from vlclone.ui import open_media

    def fake_exec(self):
        self.tabs["network"].edit.setText("https://youtu.be/abc")
        self._finish(play=True)
        return True

    monkeypatch.setattr(open_media.OpenMediaDialog, "exec", fake_exec)
    window.actions.trigger("open_url")
    ctl = window.ctl
    assert ctl.playlist.current.mrl == "https://youtu.be/abc"
    assert ctl.backend.mrl == "https://youtu.be/abc"


def test_open_media_file_tab_with_subtitle(window, tmp_path):
    from vlclone.ui.open_media import OpenMediaDialog

    video = tmp_path / "a.mkv"
    video.write_bytes(b"x")
    sub = tmp_path / "a.srt"
    sub.write_text("1\n00:00:01,000 --> 00:00:02,000\nhi\n")
    dlg = OpenMediaDialog(window.ctl.backend, window.settings, "file", str(tmp_path), window)
    tab = dlg.tabs["file"]
    with pytest.raises(ValueError):
        dlg.build_request()
    tab.add_paths([str(video), str(video)])
    assert tab.list.count() == 1
    tab.use_sub.setChecked(True)
    with pytest.raises(ValueError):
        dlg.build_request()
    tab.sub_edit.setText(str(sub))
    r = dlg.build_request(play=False)
    assert r.inputs == [str(video)] and r.options == [f":sub-file={sub}"] and r.play is False


def test_open_media_disc_tab(window, tmp_path, monkeypatch):
    from vlclone.ui import open_media

    monkeypatch.setattr(open_media, "list_optical_drives", lambda: [])
    folder = tmp_path / "BD_FOLDER"
    (folder / "BDMV").mkdir(parents=True)
    dlg = open_media.OpenMediaDialog(window.ctl.backend, window.settings, "disc", "", window)
    tab = dlg.tabs["disc"]
    assert "No optical drive" in tab.drive_hint.text()
    tab._use_path(str(folder / "BDMV"))
    assert tab.kind == "bluray" and tab.kind_buttons["bluray"].isChecked()
    tab.menus.setChecked(False)
    tab.title.setValue(2)
    r = dlg.build_request()
    assert r.inputs[0].startswith("bluray://") and r.inputs[0].endswith("BD_FOLDER#2")
    assert r.options == [":no-bluray-menu"] and "Blu-ray" in r.title
    tab.set_kind("cd")
    assert tab.track.isVisibleTo(tab) and not tab.title.isVisibleTo(tab)


def test_open_media_disc_tab_lists_drives(window, monkeypatch):
    from vlclone.core.discs import OpticalDrive
    from vlclone.ui import open_media

    monkeypatch.setattr(open_media, "list_optical_drives",
                        lambda: [OpticalDrive("D:\\", "", None), OpticalDrive("E:\\", "MOVIE", "dvd")])
    dlg = open_media.OpenMediaDialog(window.ctl.backend, window.settings, "disc", "", window)
    tab = dlg.tabs["disc"]
    assert tab.device.count() == 2 and tab.device.currentIndex() == 1  # the drive with a disc
    assert tab.location() == "E:\\" and tab.kind == "dvd"
    assert dlg.build_request().inputs == ["dvd:///E:/"]


def test_open_media_capture_tab(window):
    from vlclone.ui import open_media

    dlg = open_media.OpenMediaDialog(window.ctl.backend, window.settings, "capture", "", window)
    tab = dlg.tabs["capture"]
    tab.platform = "win32"
    assert [tab.video.itemText(i) for i in range(tab.video.count())] == ["Integrated Camera", "No video"]
    assert tab.audio.currentData() is None  # no audio unless chosen
    tab.audio.setCurrentIndex(1)  # Microphone Array
    tab.size.setCurrentIndex(4)  # 1280 × 720
    r = dlg.build_request()
    assert r.inputs == ["dshow://"]
    assert ":dshow-vdev=Integrated Camera" in r.options and ":dshow-size=1280x720" in r.options
    assert r.title == "Capture: Integrated Camera + Microphone Array"
    assert window.settings.capture_video_device == "Integrated Camera"


# ----------------------------------------------------------------- library
@pytest.fixture
def media_dir(tmp_path):
    videos = tmp_path / "Videos"
    music = tmp_path / "Music"
    for path in (videos / "Holiday.mp4", videos / "Trip" / "Day 1.mkv", music / "Band - Song One.mp3",
                 music / "Band - Song Two.mp3"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    return videos, music


def test_library_view_browse_and_play(window, app, library, media_dir):
    videos, music = media_dir
    assert library.add_folder(str(videos), "video") is None
    assert library.add_folder(str(music), "music") is None
    assert library.wait(10)
    window.actions.trigger("media_library")
    app.processEvents()
    view = window.library_view
    assert window.library_visible and not window.overlay.isVisible()
    view.poll(force=True)
    assert view.stack.currentWidget() is view.grid
    assert [i.title for i in view.grid_model.items] == ["Day 1", "Holiday"]
    assert all(i.thumb for i in view.grid_model.items)  # FakeProbe thumbnails
    assert view.count.text() == "2 videos"
    view.search.setText("holi")
    view.reload()
    assert [i.title for i in view.grid_model.items] == ["Holiday"]
    view.search.setText("")
    view.reload()
    # double-click plays and returns to the player for videos
    view.grid.doubleClicked.emit(view.grid_model.index(1))
    app.processEvents()
    assert window.ctl.playlist.current.title == "Holiday.mp4" and not window.library_visible
    window.toggle_library()
    view.show_section("music")
    tree = view.music
    assert tree.topLevelItemCount() == 1 and tree.topLevelItem(0).text(0) == "Band"
    album = tree.topLevelItem(0).child(0)
    assert album.text(0) == "Music" and album.childCount() == 2
    view.music.itemDoubleClicked.emit(album, 0)  # an album plays all its tracks, staying in the library
    assert [it.title for it in window.ctl.playlist][-2:] == ["Band - Song One.mp3", "Band - Song Two.mp3"]
    assert window.library_visible
    window._tick()
    assert not view.now_playing.isHidden() and "Band - Song One" in view.np_label.text()


def test_library_recent_continue_and_context_menu(window, app, library, media_dir, monkeypatch):
    videos, _ = media_dir
    library.add_folder(str(videos), "video")
    library.wait(10)
    ctl = window.ctl
    holiday = path_to_mrl(str(videos / "Holiday.mp4"))
    ctl.open([holiday])
    window._tick()
    ctl.backend.advance(30_000)
    window._tick()
    window.show_library()
    view = window.library_view
    view.show_section("recent")
    assert [i.title for i in view.list_model.items] == ["Holiday"]
    view.show_section("continue")
    items = view.grid_model.items
    assert [i.title for i in items] == ["Holiday"] and items[0].position_ms >= 30_000
    shown = []
    import vlclone.ui.library_view as lv

    monkeypatch.setattr(lv, "show_in_folder", shown.append)
    menu = view.build_item_menu(items)
    labels = [a.text() for a in menu.actions() if a.text()]
    assert labels == ["Play", "Add to playlist", "Convert / save…", "Show in folder"]
    next(a for a in menu.actions() if a.text() == "Show in folder").trigger()
    assert shown == [str(videos / "Holiday.mp4")]
    before = len(ctl.playlist)
    next(a for a in menu.actions() if a.text() == "Add to playlist").trigger()
    assert len(ctl.playlist) == before + 1 and window.library_visible
    view.show_section("recent")
    menu = view.build_item_menu(view.list_model.items)
    next(a for a in menu.actions() if a.text() == "Remove from history").trigger()
    view.poll()
    assert view.list_model.items == [] and view.stack.currentWidget() is view.empty


def test_library_empty_state_and_folders_dialog(window, app, library, media_dir):
    from vlclone.ui.library_view import LibraryFoldersDialog

    window.show_library()
    view = window.library_view
    view.show_section("videos")
    assert view.stack.currentWidget() is view.empty and view.empty_buttons.isVisibleTo(view.empty)
    videos, music = media_dir
    dlg = LibraryFoldersDialog(library, window.settings, window)
    dlg.add("video", str(videos))
    dlg.add("video", str(videos / "Trip"))
    assert "Already" in dlg.error.text() and dlg.list.count() == 1
    library.wait(10)
    view.poll()
    assert len(view.grid_model.items) == 2
    dlg.list.item(0).setSelected(True)
    dlg.remove_selected()
    assert dlg.list.count() == 0
    view.poll()
    assert view.stack.currentWidget() is view.empty
    dlg.close()


def test_settings_page_has_network_and_library_options(window, app):
    from vlclone.ui.settings_dialog import SettingsDialog

    dlg = SettingsDialog(window.settings, window.apply_settings, window)
    fields = {field for field, _, _ in dlg._bindings}
    assert {"network_caching_ms", "network_timeout_s", "library_watch", "library_thumbnails"} <= fields
    setter = next(set_ for field, _, set_ in dlg._bindings if field == "library_thumbnails")
    setter(False)
    dlg.accept()
    assert window.settings.library_thumbnails is False and window.library.thumbnails is False


def test_library_notify_message(window, app):
    view = window.library_view
    window._open_from_library(["http://x/a.mp4", "http://x/b.mp4"], False)
    assert view.message.text() == "Added 2 items to the playlist"


def test_play_history_recorded_for_streams(window, library):
    window.ctl.open(["https://example.com/live.m3u8"])
    window._tick()
    assert library.recent()[0].mrl == "https://example.com/live.m3u8"


def test_fullscreen_from_library_returns_to_player(window, app):
    window.show_library()
    window.toggle_fullscreen()
    assert not window.library_visible
    window.leave_fullscreen()


def test_menu_has_media_sources(window):
    menu = window.build_menu()
    media = next(a.menu() for a in menu.actions() if a.text() == "Media")
    labels = [a.text() for a in media.actions() if a.text()]
    for expected in ("Open file…", "Open disc…", "Open network stream…", "Open capture device…"):
        assert expected in labels
    assert any(a.text() == "Media library" for a in menu.actions())


def test_library_ago_helper():
    from vlclone.ui.library_view import ago

    assert ago(None) == "" and ago(100, 120) == "just now"
    assert ago(0 + 1, 7201) == "2 hours ago" and ago(1, 86401 * 3) == "3 days ago"
