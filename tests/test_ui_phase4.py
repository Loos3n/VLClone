"""Offscreen UI tests for phase 4: Convert dialog, queue panel, recording, capture recording, Stream dialog."""

import os
import socket

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from vlclone.core.controller import PlayerController  # noqa: E402
from vlclone.core.converter import JobState  # noqa: E402
from vlclone.core.fake_backend import FakeBackend, FakeTranscoder  # noqa: E402
from vlclone.core.library import LibraryDB  # noqa: E402
from vlclone.core.library_scanner import LibraryService  # noqa: E402
from vlclone.core.media_sources import path_to_mrl  # noqa: E402
from vlclone.core.settings import Settings  # noqa: E402


@pytest.fixture(scope="module")
def app():
    from vlclone.ui.theme import apply_theme

    application = QApplication.instance() or QApplication([])
    apply_theme(application)
    return application


@pytest.fixture(autouse=True)
def _no_blocking_dialogs(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError(f"unexpected message box: {args[1:3]}")

    for name in ("warning", "critical", "information", "question"):
        monkeypatch.setattr(QMessageBox, name, boom)


@pytest.fixture
def engine():
    return FakeTranscoder()


@pytest.fixture
def window(app, tmp_path, engine):
    from vlclone.ui.main_window import MainWindow

    def probe_engine():
        e = FakeTranscoder(instant=True)
        e.unsupported |= {"hevc", "webm"}  # pretend this VLC lacks H.265 and WebM
        return e

    settings = Settings(snapshot_dir=str(tmp_path), convert_output_dir=str(tmp_path / "out"),
                        record_dir=str(tmp_path / "rec"), resume_playback=False)
    ctl = PlayerController(FakeBackend(), settings)
    library = LibraryService(LibraryDB(tmp_path / "lib.db"), thumbs_dir=tmp_path / "t", watch=False)
    win = MainWindow(ctl, settings, library, lambda: engine, probe_engine)
    win.presets.start()
    assert win.presets.wait(30)
    win.show()
    app.processEvents()
    yield win
    for job in win.converter.jobs:  # don't trigger the "conversions running" question
        if not job.finished:
            win.converter.cancel(job.id)
    win.close()
    library.shutdown()
    app.processEvents()


def media(tmp_path, name="clip.mkv"):
    path = tmp_path / name
    path.write_bytes(b"x")
    return str(path)


# --------------------------------------------------------------- shortcuts
def test_phase4_shortcuts(window):
    from vlclone.core.shortcuts import DEFAULT_SHORTCUTS, find_conflicts, resolve_shortcuts

    for action, key in (("convert", "Ctrl+R"), ("stream", "Ctrl+S"), ("record", "Shift+R")):
        assert window.actions.get(action).shortcuts()[0].toString() == key and action in DEFAULT_SHORTCUTS
    assert "conversions" in DEFAULT_SHORTCUTS
    assert find_conflicts(resolve_shortcuts()) == {}


# ------------------------------------------------------------ convert dialog
def test_convert_dialog_disables_unsupported_presets(window, tmp_path):
    from vlclone.ui.convert_dialog import ConvertDialog

    dlg = ConvertDialog(window.converter, window.presets, window.settings, [], window)
    rows = {dlg.preset_model.item(r).data(Qt.UserRole): dlg.preset_model.item(r)
            for r in range(dlg.preset_model.rowCount())}
    assert not rows["mkv_h265"].isEnabled() and "Not available" in rows["mkv_h265"].toolTip()
    assert not rows["webm_vp9"].isEnabled() and rows["mp4_h264"].isEnabled() and rows["mp3"].isEnabled()
    dlg.preset.setCurrentIndex(dlg.preset.findData("mkv_h265", Qt.UserRole))
    dlg.add_inputs([media(tmp_path)])
    with pytest.raises(ValueError, match="not available"):
        dlg.build_jobs()


def test_convert_dialog_builds_and_queues_jobs(window, engine, tmp_path, app):
    from vlclone.ui.convert_dialog import ConvertDialog

    a, b = media(tmp_path, "a.mkv"), media(tmp_path, "b.avi")
    dlg = ConvertDialog(window.converter, window.presets, window.settings, [], window)
    with pytest.raises(ValueError, match="Add at least one"):
        dlg.build_jobs()
    dlg.add_inputs([a, b, a])  # duplicates ignored (as when dropped twice)
    assert dlg.sources.count() == 2
    dlg.preset.setCurrentIndex(dlg.preset.findData("mp3", Qt.UserRole))
    assert not dlg.resolution.isEnabled() and dlg.abitrate.isEnabled()
    dlg.more.setChecked(True)
    dlg.trim_start.setText("0:05")
    dlg.trim_end.setText("0:03")
    dlg.accept()
    assert "end of the trim" in dlg.error.text() and not window.converter.jobs
    dlg.trim_end.setText("1:00")
    dlg.abitrate.setCurrentIndex(dlg.abitrate.findData(256))
    dlg.accept()
    jobs = window.converter.jobs
    assert [os.path.basename(j.output) for j in jobs] == ["a.mp3", "b.mp3"]
    assert jobs[0].settings.start_ms == 5000 and jobs[0].settings.end_ms == 60_000
    assert jobs[0].settings.audio_kbps == 256
    assert window.settings.convert_preset == "mp3"
    assert os.path.dirname(jobs[0].output) == str(tmp_path / "out")


def test_convert_next_to_source_and_remux_check(window, engine, tmp_path):
    from vlclone.core.transcode import SourceInfo
    from vlclone.ui.convert_dialog import ConvertDialog

    src = media(tmp_path, "film.webm")
    engine.sources[src] = SourceInfo(["VP90"], ["Opus"])
    dlg = ConvertDialog(window.converter, window.presets, window.settings, [], window)
    dlg.add_inputs([src])
    dlg.next_to_source.setChecked(True)
    dlg.preset.setCurrentIndex(dlg.preset.findData("remux_mp4", Qt.UserRole))
    with pytest.raises(ValueError, match="Remux to MKV"):
        dlg.build_jobs()
    dlg.preset.setCurrentIndex(dlg.preset.findData("remux_mkv", Qt.UserRole))
    (_, _, out), = dlg.build_jobs()
    assert out == str(tmp_path / "film.mkv")
    assert dlg.subtitles.isEnabled() is False  # no burn-in when copying streams


def test_convert_action_uses_current_media_and_queue_panel(window, engine, tmp_path, app, monkeypatch):
    from vlclone.ui import convert_dialog

    src = media(tmp_path, "movie.mkv")
    window.ctl.open([src], options=[":network-caching=100"])
    window._tick()
    seen = []

    def fake_exec(self):
        seen.append([s.mrl for s in self.source_list()])
        self.accept()
        return True

    monkeypatch.setattr(convert_dialog.ConvertDialog, "exec", fake_exec)
    window.actions.trigger("convert")
    assert seen == [[path_to_mrl(src)]]
    panel = window._queue_panel
    assert panel is not None and panel.isVisible()
    job = window.converter.jobs[0]
    window._tick()  # starts
    engine.advance(5000)
    window._tick()
    row = panel.rows[job.id]
    assert row.status.text().startswith("50 %") and row.buttons["cancel"].isVisibleTo(row)
    engine.advance(5000)
    window._tick()
    assert job.state is JobState.DONE and row.buttons["open"].isVisibleTo(row)
    toast = window.overlay.toast
    assert toast.isVisible() and toast.label.text() == f"Saved {os.path.basename(job.output)}"
    row.buttons["remove"].click()
    assert window.converter.jobs == []


def test_queue_panel_cancel_deletes_partial_file(window, engine, tmp_path):
    from vlclone.core.transcode import ConvertSettings

    job = window.converter.add(path_to_mrl(media(tmp_path)), "clip", ConvertSettings("mp4_h264"),
                               str(tmp_path / "out" / "clip.mp4"))
    window._tick()
    assert os.path.exists(job.part)
    panel = window.show_conversions()
    panel.rows[job.id].buttons["cancel"].click()
    assert job.state is JobState.CANCELLED and not os.path.exists(job.part)
    assert panel.rows[job.id].buttons["retry"].isVisibleTo(panel.rows[job.id])


def test_playlist_and_library_offer_convert(window, tmp_path, monkeypatch):
    from vlclone.ui import convert_dialog

    opened = []
    monkeypatch.setattr(convert_dialog.ConvertDialog, "exec",
                        lambda self: opened.append([s.title for s in self.source_list()]) or False)
    window.ctl.open([media(tmp_path, "one.mkv"), media(tmp_path, "two.mkv")], play=False)
    menu = window.sidebar.build_context_menu(window.sidebar.model.index(1))
    next(a for a in menu.actions() if a.text() == "Convert / save…").trigger()
    assert opened == [["two.mkv"]]
    from vlclone.core.library import LibraryItem

    item = LibraryItem(path=str(tmp_path / "one.mkv"), kind="video", title="One")
    menu = window.library_view.build_item_menu([item])
    next(a for a in menu.actions() if a.text() == "Convert / save…").trigger()
    assert opened[-1] == ["One"]


# --------------------------------------------------------------- recording
def test_record_button_segment_and_indicator(window, engine, tmp_path, app):
    ctl = window.ctl
    ctl.open([media(tmp_path, "show.mkv")])
    window._tick()
    ctl.backend.advance(4000)
    window.actions.trigger("record")
    window._tick()
    rec = window.overlay.rec
    assert window.recorder.active and rec.isVisible() and rec.text().startswith("● REC")
    assert window.overlay.controls.buttons["record"].property("recording") is True
    ctl.backend.advance(6000)
    window._tick()
    assert rec.text() == "● REC  0:06"
    window.actions.trigger("record")
    window._tick()
    assert not window.recorder.active and not rec.isVisible()
    job = window.converter.jobs[0]
    assert job.settings.preset == "remux_mkv" and (job.settings.start_ms, job.settings.end_ms) == (4000, 10_000)
    assert job.output.startswith(str(tmp_path / "rec"))
    engine.advance(10_000)
    window._tick()
    assert job.state is JobState.DONE and os.path.exists(job.output)


def test_record_refusal_is_shown(window):
    window.actions.trigger("record")
    assert not window.recorder.active
    assert "Play something" in window.overlay.osd.text()


def test_capture_screen_record_to_file(window, engine, tmp_path, monkeypatch):
    from vlclone.ui import open_media

    def fake_exec(self):
        tab = self.tabs["capture"]
        assert self.record_button.isVisibleTo(self)
        tab.set_source("screen")
        tab.region.setChecked(True)
        tab.region_width.setValue(800)
        tab.region_height.setValue(600)
        self._finish(play=False, record=True)
        return True

    monkeypatch.setattr(open_media.OpenMediaDialog, "exec", fake_exec)
    window.actions.trigger("open_capture")
    job = window.converter.jobs[0]
    assert job.live and job.state is JobState.RUNNING and job.source == "screen://"
    assert ":screen-width=800" in job.source_options and job.settings.preset == "mkv_h264"
    assert job.output.endswith(".mkv") and "Screen 800×600" in job.title
    panel = window._queue_panel
    row = panel.rows[job.id]
    assert row.buttons["stop"].isVisibleTo(row) and row.buttons["cancel"].text() == "Discard"
    row.buttons["stop"].click()
    assert job.state is JobState.DONE and os.path.exists(job.output)
    assert window.ctl.playlist.items == []  # recorded, not played


def test_screen_source_disabled_without_module(window, engine, monkeypatch):
    from vlclone.ui import open_media

    engine.modules = {"screen": False, "xcb_screen": False}
    seen = []

    def fake_exec(self):
        b = self.tabs["capture"].source_buttons["screen"]
        seen.append((b.isEnabled(), b.toolTip()))
        return False

    monkeypatch.setattr(open_media.OpenMediaDialog, "exec", fake_exec)
    window.actions.trigger("open_capture")
    assert seen[0][0] is False and "screen-capture" in seen[0][1]


# ------------------------------------------------------------------ stream
def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_stream_dialog(window, engine, tmp_path):
    dlg = window.open_stream_dialog()
    assert not dlg.toggle.isEnabled() and "Nothing is playing" in dlg.media.text()
    window.ctl.open([media(tmp_path, "film.mkv")])
    window._tick()
    window.ctl.backend.advance(7000)
    dlg.refresh()
    port = _free_port()
    dlg.port.setValue(port)
    dlg.mode.setCurrentIndex(dlg.mode.findData("h264"))
    dlg.toggle.click()
    assert window.streamer.active and dlg.url.text().endswith(f":{port}/stream")
    assert not dlg.port.isEnabled() and dlg.toggle.text() == "Stop streaming"
    mrl, opts = engine.started[-1]
    assert ":start-time=7.000" in opts and any("vcodec=h264" in o for o in opts)
    assert window.settings.stream_port == port and window.settings.stream_mode == "h264"
    dlg.toggle.click()
    assert not window.streamer.active and dlg.url.text() == ""


# ------------------------------------------------------------------- exit
def test_quit_asks_when_conversions_run(window, engine, tmp_path, monkeypatch):
    from vlclone.core.transcode import ConvertSettings

    job = window.converter.add(path_to_mrl(media(tmp_path)), "clip", ConvertSettings("mp4_h264"),
                               str(tmp_path / "out" / "c.mp4"))
    window._tick()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)
    window.close()
    assert window.isVisible() and job.state is JobState.RUNNING
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    window.close()
    assert job.state is JobState.CANCELLED and not os.path.exists(job.part)


def test_quit_saves_an_active_recording(window, engine, tmp_path):
    engine.instant = True
    window.ctl.open([media(tmp_path, "show.mkv")])
    window._tick()
    window.actions.trigger("record")
    window.ctl.backend.advance(3000)
    window._tick()
    window.close()
    job = window.converter.jobs[0]
    assert job.state is JobState.DONE and os.path.exists(job.output)


def test_menu_has_convert_stream_record(window):
    menu = window.build_menu()
    media_menu = next(a.menu() for a in menu.actions() if a.text() == "Media")
    labels = [a.text() for a in media_menu.actions() if a.text()]
    for label in ("Convert / save…", "Stream…", "Record", "Conversions…"):
        assert label in labels
