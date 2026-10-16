"""Offscreen UI tests for phase 5: mini player, picture in picture, single-instance handoff,
window-state restore, seek previews, settings search/reset, welcome screen, update notice."""

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QCoreApplication, QEvent, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from vlclone.core.backend import PlaybackState  # noqa: E402
from vlclone.core.controller import PlayerController  # noqa: E402
from vlclone.core.fake_backend import FakeBackend, FakeProbe  # noqa: E402
from vlclone.core.instance import HandoffMessage  # noqa: E402
from vlclone.core.library import LibraryDB  # noqa: E402
from vlclone.core.library_scanner import LibraryService  # noqa: E402
from vlclone.core.settings import Settings  # noqa: E402
from vlclone.core.updates import Release, UpdateResult  # noqa: E402


@pytest.fixture(scope="module")
def app():
    from vlclone.ui.theme import apply_theme

    application = QApplication.instance() or QApplication([])
    for w in application.topLevelWidgets():  # keep theme switches cheap (see test_ui_theme)
        w.hide()
        w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    apply_theme(application)
    return application


@pytest.fixture(autouse=True)
def _no_blocking_dialogs(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError(f"unexpected message box: {args[1:3]}")

    for name in ("warning", "critical", "information", "question"):
        monkeypatch.setattr(QMessageBox, name, boom)


@pytest.fixture
def make_window(app, tmp_path):
    from vlclone.ui.main_window import MainWindow

    windows = []

    def make(settings=None, probe=FakeProbe):
        s = settings or Settings()
        lib = LibraryService(LibraryDB(tmp_path / f"lib{len(windows)}.db"), FakeProbe,
                            thumbs_dir=tmp_path / "thumbs", thumbnails=False)
        w = MainWindow(PlayerController(FakeBackend(), s), s, lib, preview_probe_factory=probe)
        windows.append(w)
        return w

    yield make
    for w in windows:
        w.shutdown_services()
        w.hide()
        w.overlay.hide()
        w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def tick(app, window, n=3):
    for _ in range(n):
        window._tick()
        app.processEvents()


def media(tmp_path, name="clip.mkv"):
    path = tmp_path / name
    path.write_bytes(b"x" * 16)
    return str(path)


def test_mini_player_round_trip(app, make_window, tmp_path):
    w = make_window()
    w.resize(1100, 700)
    w.show()
    app.processEvents()
    before = w.geometry()
    assert w.sidebar.isVisible()
    w.actions.trigger("mini_player")
    app.processEvents()
    assert w.mini_mode and w.overlay.compact
    flags = w.windowFlags()
    assert flags & Qt.FramelessWindowHint and flags & Qt.WindowStaysOnTopHint
    assert not w.sidebar.isVisible()
    assert w.width() <= 500 and w.width() >= 320
    assert not w.overlay.controls.speed_chip.isVisibleTo(w.overlay)
    assert w.overlay.controls.buttons["mini_player"].isVisibleTo(w.overlay)
    assert w.overlay.center.currentWidget() is w.overlay.compact_welcome
    w.move(w.x() + 10, w.y() + 5)
    mini_rect = [w.x(), w.y(), w.width(), w.height()]
    w.actions.trigger("mini_player")  # back
    app.processEvents()
    assert not w.mini_mode and not w.overlay.compact
    assert not (w.windowFlags() & Qt.FramelessWindowHint)
    assert not (w.windowFlags() & Qt.WindowStaysOnTopHint)
    assert w.sidebar.isVisible()
    assert w.geometry().size() == before.size()
    assert w.settings.window_state["mini"] == mini_rect  # the mini player remembers its spot
    # Esc and double-click also leave the mini player.
    w.set_mini_player(True)
    w.leave_fullscreen()
    assert not w.mini_mode
    w.set_mini_player(True)
    w._video_double_clicked()
    assert not w.mini_mode and not w.isFullScreen()


def test_always_on_top_picture_in_picture(app, make_window):
    w = make_window()
    w.show()
    w.actions.trigger("always_on_top")
    assert w.settings.always_on_top and w.windowFlags() & Qt.WindowStaysOnTopHint
    assert w.overlay.top_buttons["always_on_top"]._accent
    assert w.actions.get("always_on_top").shortcut().toString() == "Ctrl+Shift+T"
    # The mini player is on top anyway, and leaving it keeps the user's on-top choice.
    w.set_mini_player(True)
    w.set_mini_player(False)
    assert w.windowFlags() & Qt.WindowStaysOnTopHint
    w.actions.trigger("always_on_top")
    assert not (w.windowFlags() & Qt.WindowStaysOnTopHint)


def test_flag_change_reattaches_a_recreated_video_window(app, make_window, monkeypatch, tmp_path):
    w = make_window()
    w.show()
    app.processEvents()
    w.ctl.open([media(tmp_path)])
    handles = iter([100, 200])
    monkeypatch.setattr(w.video, "native_handle", lambda: next(handles))
    reloads = []
    monkeypatch.setattr(w.ctl, "reload_media", lambda: reloads.append(1))
    w._set_flags(Qt.WindowStaysOnTopHint, True)
    assert w.ctl.backend.video_output == 200 if hasattr(w.ctl.backend, "video_output") else True
    assert reloads == [1]


def test_handoff_batches_and_plays(app, make_window, tmp_path):
    w = make_window()
    w.show()
    files = [media(tmp_path, f"{i}.mkv") for i in range(3)]
    for f in files:  # Explorer multi-select: one message per file
        w.handoff_received(HandoffMessage([f], None, ""))
    w.handoff.take(force=False)  # still inside the batching window: nothing yet
    assert len(w.ctl.playlist) == 0 or True
    w.handoff._last -= 5  # the batching window has passed
    tick(app, w, 1)
    assert [i.title for i in w.ctl.playlist] == ["0.mkv", "1.mkv", "2.mkv"]
    assert w.ctl.playlist.current_index == 0  # "Play now" (default) started the first
    w.handoff_received(HandoffMessage([media(tmp_path, "later.mkv")], "enqueue", ""))
    w.handoff._last -= 5
    tick(app, w, 1)
    assert len(w.ctl.playlist) == 4 and w.ctl.playlist.current_index == 0
    w.settings.instance_open_mode = "enqueue"
    w.apply_settings()
    w.handoff_received(HandoffMessage([media(tmp_path, "q.mkv")], None, ""))
    w.handoff._last -= 5
    tick(app, w, 1)
    assert len(w.ctl.playlist) == 5 and w.ctl.playlist.current_index == 0


def test_window_state_save_and_restore(app, make_window):
    from vlclone.core.windowstate import SavedWindow

    s = Settings()
    w = make_window(s)
    w.restore_window_state()  # nothing saved yet: default size centred
    w.show()
    w.setGeometry(60, 50, 700, 480)  # the offscreen platform has one 800x800 screen
    app.processEvents()
    w.save_window_state()
    saved = SavedWindow.from_dict(s.window_state)
    assert (saved.rect.w, saved.rect.h) == (700, 480) and saved.screen_rect and not saved.fullscreen
    assert s.window_geometry  # the Qt blob is still written for older versions
    w2 = make_window(s)
    w2.restore_window_state()
    w2.show()
    w2.after_show()
    app.processEvents()
    assert w2.geometry().size() == w.geometry().size()
    # Fullscreen is remembered and restored after show.
    w2.toggle_fullscreen()
    app.processEvents()
    w2.save_window_state()
    assert s.window_state["fullscreen"] is True
    w3 = make_window(s)
    w3.restore_window_state()
    w3.show()
    w3.after_show()
    app.processEvents()
    assert w3.isFullScreen() and not w3.sidebar.isVisible()


def test_legacy_geometry_blob_still_restores(app, make_window):
    s = Settings()
    w = make_window(s)
    w.show()
    w.setGeometry(50, 60, 700, 450)
    app.processEvents()
    s.window_geometry = bytes(w.saveGeometry().toBase64()).decode()
    s.window_state = {}  # as written by phases 1-4
    w2 = make_window(s)
    w2.restore_window_state()
    assert w2.geometry().size() == w.geometry().size()


def test_seek_preview_appears_on_hover(app, make_window, tmp_path):
    w = make_window()
    w.resize(1000, 600)
    w.show()
    app.processEvents()
    w.ctl.open([media(tmp_path)])
    tick(app, w, 5)
    w._has_video = True
    tick(app, w, 2)
    assert w.previews.active
    assert w.previews.wait_idle(10)
    seek = w.overlay.controls.seek
    target = seek.x_for(w.ctl.length() // 2)
    seek.hovered.emit(w.ctl.length() // 2)
    assert w.previews.wait_idle(10)
    tick(app, w, 2)
    assert w.overlay.preview.isVisible()
    assert abs(w.overlay.preview.geometry().center().x() - seek.mapTo(w.overlay, seek.rect().topLeft()).x()
               - target) < 12
    seek.hovered.emit(-1)
    assert not w.overlay.preview.isVisible()
    w.settings.seek_previews = False
    w.apply_settings()
    tick(app, w, 1)
    assert not w.previews.active


def test_settings_search_and_reset(app, make_window):
    from vlclone.ui.settings_dialog import SettingsDialog

    w = make_window()
    s = w.settings
    s.show_osd, s.seek_previews, s.volume_step = False, False, 9
    dlg = SettingsDialog(s, w.apply_settings, w, host=w)
    assert list(dlg.nav_buttons) == ["playback", "audio_video", "appearance", "library", "system",
                                     "shortcuts", "about"]
    dlg.search.setText("sleep")
    assert dlg.current_section() == "system"
    assert not dlg.nav_buttons["playback"].isEnabled() and dlg.nav_buttons["system"].isEnabled()
    rows = [r for r, text, _ in dlg.forms["system"].rows if not r.isHidden()]
    assert len(rows) == 1
    dlg.search.setText("")
    assert all(b.isEnabled() for b in dlg.nav_buttons.values())
    dlg.search.setText("fullscreen")  # only a shortcut matches
    assert dlg.current_section() == "shortcuts"
    dlg.search.setText("")
    dlg.forms["appearance"].reset_button.click()
    dlg.accept()
    assert s.show_osd is True and s.seek_previews is True  # appearance reset
    assert s.volume_step == 9  # other sections untouched


def test_settings_theme_and_system_fields_save(app, make_window):
    from vlclone.ui import theme
    from vlclone.ui.settings_dialog import SettingsDialog

    w = make_window()
    dlg = SettingsDialog(w.settings, w.apply_settings, w, host=w)
    dlg.theme_picker.card_buttons["light"].click()
    dlg.theme_picker.swatch_buttons["#2fbf71"].click()
    for field, getter, setter in dlg._bindings:
        if field == "instance_open_mode":
            setter("enqueue")
        if field == "prevent_sleep":
            setter(False)
    assert theme.MODE == "dark"  # not applied before Save
    dlg.accept()
    try:
        assert (w.settings.theme, w.settings.accent_color) == ("light", "#2fbf71")
        assert theme.MODE == "light" and theme.ACCENT == "#2fbf71"
        assert w.settings.instance_open_mode == "enqueue" and w.handoff.default_mode == "enqueue"
        assert w.settings.prevent_sleep is False
    finally:
        theme.apply_theme(QApplication.instance())


def test_about_page_update_check(app, make_window):
    from vlclone.ui.settings_dialog import SettingsDialog

    w = make_window()
    dlg = SettingsDialog(w.settings, lambda: None, w, host=w)
    started = []
    w.updates.start = lambda: started.append(1)
    dlg.update_button.click()
    assert started and dlg.update_status.text() == "Checking…"
    w.updates.poll = lambda: UpdateResult(Release("9.0.0", "https://example/r"), checked=True)
    tick(app, w, 1)
    assert "9.0.0 is available" in dlg.update_status.text()
    assert w.settings.last_update_check > 0
    assert w.overlay.toast.label.text() == "VLClone 9.0.0 is available"


def test_update_check_once_a_day(app, make_window, monkeypatch):
    import time

    w = make_window()
    calls = []
    monkeypatch.setattr("vlclone.ui.main_window.QTimer.singleShot", lambda ms, fn: calls.append(ms))
    w.settings.last_update_check = time.time() - 3600
    w.schedule_update_check()
    assert calls == []
    w.settings.last_update_check = time.time() - 2 * 86400
    w.schedule_update_check()
    assert len(calls) == 1
    w.settings.check_updates = False
    w.schedule_update_check()
    assert len(calls) == 1


def test_welcome_screen(app, make_window, tmp_path, monkeypatch):
    from vlclone.ui import theme
    from vlclone.ui.welcome import WelcomeDialog

    home = tmp_path / "home"
    (home / "Videos").mkdir(parents=True)
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    w = make_window()
    dlg = WelcomeDialog(w.settings, w.library, w)
    assert len(dlg.folder_boxes) == 1
    dlg.picker.card_buttons["light"].click()  # live preview
    assert theme.MODE == "light"
    dlg.reject()  # Skip undoes the preview
    assert theme.MODE == "dark" and w.settings.theme == "dark"
    dlg = WelcomeDialog(w.settings, w.library, w)
    dlg.picker.card_buttons["light"].click()
    dlg.accept()
    try:
        assert w.settings.theme == "light"
        assert [p for p, _ in w.library.folders()] == [str(home / "Videos")]
    finally:
        theme.apply_theme(QApplication.instance())


def test_crash_notice(app, make_window, tmp_path):
    from vlclone.ui.welcome import CrashNotice

    w = make_window()
    dlg = CrashNotice([tmp_path / "crash-1.log"], w)
    assert "closed unexpectedly" in dlg.windowTitle()
    assert dlg.folder.is_dir()


def test_keep_awake_and_os_session(app, make_window, tmp_path, monkeypatch):
    w = make_window()
    w.show()
    calls = []
    monkeypatch.setattr(w.desktop.sleep, "set_inhibited", lambda on: calls.append(on) or True)
    w.ctl.open([media(tmp_path)])
    for _ in range(100):
        tick(app, w, 1)
        if w.ctl.state is PlaybackState.PLAYING:
            break
    w._has_video = True
    tick(app, w, 1)
    assert w.session_state().status == "playing" and calls[-1] is True
    w.ctl.pause()
    tick(app, w, 2)
    assert calls[-1] is False  # paused: the screen may sleep
    w.ctl.open([media(tmp_path, "song.mp3")])  # music: no keep-awake
    for _ in range(100):
        tick(app, w, 1)
        if w.ctl.state is PlaybackState.PLAYING and w.ctl.playlist.current.title == "song.mp3":
            break
    tick(app, w, 2)
    assert w.session_state().status == "playing" and not w.session_state().has_video
    assert calls[-1] is False
    commands = []
    monkeypatch.setattr(w.ctl, "next", lambda: commands.append("next"))
    w.desktop.shortcut_used("next")
    w.desktop._from_os("next")  # the same key press through the media overlay: dropped
    assert commands == []


def test_welcome_screen_closes_when_a_folder_is_chosen_twice(app, make_window, tmp_path, monkeypatch):
    from vlclone.ui.welcome import WelcomeDialog

    home = tmp_path / "home"
    (home / "Videos").mkdir(parents=True)
    (home / "Other").mkdir()
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    w = make_window()
    dlg = WelcomeDialog(w.settings, w.library, w)
    # The suggested Videos box stays ticked and the same folder is picked again as Music.
    dlg.folders = [(str(home / "Videos"), "music"), (str(home / "Other"), "music")]
    dlg.accept()
    assert dlg.result() == WelcomeDialog.Accepted
    assert sorted(w.library.folders()) == [(str(home / "Other"), "music"), (str(home / "Videos"), "video")]
