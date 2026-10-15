"""Offscreen smoke tests for the Qt UI using the fake backend."""

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from vlclone.core.backend import PlaybackState  # noqa: E402
from vlclone.core.controller import PlayerController  # noqa: E402
from vlclone.core.fake_backend import FakeBackend  # noqa: E402
from vlclone.core.settings import Settings  # noqa: E402


@pytest.fixture(scope="module")
def app():
    from vlclone.ui.theme import apply_theme

    application = QApplication.instance() or QApplication([])
    apply_theme(application)
    return application


@pytest.fixture
def window(app, tmp_path):
    from vlclone.ui.main_window import MainWindow

    settings = Settings(snapshot_dir=str(tmp_path))
    ctl = PlayerController(FakeBackend(), settings)
    win = MainWindow(ctl, settings)
    win.show()
    app.processEvents()
    yield win
    win.close()
    app.processEvents()


def test_icons_render(app):
    from vlclone.ui.icons import icon, names

    for name in names():
        pm = icon(name).pixmap(32, 32)
        assert not pm.isNull()


def test_open_and_play_updates_ui(window, app):
    ctl = window.ctl
    ctl.open(["http://x/clip.mp4", "http://x/b.mp4"])
    window._tick()
    assert ctl.state is PlaybackState.PLAYING
    assert "clip.mp4" in window.windowTitle()
    assert window.sidebar.model.rowCount() == 2
    ctl.backend.advance(5000)
    window._tick()
    assert window.overlay.controls.time_label.text() == "0:05"


def test_actions_trigger_controller(window):
    ctl = window.ctl
    ctl.open(["http://x/clip.mp4"])
    window._tick()
    window.actions.trigger("play_pause")
    assert ctl.backend.state() is PlaybackState.PAUSED
    window.actions.trigger("volume_up")
    assert ctl.volume == 105
    window.actions.trigger("faster")
    assert ctl.rate == 1.25
    window.actions.trigger("cycle_repeat")
    assert ctl.playlist.repeat.value == "all"
    window.actions.trigger("ab_loop")
    assert ctl.ab.a is not None


def test_shortcuts_registered(window):
    keys = window.actions.get("play_pause").shortcuts()
    assert any(k.toString() == "Space" for k in keys)
    assert window.actions.get("fullscreen").shortcutContext() == Qt.ApplicationShortcut


def test_quick_settings_and_menu_build(window, app):
    ctl = window.ctl
    ctl.open(["http://x/clip.mp4"])
    window._tick()
    window.open_quick_settings()
    app.processEvents()
    assert window.quick.isVisible()
    for key in list(window.quick.pages):
        window.quick.show_page(key)
    window.quick.hide()
    menu = window.build_menu()
    assert menu.actions()


def test_playlist_toggle_and_drop(window, app):
    window.toggle_playlist()
    window._sidebar_anim.setCurrentTime(window._sidebar_anim.duration())
    app.processEvents()
    assert window.settings.playlist_visible is False
    window.open_inputs(["http://x/dropped.mp4"])
    assert window.ctl.playlist[0].title == "dropped.mp4"


def test_playlist_model_move(window):
    window.ctl.open(["http://x/a.mp4", "http://x/b.mp4", "http://x/c.mp4"], play=False)
    model = window.sidebar.model
    mime = model.mimeData([model.index(0)])
    model.dropMimeData(mime, Qt.MoveAction, 3, 0, model.index(-1))
    assert [it.title for it in window.ctl.playlist] == ["b.mp4", "c.mp4", "a.mp4"]


def test_every_default_shortcut_has_an_action(window):
    from vlclone.core.shortcuts import DEFAULT_SHORTCUTS

    missing = [a for a in DEFAULT_SHORTCUTS if a not in window.actions.actions]
    assert missing == []


def test_media_info_dialog(window, app):
    window.ctl.open(["http://x/clip.mp4"])
    window._tick()
    window.show_media_info()
    app.processEvents()
    dlg = window._media_info
    assert dlg.isVisible()
    texts = [w.text() for w in dlg.findChildren(type(dlg.findChild(type(window.overlay.title))))]
    assert any("1920 × 1080" in t for t in texts)
    dlg.close()


def test_settings_dialog_applies(window, app):
    from vlclone.ui.settings_dialog import SettingsDialog

    dlg = SettingsDialog(window.settings, window.apply_settings, window)
    dlg.shortcuts.editors["play_pause"].setKeySequence("K")
    for field, getter, setter in dlg._bindings:
        if field == "controls_hide_delay_ms":
            setter(5000)
        if field == "max_volume":
            setter(125)
    dlg.accept()
    assert window.settings.controls_hide_delay_ms == 5000
    assert window.overlay.hide_delay_ms == 5000
    assert window.settings.shortcuts["play_pause"] == ["K", "Media Play"]
    assert any(k.toString() == "K" for k in window.actions.get("play_pause").shortcuts())
    assert window.ctl.max_volume == 125


def test_default_key_strings_parse_in_qt(app):
    from PySide6.QtGui import QKeySequence

    from vlclone.core.shortcuts import resolve_shortcuts

    for action, keys in resolve_shortcuts().items():
        for key in keys:
            assert QKeySequence(key).toString() == key, (action, key)
