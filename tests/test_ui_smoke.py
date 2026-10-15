"""Offscreen smoke tests for the Qt UI using the fake backend."""

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from vlclone.core.backend import PlaybackState  # noqa: E402
from vlclone.core.controller import PlayerController  # noqa: E402
from vlclone.core.fake_backend import FakeBackend  # noqa: E402
from vlclone.core.settings import Settings  # noqa: E402
from vlclone.core.shortcuts import DEFAULT_SHORTCUTS  # noqa: E402


@pytest.fixture(scope="module")
def app():
    from vlclone.ui.theme import apply_theme

    application = QApplication.instance() or QApplication([])
    apply_theme(application)
    return application


@pytest.fixture(autouse=True)
def _no_blocking_dialogs(monkeypatch):
    """A message box in an offscreen test would block forever: fail instead."""
    from PySide6.QtWidgets import QMessageBox

    def boom(*args, **kwargs):
        raise AssertionError(f"unexpected message box: {args[1:3]}")

    for name in ("warning", "critical", "information", "question"):
        monkeypatch.setattr(QMessageBox, name, boom)


@pytest.fixture
def window(app, tmp_path):
    from vlclone.ui.main_window import MainWindow

    from vlclone.core.effects import EffectsStore, EqPresetStore

    settings = Settings(snapshot_dir=str(tmp_path))
    ctl = PlayerController(FakeBackend(), settings, effects_store=EffectsStore(tmp_path / "fx.json"),
                           eq_presets=EqPresetStore(tmp_path / "eq.json"))
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
    assert window.settings.shortcuts["play_pause"] == ["K"] + DEFAULT_SHORTCUTS["play_pause"][1][1:]
    assert any(k.toString() == "K" for k in window.actions.get("play_pause").shortcuts())
    assert window.ctl.max_volume == 125


def test_default_key_strings_parse_in_qt(app):
    from PySide6.QtGui import QKeySequence

    from vlclone.core.shortcuts import resolve_shortcuts

    for action, keys in resolve_shortcuts().items():
        for key in keys:
            assert QKeySequence(key).toString() == key, (action, key)


# ------------------------------------------------------------------ phase 2
def test_effects_shortcut_registered_without_conflicts(window):
    from vlclone.core.shortcuts import find_conflicts, resolve_shortcuts

    assert [k.toString() for k in window.actions.get("effects").shortcuts()] == ["Ctrl+Shift+E"]
    assert find_conflicts(resolve_shortcuts()) == {}


def test_effects_panel_drives_controller(window, app, monkeypatch):
    ctl = window.ctl
    ctl.open(["http://x/clip.mp4"])
    window._tick()
    window.actions.trigger("effects")
    app.processEvents()
    panel = window.effects_panel
    assert panel.isVisible()

    eq = panel.pages["equalizer"]
    index = eq.presets.findData("Rock")
    eq.presets.setCurrentIndex(index)
    eq.presets.activated.emit(index)
    assert ctl.backend.equalizer[1][0] == 8.0
    eq.bands[2].slider.setValue(55)
    assert ctl.backend.equalizer[1][2] == 5.5
    assert eq.presets.currentText() == "Custom"

    from PySide6.QtWidgets import QInputDialog
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Bass boost", True))
    eq._save_preset()
    assert "Bass boost" in ctl.custom_eq_presets()
    assert eq.presets.currentText() == "Bass boost" and eq.delete.isEnabled()

    panel.show_page("audio")
    audio = panel.pages["audio"]
    audio.stereo["mono"].click()
    assert ctl.backend.stereo_mode == 7
    audio.normalize.setChecked(True)
    assert ctl.backend.normalization[0] is True

    panel.show_page("image")
    image = panel.pages["image"]
    image.sliders["brightness"].slider.setValue(150)
    assert ctl.backend.adjust["brightness"] == 1.5
    image.sliders["gamma"].slider.setValue(30)  # log scale: 10**0.3
    assert abs(ctl.backend.adjust["gamma"] - 1.995) < 0.01

    loads = len(ctl.backend.loads)
    window.actions.trigger("leave_fullscreen")  # Esc closes the panel first
    assert not panel.isVisible()
    assert len(ctl.backend.loads) == loads  # nothing above needed a reload


def test_video_page_flip_and_sharpen_marked_and_batched(window, app):
    ctl = window.ctl
    ctl.open(["http://x/clip.mp4"])
    window._tick()
    window.open_quick_settings("video")
    app.processEvents()
    page = window.quick.pages["video"]
    assert page.findChildren(type(page.pending), "ReloadBadge")
    loads = len(ctl.backend.loads)
    page.flip_h.click()
    page.sharpen.click()
    page.sharpen_strength.slider.setValue(120)
    assert page.pending.isVisibleTo(page)
    assert len(ctl.backend.loads) == loads
    ctl.apply_pending_reload()
    assert len(ctl.backend.loads) == loads + 1
    assert ctl.backend.pending_filters.transform == "hflip" and ctl.backend.pending_filters.sharpen == 1.2
    assert not page.pending.isVisibleTo(page)
    window.quick.hide()


def test_menus_include_effects(window):
    window.ctl.open(["http://x/clip.mp4"])
    window._tick()
    menu = window.build_menu()
    titles = []

    def walk(m):
        for act in m.actions():
            titles.append(act.text())
            if act.menu():
                walk(act.menu())

    walk(menu)
    for expected in ("Equalizer", "Rock", "Stereo mode", "Mono", "Volume normalization", "Flip horizontally",
                     "Sharpen", "Effects & filters…"):
        assert expected in titles, expected


def test_settings_scope_switch_goes_through_controller(window):
    window.ctl.set_adjust("gamma", 2.0)
    window.settings.effects_scope = "per_file"
    window.apply_settings()
    assert window.ctl.settings.effects_scope == "per_file"
    assert window.ctl.settings.effects["video"]["gamma"] == 2.0
