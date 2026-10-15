"""Theme switching: dark / light / follow-system, accent colour, and widgets following a switch."""

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from vlclone.core.controller import PlayerController  # noqa: E402
from vlclone.core.fake_backend import FakeBackend  # noqa: E402
from vlclone.core.settings import Settings  # noqa: E402
from vlclone.ui import theme  # noqa: E402


@pytest.fixture
def app():
    from PySide6.QtCore import QCoreApplication, QEvent

    application = QApplication.instance() or QApplication([])
    # Earlier test modules leave dozens of windows alive; every theme switch re-polishes all of
    # them, so start from a clean application (the real app has one window).
    for w in application.topLevelWidgets():
        w.hide()
        w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    yield application
    theme.apply_theme(application)  # other test modules expect the default dark theme


def bg_of(widget) -> str:
    img = widget.grab().toImage()
    return QColor(img.pixel(img.width() - 3, img.height() - 3)).name()


def test_dark_and_light_tokens_palette_and_stylesheet(app):
    assert theme.apply_theme(app, "light", "#3d8bfd") == "light"
    assert theme.BG == theme.LIGHT.bg and theme.TEXT == theme.LIGHT.text and theme.ACCENT == "#3d8bfd"
    assert app.palette().color(QPalette.Window).name() == theme.LIGHT.bg
    assert theme.LIGHT.bg in app.styleSheet() and theme.DARK.bg not in app.styleSheet()
    assert theme.apply_theme(app, "dark") == "dark"
    assert app.palette().color(QPalette.Window).name() == theme.DARK.bg
    assert theme.ACCENT == "#ff8a2a"


def test_follow_system(app, monkeypatch):
    monkeypatch.setattr(theme, "system_prefers_dark", lambda: False)
    assert theme.apply_theme(app, "system") == "light" and theme.requested()[0] == "system"
    monkeypatch.setattr(theme, "system_prefers_dark", lambda: True)
    theme._system_changed(app)  # the OS switched to dark
    assert theme.MODE == "dark" and theme.requested()[0] == "system"
    theme.apply_theme(app, "light")
    monkeypatch.setattr(theme, "system_prefers_dark", lambda: False)
    theme._system_changed(app)  # ignored: not following the system
    assert theme.MODE == "light"


@pytest.mark.parametrize("accent", ["#ff8a2a", "#3d8bfd", "#f5b82e", "#19b5b0", "#202020", "#ffffff"])
@pytest.mark.parametrize("mode", ["dark", "light"])
def test_accent_text_stays_readable(mode, accent):
    derived = theme.derive_accent(accent, mode)
    assert theme.contrast(derived["ACCENT_TEXT"], theme.PALETTES[mode].surface) >= 3.0
    assert theme.contrast(derived["ON_ACCENT"], derived["ACCENT"]) >= 3.0


def test_open_windows_follow_a_switch(app):
    from vlclone.ui.main_window import MainWindow

    s = Settings()
    window = MainWindow(PlayerController(FakeBackend(), s), s)
    window.resize(900, 600)
    window.show()
    window.show_library()
    app.processEvents()
    assert bg_of(window.library_view) == theme.DARK.bg
    seen = []
    theme.notifier().changed.connect(lambda: seen.append(theme.MODE))
    s.theme, s.accent_color = "light", "#9b6bff"
    window.apply_settings()  # Settings → Save
    app.processEvents()
    assert seen[-1] == "light"
    assert bg_of(window.library_view) == theme.LIGHT.bg
    assert bg_of(window.sidebar) == theme.LIGHT.surface
    # The video stage stays dark: the overlay's labels keep the video text colour.
    title = window.overlay.findChild(QLabel)
    assert title.palette().color(title.foregroundRole()).name() == theme.VIDEO_TEXT
    error = QLabel("x")
    theme.set_error(error)
    error.ensurePolished()
    assert error.palette().color(error.foregroundRole()).name() == theme.LIGHT.danger
    window.close()


def test_icons_follow_tokens(app):
    from vlclone.ui.icons import icon

    theme.apply_theme(app, "dark", "#ff8a2a")
    ic = icon("stop", "@ACCENT")

    def centre():
        return ic.pixmap(24, 24).toImage().pixelColor(12, 12).name()

    assert centre() == "#ff8a2a"
    theme.apply_theme(app, "dark", "#3d8bfd")
    assert centre() == "#3d8bfd"
