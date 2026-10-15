"""PlayerController: all player behaviour, independent of the GUI.

The UI calls methods here and listens to the ``Signal`` attributes. The
controller is driven by :meth:`tick`, which the UI calls from a timer on
its main thread (polling keeps libVLC's callback threads out of the UI).
"""

from __future__ import annotations

import os
import time as _time
from pathlib import Path

from .abloop import ABLoop, ABState
from .backend import MediaBackend, PlaybackState, Track, VideoOptions
from .events import Signal
from .m3u import read_m3u
from .media_sources import expand_inputs, is_playlist_file, mrl_to_path, path_to_mrl
from .playlist import Playlist, PlaylistItem, RepeatMode
from .resume import ResumeStore
from .settings import Settings
from .stepping import MAX_VOLUME, clamp, cycle, fine_step_rate, step_rate, step_volume
from .timefmt import format_delay, format_ms

ASPECT_RATIOS: list[str | None] = [None, "16:9", "4:3", "1:1", "16:10", "2.21:1", "2.35:1", "2.39:1", "5:4"]
CROP_RATIOS: list[str | None] = [None, "16:9", "4:3", "1:1", "16:10", "2.21:1", "2.35:1", "2.39:1", "5:4"]
ZOOM_LEVELS: list[float] = [0.0, 0.25, 0.5, 1.0, 2.0]
ROTATIONS: list[int] = [0, 90, 180, 270]
DEINTERLACE_MODES: list[str | None] = [
    None, "blend", "bob", "discard", "linear", "mean", "x", "yadif", "yadif2x", "phosphor", "ivtc",
]
DELAY_STEP_MS = 50
ACTIVE_STATES = (PlaybackState.PLAYING, PlaybackState.PAUSED, PlaybackState.BUFFERING, PlaybackState.OPENING)


def zoom_label(z: float) -> str:
    return "Fit" if not z else f"{z:g}×"


class PlayerController:
    RESUME_SAVE_INTERVAL_S = 10.0

    def __init__(self, backend: MediaBackend, settings: Settings | None = None,
                 resume_store: ResumeStore | None = None) -> None:
        self.backend = backend
        self.settings = settings or Settings()
        self.resume = resume_store
        self.playlist = Playlist()
        self.playlist.repeat = RepeatMode(self.settings.repeat)
        self.playlist.shuffle = self.settings.shuffle
        self.ab = ABLoop()

        self.volume = self.settings.volume if self.settings.remember_volume else 100
        self.muted = False
        self.rate = 1.0
        self.audio_delay = 0
        self.subtitle_delay = 0
        self.video = VideoOptions()
        self._last_subtitle_track: int | None = None

        self._state = PlaybackState.IDLE
        self._started = False  # per-media settings applied after PLAYING
        self._pending_seek: int | None = None
        self._last_resume_save = _time.monotonic()
        self._error_streak = 0
        self._known_tracks: tuple = ()
        self._pause_after_start = False

        # Signals (UI subscribes)
        self.state_changed = Signal()      # (PlaybackState)
        self.media_changed = Signal()      # (PlaylistItem | None)
        self.position_changed = Signal()   # (time_ms, length_ms)
        self.volume_changed = Signal()     # (volume, muted)
        self.rate_changed = Signal()       # (rate)
        self.repeat_changed = Signal()     # (RepeatMode)
        self.shuffle_changed = Signal()    # (bool)
        self.ab_changed = Signal()         # (ABState, a, b)
        self.tracks_changed = Signal()     # ()
        self.delays_changed = Signal()     # (audio_ms, subtitle_ms)
        self.video_changed = Signal()      # (VideoOptions)
        self.resumed = Signal()            # (position_ms)
        self.osd = Signal()                # (text)
        self.error = Signal()              # (text)

    # =============================================================== state
    @property
    def state(self) -> PlaybackState:
        return self._state

    @property
    def is_playing(self) -> bool:
        return self._state is PlaybackState.PLAYING

    @property
    def has_media(self) -> bool:
        return self.playlist.current is not None and self._state in ACTIVE_STATES

    def time(self) -> int:
        return self.backend.time()

    def length(self) -> int:
        return self.backend.length()

    # ============================================================== opening
    def open(self, inputs: list[str], play: bool | None = None, at: int | None = None) -> int:
        """Add files / folders / URLs / playlists; optionally start the first.

        Returns the number of items added.
        """
        play = self.settings.play_on_open if play is None else play
        items: list[PlaylistItem | str] = []
        for mrl in expand_inputs(inputs, self.settings.recursive_folders):
            local = mrl_to_path(mrl)
            if local and is_playlist_file(local) and os.path.isfile(local):
                try:
                    items.extend(read_m3u(local))
                except OSError as exc:
                    self.error.emit(f"Could not read playlist: {exc}")
            else:
                items.append(mrl)
        indexes = self.playlist.add(items, at=at)
        if indexes and play:
            self.play_index(indexes[0])
        return len(indexes)

    def load_playlist(self, path: str, play: bool = False) -> int:
        self.playlist.clear()
        return self.open([path], play=play)

    # ============================================================ transport
    def play_index(self, index: int) -> None:
        if not 0 <= index < len(self.playlist):
            return
        self._remember_position()
        self.playlist.set_current(index)
        self._start_current()

    def _start_current(self, seek_to: int | None = None) -> None:
        item = self.playlist.current
        if item is None:
            return
        options = self.backend.rotation_options(self.video.rotation)
        self.backend.load(item.mrl, options)
        self.ab.clear()
        self.ab_changed.emit(self.ab.state, None, None)
        self.audio_delay = 0
        self.subtitle_delay = 0
        self._started = False
        self._known_tracks = ()
        self._pending_seek = seek_to
        if seek_to is None and self.settings.resume_playback and self.resume:
            pos = self.resume.get(item.mrl)
            if pos:
                self._pending_seek = pos
                self.resumed.emit(pos)
        self.backend.play()
        self._set_state(PlaybackState.OPENING)
        self.media_changed.emit(item)
        self.delays_changed.emit(0, 0)

    def play(self) -> None:
        st = self.backend.state()
        if st is PlaybackState.PAUSED:
            self.backend.play()
        elif st in (PlaybackState.PLAYING, PlaybackState.OPENING, PlaybackState.BUFFERING):
            return
        elif self.playlist.current is not None:
            self._start_current()
        elif len(self.playlist):
            self.play_index(self.playlist.peek_next())

    def pause(self) -> None:
        if self.backend.state() is PlaybackState.PLAYING:
            self.backend.pause()
            self._remember_position()

    def toggle_play(self) -> None:
        if self.backend.state() is PlaybackState.PLAYING:
            self.pause()
            self.osd.emit("Paused")
        else:
            self.play()

    def stop(self) -> None:
        self._remember_position()
        self.backend.stop()
        self._pending_seek = None
        self.ab.clear()
        self.ab_changed.emit(self.ab.state, None, None)
        self._set_state(PlaybackState.STOPPED)
        self.position_changed.emit(0, self.backend.length())

    def next(self) -> None:
        self._remember_position()
        if self.playlist.next() is not None:
            self._start_current()

    def previous(self) -> None:
        self._remember_position()
        if self.playlist.previous() is not None:
            self._start_current()

    def frame_step(self) -> None:
        if self.has_media:
            self.backend.next_frame()
            self.osd.emit("Next frame")

    # ================================================================ seeking
    def seek(self, ms: int) -> None:
        length = self.backend.length()
        if not self.has_media or not self.backend.is_seekable():
            return
        if length > 0:
            ms = int(clamp(ms, 0, max(0, length - 500)))
        self.backend.set_time(max(0, int(ms)))
        self.position_changed.emit(int(ms), length)

    def seek_relative(self, delta_ms: int) -> None:
        if not self.has_media:
            return
        self.seek(self.backend.time() + delta_ms)
        arrow = "+" if delta_ms >= 0 else "-"
        self.osd.emit(f"{arrow}{format_ms(abs(delta_ms))}   {format_ms(self.backend.time())} / "
                      f"{format_ms(self.backend.length())}")

    def seek_fraction(self, fraction: float) -> None:
        length = self.backend.length()
        if length > 0:
            self.seek(int(length * clamp(fraction, 0.0, 1.0)))

    # ================================================================== audio
    @property
    def max_volume(self) -> int:
        return min(MAX_VOLUME, self.settings.max_volume)

    def set_volume(self, volume: int, show: bool = True) -> None:
        self.volume = int(clamp(volume, 0, self.max_volume))
        self.backend.set_volume(self.volume)
        if self.muted and volume > 0:
            self.muted = False
            self.backend.set_mute(False)
        self.settings.volume = self.volume
        self.volume_changed.emit(self.volume, self.muted)
        if show:
            self.osd.emit(f"Volume {self.volume}%")

    def change_volume(self, direction: int) -> None:
        self.set_volume(step_volume(self.volume, direction, self.settings.volume_step, self.max_volume))

    def set_muted(self, muted: bool) -> None:
        self.muted = muted
        self.backend.set_mute(muted)
        self.volume_changed.emit(self.volume, self.muted)
        self.osd.emit("Muted" if muted else f"Volume {self.volume}%")

    def toggle_mute(self) -> None:
        self.set_muted(not self.muted)

    # =================================================================== rate
    def set_rate(self, rate: float) -> None:
        self.rate = round(float(clamp(rate, 0.03125, 32.0)), 3)
        self.backend.set_rate(self.rate)
        self.rate_changed.emit(self.rate)
        self.osd.emit(f"Speed {self.rate:g}×")

    def faster(self) -> None:
        self.set_rate(step_rate(self.rate, +1))

    def slower(self) -> None:
        self.set_rate(step_rate(self.rate, -1))

    def faster_fine(self) -> None:
        self.set_rate(fine_step_rate(self.rate, +1))

    def slower_fine(self) -> None:
        self.set_rate(fine_step_rate(self.rate, -1))

    def normal_rate(self) -> None:
        self.set_rate(1.0)

    # ======================================================== loop / repeat
    def toggle_ab(self) -> ABState:
        if not self.has_media:
            return self.ab.state
        state = self.ab.toggle(self.backend.time())
        self.ab_changed.emit(state, self.ab.a, self.ab.b)
        if state is ABState.A_SET:
            self.osd.emit(f"A-B loop: A = {format_ms(self.ab.a)}")
        elif state is ABState.ACTIVE:
            self.osd.emit(f"A-B loop: {format_ms(self.ab.a)} → {format_ms(self.ab.b)}")
        else:
            self.osd.emit("A-B loop off")
        return state

    def clear_ab(self) -> None:
        self.ab.clear()
        self.ab_changed.emit(self.ab.state, None, None)

    def set_repeat(self, mode: RepeatMode) -> None:
        self.playlist.repeat = mode
        self.settings.repeat = mode.value
        self.repeat_changed.emit(mode)
        labels = {RepeatMode.NONE: "Repeat off", RepeatMode.ALL: "Repeat all", RepeatMode.ONE: "Repeat one"}
        self.osd.emit(labels[mode])

    def cycle_repeat(self) -> None:
        self.set_repeat(self.playlist.repeat.next())

    def set_shuffle(self, enabled: bool) -> None:
        self.playlist.shuffle = enabled
        self.settings.shuffle = enabled
        self.shuffle_changed.emit(enabled)
        self.osd.emit("Shuffle on" if enabled else "Shuffle off")

    def toggle_shuffle(self) -> None:
        self.set_shuffle(not self.playlist.shuffle)

    # ================================================================= tracks
    def audio_tracks(self) -> list[Track]:
        return self.backend.audio_tracks() if self.has_media else []

    def audio_track(self) -> int:
        return self.backend.audio_track()

    def set_audio_track(self, track_id: int) -> None:
        self.backend.set_audio_track(track_id)
        self.tracks_changed.emit()
        name = next((t.name for t in self.backend.audio_tracks() if t.id == track_id), str(track_id))
        self.osd.emit(f"Audio: {name}")

    def cycle_audio_track(self) -> None:
        tracks = [t.id for t in self.audio_tracks() if t.id != -1]
        if tracks:
            self.set_audio_track(cycle(tracks, self.audio_track()))

    def subtitle_tracks(self) -> list[Track]:
        return self.backend.subtitle_tracks() if self.has_media else []

    def subtitle_track(self) -> int:
        return self.backend.subtitle_track()

    def set_subtitle_track(self, track_id: int) -> None:
        self.backend.set_subtitle_track(track_id)
        if track_id != -1:
            self._last_subtitle_track = track_id
        self.tracks_changed.emit()
        name = next((t.name for t in self.backend.subtitle_tracks() if t.id == track_id), str(track_id))
        self.osd.emit("Subtitles off" if track_id == -1 else f"Subtitles: {name}")

    def cycle_subtitle(self) -> None:
        tracks = [t.id for t in self.subtitle_tracks()]
        if tracks:
            self.set_subtitle_track(cycle(tracks, self.subtitle_track()))

    def toggle_subtitles(self) -> None:
        tracks = [t.id for t in self.subtitle_tracks() if t.id != -1]
        if not tracks:
            return
        if self.subtitle_track() != -1:
            self.set_subtitle_track(-1)
        else:
            last = self._last_subtitle_track
            self.set_subtitle_track(last if last in tracks else tracks[0])

    def load_subtitle(self, path: str) -> bool:
        if not self.has_media:
            return False
        ok = self.backend.add_subtitle_file(path_to_mrl(path))
        if ok:
            self.osd.emit(f"Subtitle loaded: {Path(path).name}")
            self.tracks_changed.emit()
        else:
            self.error.emit(f"Could not load subtitle {path}")
        return ok

    # ================================================================= delays
    def set_audio_delay(self, ms: int) -> None:
        self.audio_delay = int(ms)
        self.backend.set_audio_delay(self.audio_delay)
        self.delays_changed.emit(self.audio_delay, self.subtitle_delay)
        self.osd.emit(f"Audio delay {format_delay(self.audio_delay)}")

    def change_audio_delay(self, direction: int) -> None:
        self.set_audio_delay(self.audio_delay + direction * DELAY_STEP_MS)

    def set_subtitle_delay(self, ms: int) -> None:
        self.subtitle_delay = int(ms)
        self.backend.set_subtitle_delay(self.subtitle_delay)
        self.delays_changed.emit(self.audio_delay, self.subtitle_delay)
        self.osd.emit(f"Subtitle delay {format_delay(self.subtitle_delay)}")

    def change_subtitle_delay(self, direction: int) -> None:
        self.set_subtitle_delay(self.subtitle_delay + direction * DELAY_STEP_MS)

    # ================================================================== video
    def set_aspect_ratio(self, ratio: str | None) -> None:
        self.video.aspect_ratio = ratio
        self.backend.set_aspect_ratio(ratio)
        self.video_changed.emit(self.video)
        self.osd.emit(f"Aspect ratio: {ratio or 'Default'}")

    def cycle_aspect_ratio(self) -> None:
        self.set_aspect_ratio(cycle(ASPECT_RATIOS, self.video.aspect_ratio))

    def set_crop(self, geometry: str | None) -> None:
        self.video.crop = geometry
        self.backend.set_crop(geometry)
        self.video_changed.emit(self.video)
        self.osd.emit(f"Crop: {geometry or 'None'}")

    def cycle_crop(self) -> None:
        self.set_crop(cycle(CROP_RATIOS, self.video.crop))

    def set_zoom(self, factor: float) -> None:
        self.video.zoom = float(factor)
        self.backend.set_zoom(self.video.zoom)
        self.video_changed.emit(self.video)
        self.osd.emit(f"Zoom: {zoom_label(self.video.zoom)}")

    def cycle_zoom(self) -> None:
        self.set_zoom(cycle(ZOOM_LEVELS, self.video.zoom))

    def set_deinterlace(self, mode: str | None) -> None:
        self.video.deinterlace = mode
        self.backend.set_deinterlace(mode)
        self.video_changed.emit(self.video)
        self.osd.emit(f"Deinterlace: {mode or 'Off'}")

    def cycle_deinterlace(self) -> None:
        self.set_deinterlace(cycle(DEINTERLACE_MODES, self.video.deinterlace))

    def set_rotation(self, degrees: int) -> None:
        degrees = int(degrees) % 360
        if degrees == self.video.rotation:
            return
        self.video.rotation = degrees
        self.video_changed.emit(self.video)
        self.osd.emit(f"Rotation: {degrees}°")
        if self.has_media:
            # libVLC applies the transform filter at load time: reload in place.
            was_paused = self.backend.state() is PlaybackState.PAUSED
            position = self.backend.time()
            self._start_current(seek_to=max(0, position))
            if was_paused:
                self._pause_after_start = True

    def rotate(self) -> None:
        self.set_rotation(cycle(ROTATIONS, self.video.rotation))

    def reset_video(self) -> None:
        rotation = self.video.rotation
        self.video = VideoOptions(rotation=rotation)
        self._apply_video_options()
        self.video_changed.emit(self.video)
        if rotation:
            self.set_rotation(0)

    def _apply_video_options(self) -> None:
        self.backend.set_aspect_ratio(self.video.aspect_ratio)
        self.backend.set_crop(self.video.crop)
        self.backend.set_zoom(self.video.zoom)
        self.backend.set_deinterlace(self.video.deinterlace)

    def snapshot(self) -> str | None:
        if not self.has_media or not self.backend.has_video():
            return None
        folder = Path(self.settings.snapshot_dir or ".")
        folder.mkdir(parents=True, exist_ok=True)
        item = self.playlist.current
        stem = Path(item.title).stem if item else "snapshot"
        stem = "".join(c for c in stem if c not in '<>:"/\\|?*').strip() or "snapshot"
        stamp = format_ms(self.backend.time(), force_hours=True).replace(":", "-")
        path = folder / f"{stem}_{stamp}.{self.settings.snapshot_format}"
        n = 1
        while path.exists():
            path = folder / f"{stem}_{stamp}_{n}.{self.settings.snapshot_format}"
            n += 1
        if self.backend.take_snapshot(str(path)):
            self.osd.emit(f"Snapshot saved: {path.name}")
            return str(path)
        self.error.emit("Snapshot failed")
        return None

    # =================================================================== loop
    def tick(self) -> None:
        """Poll the backend; call ~5-10 times a second from the UI thread."""
        st = self.backend.state()
        if st is PlaybackState.PLAYING and not self._started:
            self._on_started()
        if st is PlaybackState.ENDED:
            self._set_state(st)
            self._on_ended()
            return
        if st is PlaybackState.ERROR:
            self._set_state(st)
            self._on_error()
            return
        self._set_state(st)
        if st in (PlaybackState.PLAYING, PlaybackState.PAUSED):
            t = self.backend.time()
            target = self.ab.check(t)
            if target is not None:
                self.backend.set_time(target)
                t = target
            self.position_changed.emit(t, self.backend.length())
            self._poll_tracks()
            if st is PlaybackState.PLAYING:
                self._remember_position(save=False)
                if _time.monotonic() - self._last_resume_save > self.RESUME_SAVE_INTERVAL_S:
                    self._save_resume()

    def _on_started(self) -> None:
        self._started = True
        self._error_streak = 0
        self.backend.set_volume(self.volume)
        self.backend.set_mute(self.muted)
        if abs(self.rate - 1.0) > 1e-6:
            self.backend.set_rate(self.rate)
        self._apply_video_options()
        if self._pending_seek:
            self.backend.set_time(self._pending_seek)
        self._pending_seek = None
        if self._pause_after_start:
            self._pause_after_start = False
            self.backend.pause()
        index = self.playlist.current_index
        length = self.backend.length()
        if index >= 0 and length > 0 and self.playlist[index].duration_ms != length:
            self.playlist.set_duration(index, length)
        info = self.backend.media_info()
        title = info.meta.get("Title", "")
        current = self.playlist.current
        if current and title and "://" not in title and title != current.title and not mrl_to_path(current.mrl):
            # Streams often carry a nicer title than their URL.
            self.playlist.set_title(index, title)
        self.tracks_changed.emit()

    def _poll_tracks(self) -> None:
        signature = (tuple((t.id, t.name) for t in self.backend.audio_tracks()),
                     tuple((t.id, t.name) for t in self.backend.subtitle_tracks()))
        if signature != self._known_tracks:
            self._known_tracks = signature
            self.tracks_changed.emit()

    def _on_ended(self) -> None:
        item = self.playlist.current
        if item and self.resume:
            self.resume.forget(item.mrl)
        nxt = self.playlist.next(auto=True)
        if nxt is not None:
            self._start_current()
        else:
            self.backend.stop()
            self._set_state(PlaybackState.STOPPED)
            self.position_changed.emit(0, -1)

    def _on_error(self) -> None:
        item = self.playlist.current
        self.error.emit(f"Could not play {item.title if item else 'media'}")
        self._error_streak += 1
        self.backend.stop()
        if self._error_streak < len(self.playlist) and self.playlist.peek_next() not in (-1, self.playlist.current_index):
            self.playlist.next()
            self._start_current()
        else:
            self._error_streak = 0
            self._set_state(PlaybackState.STOPPED)

    def _set_state(self, st: PlaybackState) -> None:
        if st is not self._state:
            self._state = st
            self.state_changed.emit(st)

    # ================================================================ resume
    def _remember_position(self, save: bool = True) -> None:
        item = self.playlist.current
        if not (self.resume and item and self._started):
            return
        if self.backend.state() in (PlaybackState.PLAYING, PlaybackState.PAUSED):
            self.resume.update(item.mrl, self.backend.time(), self.backend.length())
        if save:
            self._save_resume()

    def _save_resume(self) -> None:
        self._last_resume_save = _time.monotonic()
        if self.resume:
            try:
                self.resume.save()
            except OSError:
                pass

    def shutdown(self) -> None:
        self._remember_position()
        self.backend.release()
