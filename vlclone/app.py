"""Application entry point."""

from __future__ import annotations

import argparse
import os
import sys


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="vlclone", description="A modern player built on libVLC.")
    parser.add_argument("inputs", nargs="*", help="files, folders, playlists or URLs to play")
    parser.add_argument("--fake-backend", action="store_true",
                        help="run the UI without libVLC (for UI development)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if sys.platform.startswith("linux") and not args.fake_backend:
        # libVLC embeds into X11 windows; under Wayland use XWayland.
        os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication, QMessageBox

    from . import APP_NAME, __version__
    from .core.controller import PlayerController
    from .core.resume import ResumeStore
    from .core.settings import Settings
    from .ui.main_window import MainWindow
    from .ui.theme import apply_theme

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName(APP_NAME)
    apply_theme(app)

    settings = Settings.load()
    if args.fake_backend:
        from .core.fake_backend import FakeBackend

        backend = FakeBackend()
    else:
        from .core.vlc_backend import LibVlcNotFound, VlcBackend

        try:
            backend = VlcBackend(settings.vlc_args())
        except LibVlcNotFound as exc:
            QMessageBox.critical(None, f"{APP_NAME} — VLC not found", str(exc))
            return 1

    resume = ResumeStore(min_position_ms=settings.resume_min_seconds * 1000)
    controller = PlayerController(backend, settings, resume)
    window = MainWindow(controller, settings)
    window.show()
    if args.inputs:
        controller.open(args.inputs)

    code = app.exec()
    controller.shutdown()
    try:
        settings.save()
    except OSError:
        pass
    return code


if __name__ == "__main__":
    sys.exit(main())
