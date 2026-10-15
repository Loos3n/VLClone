"""Main window: video, overlay controls, playlist sidebar, menus."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, QEasingCurve, QPoint, QRect, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QActionGroup, QCloseEvent
from PySide6.QtWidgets import (QApplication, QFileDialog, QMainWindow, QMenu, QMessageBox, QSplitter)

from .. import APP_NAME
from ..core.backend import PlaybackState
from ..core.controller import (ASPECT_RATIOS, CROP_RATIOS, DEINTERLACE_MODES, ROTATIONS, ZOOM_LEVELS,
                               PlayerController, zoom_label)
from ..core.m3u import write_m3u
from ..core.media_sources import (AUDIO_EXTENSIONS, PLAYLIST_EXTENSIONS, SUBTITLE_EXTENSIONS,
                                  VIDEO_EXTENSIONS, is_subtitle_file, mrl_to_path)
from ..core.playlist import RepeatMode
from ..core.settings import Settings
from ..core.stepping import RATE_PRESETS
from ..core.timefmt import format_ms
from .actions import ActionRegistry
from .dialogs import JumpToTimeDialog, OpenUrlDialog
from .icons import icon
from .overlay import OverlayWindow
from .playlist_panel import PlaylistPanel
from .quick_settings import AudioPage, QuickSettings, SubtitlesPage, VideoPage
from .video_surface import VideoSurface


def _filter(name: str, exts) -> str:
    return f"{name} ({' '.join('*.' + e for e in sorted(exts))})"


MEDIA_FILTER = ";;".join([
    _filter("Media files", VIDEO_EXTENSIONS | AUDIO_EXTENSIONS | PLAYLIST_EXTENSIONS),
    _filter("Video files", VIDEO_EXTENSIONS),
    _filter("Audio files", AUDIO_EXTENSIONS),
    _filter("Playlists", PLAYLIST_EXTENSIONS),
    "All files (*)",
])
SUBTITLE_FILTER = _filter("Subtitles", SUBTITLE_EXTENSIONS) + ";;All files (*)"


class MainWindow(QMainWindow):
    TICK_MS = 100

    def __init__(self, controller: PlayerController, settings: Settings) -> None:
        super().__init__()
        self.ctl = controller
        self.settings = settings
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(icon("cone"))
        self.setAcceptDrops(True)
        self.resize(1280, 760)
        self.setMinimumSize(640, 400)

        self.actions = ActionRegistry(self, settings.shortcuts)
        self.video = VideoSurface()
        self.sidebar = PlaylistPanel(controller.playlist, self.actions.trigger)
        self.sidebar.play_requested.connect(self.ctl.play_index)
        self.sidebar.files_dropped.connect(lambda items, row: self.ctl.open(items, play=False, at=row))
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setHandleWidth(3)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self.video)
        self.splitter.addWidget(self.sidebar)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.splitterMoved.connect(self._sidebar_resized)
        self.sidebar.setMinimumWidth(220)
        self.splitter.setFocusPolicy(Qt.StrongFocus)
        self.setCentralWidget(self.splitter)

        self.overlay = OverlayWindow(self, self.actions.trigger, settings.controls_hide_delay_ms)
        self.overlay.double_clicked.connect(self.toggle_fullscreen)
        self.overlay.clicked.connect(self._video_clicked)
        self.overlay.pressed.connect(self.take_focus)
        self.overlay.wheel.connect(self.ctl.change_volume)
        self.overlay.context_requested.connect(self._show_context_menu)
        self.overlay.files_dropped.connect(self.open_inputs)
        self.overlay.controls.seek.seek_requested.connect(self.ctl.seek)
        self.overlay.controls.volume.volume_requested.connect(lambda v: self.ctl.set_volume(v, show=False))
        self.overlay.controls.volume.set_maximum(self.ctl.max_volume)

        self.quick = QuickSettings(controller, self)
        self.quick.closed.connect(lambda: self.overlay.pin("popover", False))
        self.extend_quick_settings(self.quick)

        self._fullscreen_restore_sidebar = False
        self._was_maximized = False
        self._attached = False
        self._sidebar_anim = QVariantAnimation(self, duration=220, easingCurve=QEasingCurve.OutCubic)
        self._sidebar_anim.valueChanged.connect(self._apply_sidebar_width)
        self._sidebar_anim.finished.connect(self._sidebar_anim_done)
        self._sidebar_target_visible = settings.playlist_visible

        self._register_actions()
        self._connect_controller()
        if not settings.playlist_visible:
            self.sidebar.hide()
        if settings.window_geometry:
            self.restoreGeometry(QByteArray.fromBase64(settings.window_geometry.encode()))
        if settings.always_on_top:
            self.setWindowFlag(Qt.WindowStaysOnTopHint, True)

        self._timer = QTimer(self, interval=self.TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    # Hook for later feature pages (tracks, video...). Kept separate so the
    # popover can grow without touching the window setup.
    def extend_quick_settings(self, quick: QuickSettings) -> None:
        quick.add_page("audio", "Audio", AudioPage(self.ctl))
        quick.add_page("subtitles", "Subtitles", SubtitlesPage(self.ctl, self.load_subtitle_dialog))
        quick.add_page("video", "Video", VideoPage(self.ctl, self.take_snapshot))

    # ================================================================ actions
    def _register_actions(self) -> None:
        a = self.actions
        c = self.ctl
        s = self.settings
        a.register("open_file", self.open_file_dialog)
        a.register("open_folder", self.open_folder_dialog)
        a.register("open_url", self.open_url_dialog)
        a.register("add_files", lambda: self.open_file_dialog(play=False), "Add files…")
        a.register("add_folder", lambda: self.open_folder_dialog(play=False), "Add folder…")
        a.register("add_url", lambda: self.open_url_dialog(play=False), "Add URL…")
        a.register("quit", self.close)
        a.register("play_pause", c.toggle_play)
        a.register("stop", c.stop)
        a.register("next", c.next)
        a.register("previous", c.previous)
        a.register("frame_step", c.frame_step)
        for name, attr, sign in (("short", "seek_short_s", 1), ("medium", "seek_medium_s", 1),
                                 ("long", "seek_long_s", 1), ("very_long", "seek_very_long_s", 1)):
            a.register(f"jump_back_{name}", lambda at=attr: c.seek_relative(-getattr(s, at) * 1000))
            a.register(f"jump_fwd_{name}", lambda at=attr: c.seek_relative(getattr(s, at) * 1000))
        a.register("goto_time", self.goto_time_dialog)
        a.register("faster", c.faster)
        a.register("slower", c.slower)
        a.register("normal_speed", c.normal_rate)
        a.register("ab_loop", c.toggle_ab)
        a.register("cycle_repeat", c.cycle_repeat)
        a.register("toggle_shuffle", c.toggle_shuffle)
        a.register("volume_up", lambda: c.change_volume(+1))
        a.register("volume_down", lambda: c.change_volume(-1))
        a.register("mute", c.toggle_mute)
        a.register("fullscreen", self.toggle_fullscreen)
        a.register("leave_fullscreen", self.leave_fullscreen)
        a.register("toggle_playlist", self.toggle_playlist)
        a.register("quick_settings", lambda: self.open_quick_settings())
        a.register("quick_settings_speed", lambda: self.open_quick_settings("playback", anchor="speed"),
                   "Speed")
        a.register("main_menu", self._show_main_menu, "Menu")
        a.register("show_time", self._show_time)
        a.register("clear_playlist", self._clear_playlist, "Clear playlist")
        a.register("save_playlist", self.save_playlist_dialog)
        a.register("load_playlist", self.load_playlist_dialog, "Load playlist…")
        a.register("always_on_top", self.toggle_always_on_top, "Always on top", checkable=True)
        a.get("always_on_top").setChecked(s.always_on_top)
        self.register_feature_actions()

    def register_feature_actions(self) -> None:
        """Tracks, delays and video adjustments."""
        a = self.actions
        c = self.ctl
        a.register("load_subtitle", self.load_subtitle_dialog, "Load subtitle file…")
        a.register("cycle_audio_track", c.cycle_audio_track)
        a.register("audio_delay_down", lambda: c.change_audio_delay(-1))
        a.register("audio_delay_up", lambda: c.change_audio_delay(+1))
        a.register("cycle_subtitle", c.cycle_subtitle)
        a.register("toggle_subtitles", c.toggle_subtitles)
        a.register("subtitle_delay_down", lambda: c.change_subtitle_delay(-1))
        a.register("subtitle_delay_up", lambda: c.change_subtitle_delay(+1))
        a.register("cycle_aspect", c.cycle_aspect_ratio)
        a.register("cycle_crop", c.cycle_crop)
        a.register("cycle_zoom", c.cycle_zoom)
        a.register("cycle_deinterlace", c.cycle_deinterlace)
        a.register("rotate", c.rotate)
        a.register("snapshot", self.take_snapshot)

    # ============================================================ controller
    def _connect_controller(self) -> None:
        c = self.ctl
        ov = self.overlay
        c.state_changed.connect(self._on_state)
        c.media_changed.connect(self._on_media)
        c.position_changed.connect(lambda t, l: None if ov.controls.seek.is_dragging else ov.controls.set_time(t, l))
        c.volume_changed.connect(ov.update_volume)
        c.rate_changed.connect(ov.update_rate)
        c.repeat_changed.connect(ov.update_repeat)
        c.shuffle_changed.connect(ov.update_shuffle)
        c.ab_changed.connect(ov.update_ab)
        c.osd.connect(self._osd)
        c.error.connect(lambda msg: ov.flash(f"⚠  {msg}"))
        c.resumed.connect(lambda pos: ov.toast.show_message(
            f"Resumed at {format_ms(pos)}", "Start over", lambda: c.seek(0)))
        c.tracks_changed.connect(self._update_media_kind)
        ov.update_volume(c.volume, c.muted)
        ov.update_rate(c.rate)
        ov.update_repeat(c.playlist.repeat)
        ov.update_shuffle(c.playlist.shuffle)

    def _osd(self, text: str) -> None:
        if self.settings.show_osd:
            self.overlay.flash(text)

    def _on_state(self, state: PlaybackState) -> None:
        self.overlay.update_state(state)
        idle = state in (PlaybackState.STOPPED, PlaybackState.IDLE, PlaybackState.ERROR)
        if idle:
            self.overlay.show_welcome(True)
            self.overlay.controls.set_time(0, -1)
        elif state is PlaybackState.PLAYING:
            self._update_media_kind()

    def _on_media(self, item) -> None:
        if item is None:
            self.setWindowTitle(APP_NAME)
            self.overlay.set_title("")
            return
        self.setWindowTitle(f"{item.title} — {APP_NAME}")
        self.overlay.set_title(item.title)
        self.overlay.show_welcome(False)

    def _update_media_kind(self) -> None:
        if not self.ctl.has_media:
            return
        info = self.ctl.backend.media_info()
        streams = info.streams
        video = next((s for s in streams if s.kind == "video"), None)
        item = self.ctl.playlist.current
        if video and video.width:
            self.overlay.set_title(item.title if item else "", f"{video.width}×{video.height}")
        if streams and video is None:
            meta = info.meta
            subtitle = " — ".join(x for x in (meta.get("Artist", ""), meta.get("Album", "")) if x)
            self.overlay.show_audio_card(meta.get("Title") or (item.title if item else ""), subtitle)
        elif self.ctl.state in (PlaybackState.PLAYING, PlaybackState.PAUSED):
            self.overlay.show_welcome(False)

    def _tick(self) -> None:
        self.ctl.tick()
        self._sync_overlay()

    # =============================================================== overlay
    def _video_rect(self) -> QRect:
        return QRect(self.video.mapToGlobal(QPoint(0, 0)), self.video.size())

    def _sync_overlay(self) -> None:
        if not self.isVisible() or self.isMinimized():
            if self.overlay.isVisible():
                self.overlay.hide()
            return
        rect = self._video_rect()
        if self.overlay.geometry() != rect:
            self.overlay.setGeometry(rect)
        if not self.overlay.isVisible():
            self.overlay.show()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if not self._attached:
            self._attached = True
            self.ctl.backend.set_video_output(self.video.native_handle())
            self.take_focus()
        QTimer.singleShot(0, self._sync_overlay)

    def take_focus(self) -> None:
        """Move keyboard focus away from text fields so shortcuts work."""
        self.activateWindow()
        self.splitter.setFocus(Qt.OtherFocusReason)

    def moveEvent(self, e) -> None:
        super().moveEvent(e)
        self._sync_overlay()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._sync_overlay()

    def changeEvent(self, e) -> None:
        super().changeEvent(e)
        if e.type() == e.Type.WindowStateChange:
            QTimer.singleShot(0, self._sync_overlay)
            self.overlay.update_fullscreen(self.isFullScreen())

    def _video_clicked(self) -> None:
        if self.settings.pause_on_click and self.ctl.has_media:
            self.ctl.toggle_play()

    # =============================================================== sidebar
    def toggle_playlist(self) -> None:
        self.set_playlist_visible(not self._sidebar_target_visible)

    def set_playlist_visible(self, visible: bool) -> None:
        if self.isFullScreen():
            self._fullscreen_restore_sidebar = visible
            return
        self._sidebar_target_visible = visible
        self.settings.playlist_visible = visible
        start = self.sidebar.width() if self.sidebar.isVisible() else 0
        end = self.settings.playlist_width if visible else 0
        if visible:
            self.sidebar.setMinimumWidth(0)
            self.sidebar.show()
            self._apply_sidebar_width(start)
        else:
            self.sidebar.setMinimumWidth(0)
        self._sidebar_anim.stop()
        self._sidebar_anim.setStartValue(start)
        self._sidebar_anim.setEndValue(end)
        self._sidebar_anim.start()

    def _apply_sidebar_width(self, width) -> None:
        total = sum(self.splitter.sizes())
        width = int(width)
        self.splitter.setSizes([max(0, total - width), width])
        self._sync_overlay()

    def _sidebar_anim_done(self) -> None:
        if not self._sidebar_target_visible:
            self.sidebar.hide()
        self.sidebar.setMinimumWidth(220 if self._sidebar_target_visible else 0)
        self._sync_overlay()

    def _sidebar_resized(self, *_):
        if self.sidebar.isVisible() and self._sidebar_anim.state() != QVariantAnimation.Running:
            self.settings.playlist_width = self.sidebar.width()
        self._sync_overlay()

    # ============================================================ fullscreen
    def toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.leave_fullscreen()
        else:
            self._was_maximized = self.isMaximized()
            self._fullscreen_restore_sidebar = self.sidebar.isVisible()
            self.sidebar.hide()
            self.showFullScreen()
        self.overlay.update_fullscreen(self.isFullScreen())

    def leave_fullscreen(self) -> None:
        # Esc is global; let it close an open popover/menu first, like a browser.
        popup = QApplication.activePopupWidget()
        if popup is not None:
            popup.close()
            return
        if not self.isFullScreen():
            return
        if self._was_maximized:
            self.showMaximized()
        else:
            self.showNormal()
        if self._fullscreen_restore_sidebar:
            self.sidebar.show()
            self._apply_sidebar_width(self.settings.playlist_width)
        self.overlay.update_fullscreen(False)

    def toggle_always_on_top(self) -> None:
        on = not self.settings.always_on_top
        self.settings.always_on_top = on
        self.setWindowFlag(Qt.WindowStaysOnTopHint, on)
        self.show()
        self.actions.get("always_on_top").setChecked(on)

    # =============================================================== opening
    def open_inputs(self, inputs: list[str], play: bool = True) -> None:
        if not inputs:
            return
        if self.ctl.has_media and all(is_subtitle_file(i) for i in inputs):
            for path in inputs:  # dropping .srt/.ass on a playing video loads it, like VLC
                self.ctl.load_subtitle(path)
            return
        added = self.ctl.open(inputs, play=play)
        if not added:
            self.overlay.flash("Nothing playable found")
        elif not play:
            self.overlay.flash(f"Added {added} item{'s' if added != 1 else ''}")

    def _start_dir(self) -> str:
        d = self.settings.last_open_dir
        return d if d and Path(d).is_dir() else str(Path.home())

    def open_file_dialog(self, play: bool = True) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Open media", self._start_dir(), MEDIA_FILTER)
        if files:
            self.settings.last_open_dir = str(Path(files[0]).parent)
            self.open_inputs(files, play)

    def open_folder_dialog(self, play: bool = True) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Open folder", self._start_dir())
        if folder:
            self.settings.last_open_dir = folder
            self.open_inputs([folder], play)

    def open_url_dialog(self, play: bool = True) -> None:
        dlg = OpenUrlDialog(self.settings.recent_urls, self)
        if dlg.exec() and dlg.url():
            url = dlg.url()
            recent = [u for u in self.settings.recent_urls if u != url]
            self.settings.recent_urls = [url] + recent[:9]
            self.open_inputs([url], play)

    def goto_time_dialog(self) -> None:
        if not self.ctl.has_media:
            return
        dlg = JumpToTimeDialog(self.ctl.time(), self.ctl.length(), self)
        if dlg.exec() and dlg.value() is not None:
            self.ctl.seek(dlg.value())

    def _show_time(self) -> None:
        if self.ctl.has_media:
            self.overlay.flash(f"{format_ms(self.ctl.time())} / {format_ms(self.ctl.length())}")

    # ============================================================== playlist
    def _clear_playlist(self) -> None:
        self.ctl.stop()
        self.ctl.playlist.clear()

    def save_playlist_dialog(self) -> None:
        if not len(self.ctl.playlist):
            self.overlay.flash("Playlist is empty")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save playlist", str(Path(self._start_dir()) / "playlist.m3u"),
                                              "M3U playlist (*.m3u);;M3U8 playlist (*.m3u8)")
        if path:
            if not Path(path).suffix:
                path += ".m3u"
            try:
                write_m3u(path, self.ctl.playlist.items)
                self.overlay.flash(f"Saved {Path(path).name}")
            except OSError as exc:
                QMessageBox.warning(self, "Save playlist", f"Could not save playlist:\n{exc}")

    def load_playlist_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load playlist", self._start_dir(),
                                              _filter("Playlists", PLAYLIST_EXTENSIONS))
        if path:
            self.ctl.open([path], play=False)

    # ============================================================ popovers
    def open_quick_settings(self, page: str | None = None, anchor: str = "quick_settings") -> None:
        if self.quick.isVisible():
            self.quick.hide()
            return
        self.overlay.pin("popover", True)
        self.quick.popup(self.overlay.anchor(anchor), page)

    def _show_main_menu(self) -> None:
        menu = self.build_menu()
        self.overlay.pin("menu", True)
        r = self.overlay.anchor("main_menu")
        size = menu.sizeHint()
        menu.exec(QPoint(r.right() - size.width(), r.top() - size.height() - 6))
        self.overlay.pin("menu", False)

    def _show_context_menu(self, pos: QPoint) -> None:
        menu = self.build_menu()
        self.overlay.pin("menu", True)
        menu.exec(pos)
        self.overlay.pin("menu", False)

    def build_menu(self) -> QMenu:
        a = self.actions.get
        c = self.ctl
        menu = QMenu(self)
        menu.addAction(icon("file"), "Open file…", self.open_file_dialog).setShortcut(a("open_file").shortcut())
        menu.addAction(icon("folder"), "Open folder…", self.open_folder_dialog).setShortcut(
            a("open_folder").shortcut())
        menu.addAction(icon("link"), "Open network stream…", self.open_url_dialog).setShortcut(
            a("open_url").shortcut())
        menu.addSeparator()

        playback = menu.addMenu("Playback")
        speed = playback.addMenu("Speed")
        group = QActionGroup(speed)
        for r in RATE_PRESETS:
            act = speed.addAction(f"{r:g}×", lambda r=r: c.set_rate(r))
            act.setCheckable(True)
            act.setChecked(abs(c.rate - r) < 1e-3)
            group.addAction(act)
        playback.addAction(a("ab_loop"))
        repeat = playback.addMenu("Repeat")
        rgroup = QActionGroup(repeat)
        for label, mode in (("Off", RepeatMode.NONE), ("All", RepeatMode.ALL), ("One", RepeatMode.ONE)):
            act = repeat.addAction(label, lambda m=mode: c.set_repeat(m))
            act.setCheckable(True)
            act.setChecked(c.playlist.repeat is mode)
            rgroup.addAction(act)
        shuffle = playback.addAction("Shuffle", c.toggle_shuffle)
        shuffle.setCheckable(True)
        shuffle.setChecked(c.playlist.shuffle)
        playback.addSeparator()
        playback.addAction(a("goto_time"))
        playback.addAction(a("frame_step"))
        playback.addAction(a("stop"))
        self.extend_menu(menu)
        menu.addSeparator()
        view = menu.addMenu("View")
        view.addAction(a("toggle_playlist"))
        view.addAction(a("fullscreen"))
        view.addAction(a("always_on_top"))
        menu.addSeparator()
        menu.addAction(a("quit"))
        return menu

    def extend_menu(self, menu: QMenu) -> None:
        """Audio, subtitle and video submenus (rebuilt each time, so tracks are current)."""
        a = self.actions.get
        c = self.ctl

        def radio(parent: QMenu, items, current, setter) -> None:
            group = QActionGroup(parent)
            for label, value in items:
                act = parent.addAction(label, lambda v=value: setter(v))
                act.setCheckable(True)
                act.setChecked(value == current)
                group.addAction(act)

        audio = menu.addMenu("Audio")
        tracks = audio.addMenu("Audio track")
        radio(tracks, [("Off" if t.id == -1 else t.name, t.id) for t in c.audio_tracks()],
              c.audio_track(), c.set_audio_track)
        tracks.setEnabled(bool(c.audio_tracks()))
        audio.addAction(a("cycle_audio_track"))
        audio.addSeparator()
        audio.addAction(a("volume_up"))
        audio.addAction(a("volume_down"))
        audio.addAction(a("mute"))
        audio.addSeparator()
        audio.addAction(a("audio_delay_down"))
        audio.addAction(a("audio_delay_up"))

        subs = menu.addMenu("Subtitles")
        subs.addAction(a("load_subtitle"))
        tracks = subs.addMenu("Subtitle track")
        radio(tracks, [("Off" if t.id == -1 else t.name, t.id) for t in c.subtitle_tracks()],
              c.subtitle_track(), c.set_subtitle_track)
        tracks.setEnabled(bool(c.subtitle_tracks()))
        subs.addAction(a("toggle_subtitles"))
        subs.addSeparator()
        subs.addAction(a("subtitle_delay_down"))
        subs.addAction(a("subtitle_delay_up"))

        video = menu.addMenu("Video")
        radio(video.addMenu("Aspect ratio"), [(r or "Default", r) for r in ASPECT_RATIOS],
              c.video.aspect_ratio, c.set_aspect_ratio)
        radio(video.addMenu("Crop"), [(r or "None", r) for r in CROP_RATIOS], c.video.crop, c.set_crop)
        radio(video.addMenu("Zoom"), [(zoom_label(z), z) for z in ZOOM_LEVELS], c.video.zoom, c.set_zoom)
        radio(video.addMenu("Rotate"), [(f"{r}°", r) for r in ROTATIONS], c.video.rotation, c.set_rotation)
        radio(video.addMenu("Deinterlace"), [(m or "Off", m) for m in DEINTERLACE_MODES],
              c.video.deinterlace, c.set_deinterlace)
        video.addSeparator()
        video.addAction(a("snapshot"))
        video.addAction("Reset video adjustments", c.reset_video)

    def load_subtitle_dialog(self) -> None:
        if not self.ctl.has_media:
            self.overlay.flash("Play something first")
            return
        start = self._start_dir()
        local = mrl_to_path(self.ctl.playlist.current.mrl) if self.ctl.playlist.current else None
        if local:
            start = str(Path(local).parent)
        path, _ = QFileDialog.getOpenFileName(self, "Load subtitle", start, SUBTITLE_FILTER)
        if path:
            self.ctl.load_subtitle(path)

    def take_snapshot(self) -> None:
        if self.ctl.snapshot() is None and not self.ctl.has_media:
            self.overlay.flash("Nothing to snapshot")

    # ============================================================ shutdown
    def closeEvent(self, e: QCloseEvent) -> None:
        if self.isFullScreen():
            self.showNormal()
        self.settings.window_geometry = bytes(self.saveGeometry().toBase64()).decode()
        self.overlay.hide()
        self.quick.hide()
        super().closeEvent(e)

    # Drops onto the window chrome / sidebar area outside the overlay
    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasUrls() or e.mimeData().hasText():
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:
        from .overlay import mime_to_inputs
        self.open_inputs(mime_to_inputs(e.mimeData()))
