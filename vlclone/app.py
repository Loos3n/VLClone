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
    parser.add_argument("--self-test", metavar="REPORT",
                        help="load libVLC, write its version to REPORT and exit (used by the build)")
    return parser.parse_args(argv)


def self_test(report: str) -> int:
    """Headless check that the packaged app can load libVLC, play, and apply effects.

    Exit code is non-zero only if libVLC itself is unusable; effect results are
    written to the report so a build log shows them.
    """
    import tempfile
    import time

    lines: list[str] = []
    code = 0
    try:
        from .core.backend import PlaybackState
        from .core.effects import EngineFilters
        from .core.media_sources import path_to_mrl
        from .core.vlc_backend import VlcBackend
        from .selftest_media import write_tone

        backend = VlcBackend(["--quiet", "--vout=dummy", "--aout=dummy"])
        lines.append(f"ok libVLC {backend.engine_version()}")
        lines.append(f"equalizer presets: {len(backend.equalizer_presets())}")
        lines.append(f"normalization supported: {backend.supports_normalization}")
        with tempfile.TemporaryDirectory() as tmp:
            tone = os.path.join(tmp, "tone.wav")
            write_tone(tone)
            backend.set_engine_filters(EngineFilters("hflip", 0.5))
            backend.load(path_to_mrl(tone))
            backend.play()
            deadline = time.monotonic() + 8
            while backend.state() is not PlaybackState.PLAYING and time.monotonic() < deadline:
                time.sleep(0.05)
            lines.append(f"playback (filtered instance): {backend.state().value}")
            backend.set_equalizer(5.0, [2.0] * 10)
            backend.set_stereo_mode(7)
            lines.append(f"stereo mode set: {backend.vlc.libvlc_audio_get_channel(backend.player)}")
            if backend.supports_normalization:
                ok = backend._aout_hook.set_chain(backend.player, "equalizer:normvol", 2.0)
                lines.append(f"normalization hook applied: {ok}")
            backend.stop()
            backend.set_engine_filters(EngineFilters())
            lines.extend(_self_test_phase3(backend, tmp, tone))
            lines.extend(_self_test_phase4(tmp))
        backend.release()
    except Exception as exc:  # report anything: this runs without a console
        lines.append(f"fail {type(exc).__name__}: {exc}")
        code = 1
    with open(report, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return code


def make_transcoder_factory(fake: bool = False):
    """Engine for conversions/recording/streaming: a separate libVLC instance, created on first use."""
    if fake:
        from .core.fake_backend import FakeTranscoder

        return lambda: FakeTranscoder(realtime_speed=4.0)

    def factory():
        from .core.vlc_backend import VlcTranscoder

        return VlcTranscoder()

    return factory


def make_library(settings, fake: bool = False):
    """The media library service; metadata and thumbnails come from libVLC unless faked."""
    from .core.library import LibraryDB
    from .core.library_scanner import FilenameProbe, LibraryService

    if fake:
        from .core.fake_backend import FakeProbe

        factory = FakeProbe
    else:
        def factory():
            from .core.vlc_backend import VlcMediaProbe

            try:
                return VlcMediaProbe()
            except Exception:  # libVLC trouble must not stop the library from listing files
                return FilenameProbe()

    service = LibraryService(LibraryDB(), factory, watch=settings.library_watch,
                             thumbnails=settings.library_thumbnails)
    if service.folders():
        service.rescan()  # incremental: picks up what changed while the app was closed
    return service


def _wait(cond, timeout: float = 8.0) -> bool:
    import time

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def _self_test_phase3(backend, tmp: str, tone: str) -> list[str]:
    """Chapters, a local HTTP stream, a failing URL, library probe/thumbnail, devices and drives."""
    import functools
    import http.server
    import threading

    from .core.backend import PlaybackState
    from .core.discs import list_optical_drives
    from .core.media_sources import path_to_mrl
    from .core.vlc_backend import VlcMediaProbe
    from .selftest_media import write_chapter_mkv, write_y4m

    lines: list[str] = []

    def step(name, fn) -> None:
        try:
            lines.append(f"{name}: {fn()}")
        except Exception as exc:  # one failing check must not hide the others
            lines.append(f"{name}: fail {type(exc).__name__}: {exc}")

    def chapters() -> str:
        mkv = os.path.join(tmp, "chapters.mkv")
        write_chapter_mkv(mkv)
        backend.load(path_to_mrl(mkv))
        backend.play()
        if not _wait(lambda: backend.state() is PlaybackState.PLAYING) or not _wait(lambda: backend.chapters()):
            return f"none (state {backend.state().value})"
        names = [c.title for c in backend.chapters()]
        backend.set_chapter(2)
        jumped = _wait(lambda: backend.time() >= 4000, 4)
        backend.stop()
        return f"{len(names)} ({', '.join(names)}); jump {'ok' if jumped else 'failed'}"

    class QuietHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args) -> None:  # the windowed exe has no stderr
            pass

    handler = functools.partial(QuietHandler, directory=tmp)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def http_stream() -> str:
        backend.load(f"{base}/{os.path.basename(tone)}")
        backend.play()
        ok = _wait(lambda: backend.state() is PlaybackState.PLAYING, 10)
        state = backend.state().value
        backend.stop()
        return "playing" if ok else f"not playing ({state})"

    def http_missing() -> str:
        backend.load(f"{base}/missing.wav")
        backend.play()
        _wait(lambda: backend.state() in (PlaybackState.ERROR, PlaybackState.ENDED), 10)
        state = backend.state().value
        backend.stop()
        # libVLC ends (rather than errors) on a 404; the controller reports both as open failures.
        return f"failed as expected ({state})" if state in ("error", "ended") else f"unexpected {state}"

    def library_probe() -> str:
        video = os.path.join(tmp, "clip.y4m")
        write_y4m(video)
        probe = VlcMediaProbe()
        try:
            meta = probe.probe(video)
            thumb = probe.thumbnail(video, os.path.join(tmp, "thumb.png"), 64)
        finally:
            probe.close()
        return f"{meta.width}x{meta.height} {meta.duration_ms} ms, thumbnail {thumb}"

    def devices() -> str:
        found = backend.capture_devices()
        return f"{len(found.get('video', []))} video, {len(found.get('audio', []))} audio"

    try:
        step("chapters", chapters)
        step("http stream", http_stream)
        step("http 404", http_missing)
    finally:
        server.shutdown()
    step("library probe", library_probe)
    step("capture devices", devices)
    step("optical drives", lambda: len(list_optical_drives()))
    return lines


def _self_test_phase4(tmp: str) -> list[str]:
    """MP4 and MP3 conversions through the real queue, every preset probed, an HTTP stream fetched."""
    import socket
    import time
    import urllib.request

    from .core.converter import ConvertQueue, JobState, probe_presets
    from .core.media_sources import path_to_mrl
    from .core.streamer import HttpStreamer
    from .core.transcode import CONVERT_PRESETS, ConvertSettings, mpeg_audio_layer
    from .core.vlc_backend import VlcTranscoder
    from .selftest_media import write_tone, write_y4m

    lines: list[str] = []
    try:
        engine = VlcTranscoder()
    except Exception as exc:
        return [f"converter: fail {type(exc).__name__}: {exc}"]
    try:
        folder = os.path.join(tmp, "convert out")  # a space, like real Windows paths
        os.makedirs(folder, exist_ok=True)
        video, audio = os.path.join(tmp, "src.y4m"), os.path.join(tmp, "src.wav")
        write_y4m(video, 160, 120, frames=75)
        write_tone(audio, 3)
        queue = ConvertQueue(lambda: engine)
        for key, name in (("mp4_h264", "it's mp4.mp4"), ("mp3", "song.mp3")):
            job = queue.add(path_to_mrl(video), key, ConvertSettings(key), os.path.join(folder, name),
                            [f":input-slave={path_to_mrl(audio)}"])
            end = time.monotonic() + 60
            while not job.finished and time.monotonic() < end:
                queue.tick()
                time.sleep(0.05)
            if job.state is JobState.DONE:
                info = engine.media_info(job.output)
                extra = f", MPEG layer {mpeg_audio_layer(job.output)}" if key == "mp3" else ""
                lines.append(f"convert {key}: ok ({'+'.join(info.video + info.audio)}{extra}, "
                             f"{os.path.getsize(job.output)} bytes)")
            else:
                lines.append(f"convert {key}: fail ({job.state.value}: {job.error})")
        leftovers = [f for f in os.listdir(folder) if f.endswith(".part")]
        lines.append(f"partial files left: {len(leftovers)}")

        caps = probe_presets(engine, workdir=tmp)
        ok = [k for k in CONVERT_PRESETS if caps[k].ok]
        bad = [f"{k} ({caps[k].reason})" for k in CONVERT_PRESETS if not caps[k].ok]
        lines.append(f"presets working: {', '.join(ok) or 'none'}")
        lines.append(f"presets disabled: {', '.join(bad) or 'none'}")
        lines.append(f"screen capture module: {engine.has_module('screen')}")

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        streamer = HttpStreamer(lambda: engine)
        error = streamer.start(path_to_mrl(video), [f":input-slave={path_to_mrl(audio)}"], "src", 0, "h264", port)
        data = b""
        if error is None:
            end = time.monotonic() + 10
            while time.monotonic() < end and len(data) < 188 * 20:
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/stream", timeout=3) as resp:
                        data += resp.read(188 * 20)
                except OSError:
                    time.sleep(0.2)
            streamer.stop()
        ts = len(data) >= 188 * 5 and all(data[i] == 0x47 for i in range(0, 188 * 5, 188))
        lines.append(f"http stream out: {'ok' if ts else 'fail'} ({error or f'{len(data)} bytes'})")
    except Exception as exc:
        lines.append(f"converter: fail {type(exc).__name__}: {exc}")
    finally:
        engine.release()
    return lines


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.self_test:
        return self_test(args.self_test)
    if sys.platform.startswith("linux") and not args.fake_backend:
        # libVLC embeds into X11 windows; under Wayland use XWayland.
        os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication, QMessageBox

    from . import APP_NAME, __version__
    from .core.controller import PlayerController
    from .core.effects import EffectsStore, EqPresetStore
    from .core.resume import ResumeStore
    from .core.fake_backend import FakeTranscoder
    from .core.session import clear_saved_playlist, restore_playlist, save_playlist
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
    controller = PlayerController(backend, settings, resume, EffectsStore(), EqPresetStore())
    if settings.restore_playlist:
        restore_playlist(controller.playlist)
    library = make_library(settings, fake=args.fake_backend)
    transcoder = make_transcoder_factory(fake=args.fake_backend)
    window = MainWindow(controller, settings, library, transcoder,
                        (lambda: FakeTranscoder(instant=True)) if args.fake_backend else None)
    window.presets.start()  # background: which presets this libVLC can write (cached per version)
    window.show()
    if args.inputs:
        controller.open(args.inputs)

    code = app.exec()
    try:
        if settings.restore_playlist:
            save_playlist(controller.playlist)
        else:
            clear_saved_playlist()
    except OSError:
        pass
    controller.shutdown()
    library.shutdown()
    try:
        settings.save()
    except OSError:
        pass
    return code


if __name__ == "__main__":
    sys.exit(main())
