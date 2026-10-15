"""Screenshot check: every theme (dark, light, follow-system, a custom accent) and the mini player.

Each scene is rendered from the widgets themselves and composited (the
controls overlay is its own window above the video), then checked pixel by
pixel against the theme's tokens: window and panel backgrounds, popover
surfaces, the accent on primary buttons, and the always-dark video stage.

Runs offscreen in the normal test run. CI also runs it on a virtual X display:

    xvfb-run -a env QT_QPA_PLATFORM=xcb VLCLONE_SCREENSHOT_DIR=shots \\
        python -m pytest tests/test_screenshots.py

With VLCLONE_SCREENSHOT_DIR set, every scene is saved there as PNG.
"""

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QCoreApplication, QEvent, QPoint  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from vlclone.core.backend import PlaybackState  # noqa: E402
from vlclone.core.controller import PlayerController  # noqa: E402
from vlclone.core.fake_backend import FakeBackend, FakeProbe  # noqa: E402
from vlclone.core.library import LibraryDB  # noqa: E402
from vlclone.core.library_scanner import LibraryService  # noqa: E402
from vlclone.core.settings import Settings  # noqa: E402
from vlclone.ui import theme  # noqa: E402

THEMES = [("dark", "#ff8a2a"), ("light", "#ff8a2a"), ("system", "#ff8a2a"), ("light", "#3d8bfd"),
          ("dark", "#9b6bff")]


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    for w in application.topLevelWidgets():  # theme switches re-polish every live widget
        w.hide()
        w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    yield application
    theme.apply_theme(application)


@pytest.fixture(scope="module")
def out_dir(tmp_path_factory):
    target = os.environ.get("VLCLONE_SCREENSHOT_DIR")
    path = Path(target) if target else tmp_path_factory.mktemp("shots")
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def window(app, tmp_path):
    from vlclone.ui.main_window import MainWindow

    s = Settings()
    lib = LibraryService(LibraryDB(tmp_path / "lib.db"), FakeProbe, thumbs_dir=tmp_path / "thumbs",
                         thumbnails=False)
    w = MainWindow(PlayerController(FakeBackend(), s), s, lib, preview_probe_factory=FakeProbe)
    w.resize(1120, 660)
    w.show()
    clips = []
    for name in ("Big Buck Bunny (2008).mkv", "Sintel.mp4", "Tears of Steel.webm", "Night Drive - Synthwave.mp3"):
        path = tmp_path / name
        path.write_bytes(b"x" * 8)
        clips.append(str(path))
    w.ctl.open(clips)
    for _ in range(60):
        w._tick()
        app.processEvents()
        if w.ctl.state is PlaybackState.PLAYING:
            break
    w.ctl.seek(w.ctl.length() // 3)
    settle(app, w)
    yield w
    w.shutdown_services()
    for top in (w.quick, w.effects_panel, w.overlay, w):
        top.hide()
    w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def settle(app, w, n=4):
    for _ in range(n):
        w._tick()
        app.processEvents()


def composite(w) -> QImage:
    """The window with the overlay (a separate translucent window) painted over the video."""
    img = w.grab().toImage().convertToFormat(QImage.Format.Format_ARGB32)
    if w.overlay.isVisible():
        p = QPainter(img)
        origin = w.video.mapTo(w, QPoint(0, 0))
        p.drawImage(origin, w.overlay.grab().toImage())
        p.end()
    return img


def px(img: QImage, x: int, y: int) -> str:
    return QColor(img.pixel(x, y)).name()


def close_to(a: str, b: str, tol: int = 6) -> bool:
    ca, cb = QColor(a), QColor(b)
    return max(abs(ca.red() - cb.red()), abs(ca.green() - cb.green()), abs(ca.blue() - cb.blue())) <= tol


@pytest.mark.parametrize("mode,accent", THEMES)
def test_every_theme(app, window, out_dir, mode, accent):
    w = window
    resolved = theme.apply_theme(app, mode, accent)
    pal = theme.PALETTES[resolved]
    tag = f"{mode}-{accent[1:]}"
    w.overlay.reveal()
    settle(app, w)

    # 1. Player: video stage + playlist sidebar.
    shot = composite(w)
    shot.save(str(out_dir / f"{tag}-player.png"))
    side = w.sidebar.mapTo(w, QPoint(0, 0))
    assert close_to(px(shot, side.x() + w.sidebar.width() - 6, shot.height() - 6), pal.surface)
    stage = w.overlay.controls.mapTo(w, QPoint(0, 0)) + w.video.mapTo(w, QPoint(0, 0))
    bar = QColor(px(shot, w.video.width() // 2, stage.y() + w.overlay.controls.height() - 4))
    assert bar.lightness() < 60, "the video stage must stay dark in every theme"
    title = w.overlay.title.palette().color(w.overlay.title.foregroundRole()).name()
    assert title == theme.VIDEO_TEXT

    # 2. Quick settings popover and Effects panel follow the theme.
    w.open_quick_settings("playback")
    settle(app, w)
    pop = w.quick.grab().toImage()
    pop.save(str(out_dir / f"{tag}-quick-settings.png"))
    inner = w.quick.frame.geometry()
    assert close_to(px(pop, inner.left() + 30, inner.bottom() - 8), pal.surface)
    w.quick.hide()
    w.open_effects("equalizer")
    settle(app, w)
    fx = w.effects_panel.grab().toImage()
    fx.save(str(out_dir / f"{tag}-effects.png"))
    inner = w.effects_panel.frame.geometry()
    assert close_to(px(fx, inner.left() + 30, inner.bottom() - 8), pal.surface)
    w.effects_panel.hide()

    # 3. Library view.
    w.show_library()
    settle(app, w)
    lib = w.grab().toImage()
    lib.save(str(out_dir / f"{tag}-library.png"))
    centre = w.library_view.mapTo(w, w.library_view.rect().center())
    assert close_to(px(lib, centre.x(), w.library_view.mapTo(w, QPoint(0, 0)).y() + w.library_view.height() - 8),
                    pal.bg)
    w.show_player()

    # 4. Settings (Appearance) and the welcome screen.
    from vlclone.ui.settings_dialog import SettingsDialog
    from vlclone.ui.welcome import WelcomeDialog

    dlg = SettingsDialog(w.settings, lambda: None, w, host=w)
    dlg.show_section("appearance")
    dlg.resize(860, 640)
    dlg.show()
    settle(app, w)
    img = dlg.grab().toImage()
    img.save(str(out_dir / f"{tag}-settings.png"))
    assert close_to(px(img, 4, img.height() // 2), pal.bg)
    save = dlg.ok_button.mapTo(dlg, dlg.ok_button.rect().center())
    assert close_to(px(img, save.x() - dlg.ok_button.width() // 2 + 6, save.y()), theme.ACCENT, 12)
    dlg.close()
    welcome = WelcomeDialog(w.settings, w.library, w)
    welcome.show()
    settle(app, w)
    img = welcome.grab().toImage()
    img.save(str(out_dir / f"{tag}-welcome.png"))
    assert close_to(px(img, 4, img.height() // 2), pal.bg)
    welcome.hide()
    welcome.deleteLater()


@pytest.mark.parametrize("mode", ["dark", "light"])
def test_mini_player(app, window, out_dir, mode):
    w = window
    theme.apply_theme(app, mode)
    w.set_mini_player(True)
    w.overlay.reveal()
    settle(app, w, 6)
    shot = composite(w)
    shot.save(str(out_dir / f"{mode}-mini-player.png"))
    assert (shot.width(), shot.height()) == (w.width(), w.height()) and w.width() <= 500
    assert w.overlay.compact and not w.sidebar.isVisible()
    play = w.overlay.controls.buttons["play_pause"]
    assert play.isVisibleTo(w.overlay) and not w.overlay.controls.speed_chip.isVisibleTo(w.overlay)
    # Controls inside the window, on the dark stage.
    centre = play.mapTo(w.overlay, play.rect().center()) + w.video.mapTo(w, QPoint(0, 0))
    assert 0 < centre.x() < shot.width() and 0 < centre.y() < shot.height()
    assert QColor(px(shot, 6, shot.height() - 4)).lightness() < 60
    w.set_mini_player(False)
