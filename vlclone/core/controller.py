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
from .backend import NAVIGATE_ACTIONS, Chapter, MediaBackend, PlaybackState, TitleInfo, Track, VideoOptions
from .effects import (ADJUST_RANGES, ASPECT_RATIOS, CROP_RATIOS, DEINTERLACE_MODES, EQ_BANDS, NORMALIZE_MAX,
                      NORMALIZE_MIN, ROTATIONS, SHARPEN_MAX, SHARPEN_MIN, STEREO_MODE_VALUES, STEREO_MODES,
                      ZOOM_LEVELS, AudioEffects, Debouncer, Effects, EffectsStore, EngineFilters, EqPresetStore,
                      Geometry, VideoEffects, clamp_db, clean_bands, engine_filters)
from .events import Signal
from .m3u import read_m3u
from .media_sources import expand_inputs, is_playlist_file, mrl_to_path, path_to_mrl
from .network import DEFAULT_NETWORK_CACHING_MS, caching_option, describe_open_error, is_network_mrl
from .playlist import Playlist, PlaylistItem, RepeatMode
from .resume import ResumeStore
from .settings import Settings
from .stepping import MAX_VOLUME, clamp, cycle, fine_step_rate, step_rate, step_volume
from .timefmt import format_delay, format_ms

__all__ = ["ASPECT_RATIOS", "CROP_RATIOS", "DEINTERLACE_MODES", "ROTATIONS", "ZOOM_LEVELS", "PlayerController",
           "zoom_label"]

DELAY_STEP_MS = 50
ACTIVE_STATES = (PlaybackState.PLAYING, PlaybackState.PAUSED, PlaybackState.BUFFERING, PlaybackState.OPENING)


def zoom_label(z: float) -> str:
    return "Fit" if not z else f"{z:g}×"


class PlayerController:
    RESUME_SAVE_INTERVAL_S = 10.0
    #: How often chapters/titles are re-read while playing (discs change them on the fly).
    CHAPTER_POLL_S = 1.0
    #: Within this much of a chapter's start, "previous chapter" goes to the one before.
    CHAPTER_RESTART_MS = 3000
    #: Quiet period before load-time filters (rotate, flip, sharpen) reload the media.
    RELOAD_DEBOUNCE_S = 0.6

    def __init__(self, backend: MediaBackend, settings: Settings | None = None,
                 resume_store: ResumeStore | None = None, effects_store: EffectsStore | None = None,
                 eq_presets: EqPresetStore | None = None, clock=_time.monotonic) -> None:
        self.backend = backend
        self.settings = settings or Settings()
        self.resume = resume_store
        self.effects_store = effects_store
        self.eq_preset_store = eq_presets
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
        self._clock = clock
        self._last_subtitle_track: int | None = None

        self._state = PlaybackState.IDLE
        self._started = False  # per-media settings applied after PLAYING
        self._pending_seek: int | None = None
        self._last_resume_save = _time.monotonic()
        self._error_streak = 0
        self._known_tracks: tuple = ()
        self._pause_after_start = False
        self._external_subtitles: list[str] = []
        self._restore_tracks: tuple[int, int] | None = None

        # Effects (phase 2): self.effects is what is applied now. In "global"
        # scope it mirrors settings.effects; in "per_file" scope it belongs to
        # the current file and new files start from the global set.
        self.effects = Effects.from_dict(self.settings.effects)
        self.reload_debounce = Debouncer(self.RELOAD_DEBOUNCE_S, clock)
        self._loaded_filters = EngineFilters()
        self._builtin_eq: dict[str, tuple[float, list[float]]] | None = None
        # Live effects the engine has been given a non-default value for. libVLC
        # keeps them on the player across media, so these must always be re-sent.
        self._live_touched: set[str] = set()
        self._load_geometry(emit=False)

        # Phase 3: chapters/titles, opening watchdog, playlist URLs.
        self.chapters: list[Chapter] = []
        self.titles: list[TitleInfo] = []
        self.current_title = -1
        self._chapter_poll = 0.0
        self._opening_since: float | None = None

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
        self.effects_changed = Signal()    # (Effects)
        self.reload_pending = Signal()     # (bool) load-time filters waiting to apply
        self.chapters_changed = Signal()   # (chapters, titles, current title)
        self.open_failed = Signal()        # (PlaylistItem, message) media could not be opened

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
    def open(self, inputs: list[str], play: bool | None = None, at: int | None = None,
             options: list[str] | None = None, title: str | None = None) -> int:
        """Add files / folders / URLs / playlists; optionally start the first.

        ``options`` are libVLC media options for every added item (caching,
        capture device, disc menus…); ``title`` names a single added item.
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
                items.append(PlaylistItem(mrl, options=list(options or [])))
        if title and len(items) == 1 and isinstance(items[0], PlaylistItem):
            items[0].title = title
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

    def _start_current(self, seek_to: int | None = None, reload: bool = False) -> None:
        """Load and play the current item.

        ``reload`` restarts the same media in place (needed for rotation) and
        keeps loop points, delays, external subtitles and track choices.
        """
        item = self.playlist.current
        if item is None:
            return
        if reload and self._started:
            self._restore_tracks = (self.backend.audio_track(), self.backend.subtitle_track())
        else:
            self._restore_tracks = None
            self._switch_effects_for(item.mrl)
        if self.reload_debounce.pending:
            self.reload_debounce.cancel()
            self.reload_pending.emit(False)
        self._loaded_filters = self.current_engine_filters()
        self.backend.set_engine_filters(self._loaded_filters)
        self.backend.load(item.mrl, self.media_options(item))
        self._opening_since = self._clock()
        if not reload:
            self._set_chapters([], [], -1)
        if not reload:
            self.ab.clear()
            self.ab_changed.emit(self.ab.state, None, None)
            self.audio_delay = 0
            self.subtitle_delay = 0
            self._external_subtitles = []
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
        if not reload:
            self.media_changed.emit(item)
            self.delays_changed.emit(0, 0)

    def media_options(self, item: PlaylistItem) -> list[str]:
        """libVLC options for ``item``: its own plus the default network caching."""
        options = list(item.options)
        caching = self.settings.network_caching_ms
        if (is_network_mrl(item.mrl) and caching != DEFAULT_NETWORK_CACHING_MS
                and not any(o.startswith(":network-caching=") for o in options)):
            options.append(caching_option(caching))
        return options

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
        mrl = path_to_mrl(path)
        ok = self.backend.add_subtitle_file(mrl)
        if ok:
            self._external_subtitles.append(mrl)
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
        self._geometry_edited()
        self.osd.emit(f"Aspect ratio: {ratio or 'Default'}")

    def cycle_aspect_ratio(self) -> None:
        self.set_aspect_ratio(cycle(ASPECT_RATIOS, self.video.aspect_ratio))

    def set_crop(self, geometry: str | None) -> None:
        self.video.crop = geometry
        self.backend.set_crop(geometry)
        self._geometry_edited()
        self.osd.emit(f"Crop: {geometry or 'None'}")

    def cycle_crop(self) -> None:
        self.set_crop(cycle(CROP_RATIOS, self.video.crop))

    def set_zoom(self, factor: float) -> None:
        self.video.zoom = float(factor)
        self.backend.set_zoom(self.video.zoom)
        self._geometry_edited()
        self.osd.emit(f"Zoom: {zoom_label(self.video.zoom)}")

    def cycle_zoom(self) -> None:
        self.set_zoom(cycle(ZOOM_LEVELS, self.video.zoom))

    def set_deinterlace(self, mode: str | None) -> None:
        self.video.deinterlace = mode
        self.backend.set_deinterlace(mode)
        self._geometry_edited()
        self.osd.emit(f"Deinterlace: {mode or 'Off'}")

    def cycle_deinterlace(self) -> None:
        self.set_deinterlace(cycle(DEINTERLACE_MODES, self.video.deinterlace))

    def set_rotation(self, degrees: int) -> None:
        degrees = int(degrees) % 360
        if degrees == self.video.rotation:
            return
        self.video.rotation = degrees
        self._geometry_edited()
        self.osd.emit(f"Rotation: {degrees}°")
        # libVLC applies the transform filter at load time: reload in place.
        self._request_reload()

    # ------------------------------------------- reload-with-position (filters)
    def current_engine_filters(self) -> EngineFilters:
        return engine_filters(self.video.rotation, self.effects.video)

    @property
    def reload_needed(self) -> bool:
        """Load-time filters differ from what the playing media was loaded with."""
        return self.has_media and self.current_engine_filters() != self._loaded_filters

    def _request_reload(self) -> None:
        """Batch load-time filter changes: one reload after a short quiet period."""
        if not self.has_media:
            return  # applied when the next media loads
        self.reload_debounce.request()
        self.reload_pending.emit(True)

    def apply_pending_reload(self) -> None:
        """Apply batched filter changes now instead of waiting for the debounce."""
        if self.reload_debounce.flush():
            self._reload_in_place()

    def _reload_in_place(self) -> None:
        self.reload_pending.emit(False)
        if not self.reload_needed:
            return  # e.g. flipped twice: nothing to do
        was_paused = self.backend.state() is PlaybackState.PAUSED
        position = self.backend.time()
        self._start_current(seek_to=max(0, position), reload=True)
        if was_paused:
            self._pause_after_start = True

    def rotate(self) -> None:
        self.set_rotation(cycle(ROTATIONS, self.video.rotation))

    def reset_video(self) -> None:
        rotation = self.video.rotation
        self.video = VideoOptions(rotation=rotation)
        self._apply_video_options()
        self._geometry_edited()
        if rotation:
            self.set_rotation(0)

    # Geometry persists with the effects (same global / per-file scope).
    def _geometry_edited(self) -> None:
        v = self.video
        self.effects.geometry = Geometry(v.aspect_ratio, v.crop, v.zoom, v.rotation, v.deinterlace)
        self._store_effects()
        self.video_changed.emit(self.video)

    def _load_geometry(self, emit: bool = True, apply: bool = False) -> None:
        """Take aspect/crop/zoom/rotation/deinterlace from the current effect set."""
        g = self.effects.geometry
        video = VideoOptions(g.aspect_ratio, g.crop, g.zoom, g.rotation, g.deinterlace)
        if video == self.video:
            return
        self.video = video
        if apply and self.has_media:
            self._apply_video_options()
        if emit:
            self.video_changed.emit(self.video)

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

    # ================================================================ effects
    # --- scope / persistence
    def _per_file(self) -> bool:
        return self.settings.effects_scope == "per_file" and self.effects_store is not None

    def _effects_owner(self) -> str | None:
        """MRL whose per-file effects are being edited, or None for the global set."""
        item = self.playlist.current
        return item.mrl if self._per_file() and item is not None else None

    def _switch_effects_for(self, mrl: str) -> None:
        if not self._per_file():
            return
        fx = self.effects_store.get(mrl) or Effects.from_dict(self.settings.effects)
        if fx.to_dict() != self.effects.to_dict():
            self.effects = fx
            self.effects_changed.emit(self.effects)
            self._load_geometry()

    def _store_effects(self) -> None:
        owner = self._effects_owner()
        if owner is not None:
            self.effects_store.put(owner, self.effects)
        else:
            self.settings.effects = self.effects.to_dict()

    def set_effects_scope(self, scope: str) -> None:
        """Switch between one global effect set and per-file effects."""
        if scope not in ("global", "per_file") or scope == self.settings.effects_scope:
            return
        if scope == "per_file":
            self.settings.effects = self.effects.to_dict()  # current set becomes the default
            self.settings.effects_scope = scope
            return
        self.settings.effects_scope = scope
        self._replace_effects(Effects.from_dict(self.settings.effects))

    def _replace_effects(self, fx: Effects) -> None:
        self.effects = fx
        self._apply_live_effects()
        self.effects_changed.emit(self.effects)
        self._load_geometry(apply=True)
        if self.reload_needed:
            self._request_reload()

    def _effects_edited(self, reload: bool = False, osd: str | None = None) -> None:
        self._store_effects()
        self.effects_changed.emit(self.effects)
        if osd:
            self.osd.emit(osd)
        if reload:
            self._request_reload()

    def _apply_live_effects(self, initial: bool = False) -> None:
        """Send all live effects to the engine.

        ``initial`` (media just started) skips features still at their default
        that the engine has never been changed for: in particular forcing
        "stereo" on every file would downmix surround audio.
        """
        a, v = self.effects.audio, self.effects.video
        if a.eq_enabled:
            self._live_touched.add("eq")
        if a.stereo_mode != "stereo":
            self._live_touched.add("stereo")
        if a.normalize:
            self._live_touched.add("normalize")
        if v.adjust_active:
            self._live_touched.add("adjust")

        def send(feature: str) -> bool:
            return not initial or feature in self._live_touched

        if send("eq"):
            self._push_equalizer()
        if send("stereo"):
            self.backend.set_stereo_mode(STEREO_MODE_VALUES[a.stereo_mode])
        if send("normalize") and self.backend.supports_normalization:
            self.backend.set_normalization(a.normalize, a.normalize_level)
        if send("adjust"):
            self.backend.set_video_adjust(v.adjust_values() if v.adjust_active else None)

    # --- equalizer (live)
    def builtin_eq_presets(self) -> dict[str, tuple[float, list[float]]]:
        if self._builtin_eq is None:
            self._builtin_eq = self.backend.equalizer_presets()
        return self._builtin_eq

    def custom_eq_presets(self) -> list[str]:
        return self.eq_preset_store.names() if self.eq_preset_store else []

    def _push_equalizer(self) -> None:
        a = self.effects.audio
        self._live_touched.add("eq")
        if a.eq_enabled:
            self.backend.set_equalizer(a.eq_preamp, list(a.eq_bands))
        else:
            self.backend.set_equalizer(None)

    def set_eq_enabled(self, enabled: bool) -> None:
        a = self.effects.audio
        a.eq_enabled = bool(enabled)
        self._push_equalizer()
        label = a.eq_preset or "Custom"
        self._effects_edited(osd=f"Equalizer: {label}" if enabled else "Equalizer off")

    def set_eq_band(self, index: int, gain_db: float) -> None:
        a = self.effects.audio
        if not 0 <= index < EQ_BANDS:
            return
        a.eq_bands[index] = clamp_db(gain_db)
        a.eq_preset = ""
        a.eq_enabled = True
        self._push_equalizer()
        self._effects_edited()

    def set_eq_preamp(self, gain_db: float) -> None:
        a = self.effects.audio
        a.eq_preamp = clamp_db(gain_db)
        a.eq_preset = ""
        a.eq_enabled = True
        self._push_equalizer()
        self._effects_edited()

    def apply_eq_preset(self, name: str) -> bool:
        """Load a built-in or saved preset (and switch the equalizer on)."""
        preset = self.builtin_eq_presets().get(name)
        if preset is None and self.eq_preset_store:
            preset = self.eq_preset_store.get(name)
        if preset is None:
            return False
        a = self.effects.audio
        a.eq_preamp, a.eq_bands = clamp_db(preset[0]), clean_bands(preset[1])
        a.eq_preset = name
        a.eq_enabled = True
        self._push_equalizer()
        self._effects_edited(osd=f"Equalizer: {name}")
        return True

    def save_eq_preset(self, name: str) -> str:
        """Save the current bands as a user preset; raises ValueError on bad names."""
        if not self.eq_preset_store:
            raise ValueError("Presets cannot be saved")
        a = self.effects.audio
        if name.strip().lower() in {n.lower() for n in self.builtin_eq_presets()}:
            raise ValueError(f"“{name.strip()}” is a built-in preset name")
        saved = self.eq_preset_store.save(name, a.eq_preamp, a.eq_bands)
        a.eq_preset = saved
        self._effects_edited(osd=f"Preset saved: {saved}")
        return saved

    def delete_eq_preset(self, name: str) -> bool:
        if not (self.eq_preset_store and self.eq_preset_store.delete(name)):
            return False
        if self.effects.audio.eq_preset == name:
            self.effects.audio.eq_preset = ""
        self._effects_edited(osd=f"Preset deleted: {name}")
        return True

    # --- other audio effects (live)
    def set_normalization(self, enabled: bool) -> None:
        a = self.effects.audio
        a.normalize = bool(enabled)
        self._live_touched.add("normalize")
        if self.backend.supports_normalization:
            self.backend.set_normalization(a.normalize, a.normalize_level)
        self._effects_edited(osd="Volume normalization on" if enabled else "Volume normalization off")

    def set_normalization_level(self, level: float) -> None:
        a = self.effects.audio
        a.normalize_level = round(float(clamp(level, NORMALIZE_MIN, NORMALIZE_MAX)), 1)
        if a.normalize and self.backend.supports_normalization:
            self.backend.set_normalization(True, a.normalize_level)
        self._effects_edited()

    def set_stereo_mode(self, key: str) -> None:
        if key not in STEREO_MODE_VALUES:
            return
        self.effects.audio.stereo_mode = key
        self._live_touched.add("stereo")
        self.backend.set_stereo_mode(STEREO_MODE_VALUES[key])
        label = next(lbl for k, lbl, _ in STEREO_MODES if k == key)
        self._effects_edited(osd=f"Audio: {label}")

    def reset_audio_effects(self) -> None:
        self.effects.audio = AudioEffects()
        self._apply_live_effects()
        self._effects_edited(osd="Audio effects reset")

    # --- video effects
    def set_adjust(self, name: str, value: float) -> None:
        """Brightness, contrast, hue, saturation or gamma (live)."""
        if name not in ADJUST_RANGES:
            return
        lo, hi, _ = ADJUST_RANGES[name]
        v = self.effects.video
        setattr(v, name, round(float(clamp(value, lo, hi)), 3))
        self._live_touched.add("adjust")
        self.backend.set_video_adjust(v.adjust_values() if v.adjust_active else None)
        self._effects_edited()

    def reset_adjust(self, name: str | None = None) -> None:
        v = self.effects.video
        for n in ([name] if name else list(ADJUST_RANGES)):
            if n in ADJUST_RANGES:
                setattr(v, n, ADJUST_RANGES[n][2])
        self._live_touched.add("adjust")
        self.backend.set_video_adjust(v.adjust_values() if v.adjust_active else None)
        self._effects_edited()

    def set_flip(self, horizontal: bool | None = None, vertical: bool | None = None) -> None:
        """Mirror the picture (applied by reloading the media)."""
        v = self.effects.video
        if horizontal is not None:
            v.flip_h = bool(horizontal)
        if vertical is not None:
            v.flip_v = bool(vertical)
        parts = [p for p, on in (("horizontal", v.flip_h), ("vertical", v.flip_v)) if on]
        self._effects_edited(reload=True, osd="Flip: " + (" + ".join(parts) if parts else "off"))

    def toggle_flip_horizontal(self) -> None:
        self.set_flip(horizontal=not self.effects.video.flip_h)

    def toggle_flip_vertical(self) -> None:
        self.set_flip(vertical=not self.effects.video.flip_v)

    def set_sharpen(self, enabled: bool) -> None:
        self.effects.video.sharpen = bool(enabled)
        self._effects_edited(reload=True, osd="Sharpen on" if enabled else "Sharpen off")

    def set_sharpen_strength(self, strength: float) -> None:
        v = self.effects.video
        v.sharpen_strength = round(float(clamp(strength, SHARPEN_MIN, SHARPEN_MAX)), 2)
        self._effects_edited(reload=v.sharpen)

    def reset_video_effects(self) -> None:
        self.effects.video = VideoEffects()
        self._apply_live_effects()
        self._effects_edited(reload=True, osd="Video effects reset")

    def reset_all_effects(self) -> None:
        """Every audio and video effect back to neutral (geometry like crop/zoom untouched)."""
        self.effects = Effects(geometry=self.effects.geometry)
        self._apply_live_effects()
        self._effects_edited(reload=True, osd="All effects reset")

    # ===================================================== chapters / titles
    def _set_chapters(self, chapters: list[Chapter], titles: list[TitleInfo], current_title: int) -> None:
        if (chapters, titles, current_title) == (self.chapters, self.titles, self.current_title):
            return
        self.chapters, self.titles, self.current_title = chapters, titles, current_title
        self.chapters_changed.emit(self.chapters, self.titles, self.current_title)

    def refresh_chapters(self) -> None:
        """Re-read chapters and titles from the engine (cheap; called while playing)."""
        self._chapter_poll = self._clock()
        if not self.has_media:
            self._set_chapters([], [], -1)
            return
        self._set_chapters(self.backend.chapters(), self.backend.titles(), self.backend.title())

    def current_chapter(self) -> int:
        """Index of the chapter at the playback position (-1 without chapters)."""
        if not self.chapters:
            return -1
        t = self.backend.time()
        index = 0
        for i, ch in enumerate(self.chapters):
            if ch.start_ms <= t:
                index = i
        return index

    def _chapter_osd(self, index: int) -> None:
        if 0 <= index < len(self.chapters):
            ch = self.chapters[index]
            self.osd.emit(f"Chapter {index + 1}/{len(self.chapters)}: {ch.title}")

    def set_chapter(self, index: int) -> None:
        if not self.has_media or not 0 <= index < len(self.chapters):
            return
        self.backend.set_chapter(index)
        self.position_changed.emit(self.chapters[index].start_ms, self.backend.length())
        self._chapter_osd(index)

    def next_chapter(self) -> None:
        if not self.has_media:
            return
        index = self.current_chapter()
        if self.chapters and index + 1 < len(self.chapters):
            self.set_chapter(index + 1)
        elif self.titles and 0 <= self.current_title < len(self.titles) - 1:
            self.set_title(self.current_title + 1)  # like VLC: past the last chapter → next title
        elif self.chapters:
            self.osd.emit("Last chapter")

    def previous_chapter(self) -> None:
        if not self.has_media or not self.chapters:
            if self.has_media and self.titles and self.current_title > 0:
                self.set_title(self.current_title - 1)
            return
        index = self.current_chapter()
        into = self.backend.time() - self.chapters[index].start_ms
        if index > 0 and into < self.CHAPTER_RESTART_MS:
            self.set_chapter(index - 1)
        else:
            self.set_chapter(index)

    def set_title(self, index: int) -> None:
        if not self.has_media or not 0 <= index < len(self.titles):
            return
        self.backend.set_title(index)
        title = self.titles[index]
        self.osd.emit("Disc menu" if title.is_menu else f"Title {index + 1}: {title.name}")
        self.refresh_chapters()

    def next_title(self) -> None:
        if self.titles and self.current_title + 1 < len(self.titles):
            self.set_title(self.current_title + 1)

    def previous_title(self) -> None:
        if self.titles and self.current_title > 0:
            self.set_title(self.current_title - 1)

    @property
    def in_menu(self) -> bool:
        """The current disc title is an interactive menu (arrows/Enter navigate it)."""
        return 0 <= self.current_title < len(self.titles) and self.titles[self.current_title].is_menu

    def disc_menu(self) -> None:
        """Go to the disc's menu (its menu title, or the engine's popup menu)."""
        if not self.has_media:
            return
        menu = next((t.index for t in self.titles if t.is_menu), None)
        if menu is not None:
            self.set_title(menu)
        else:
            self.backend.navigate("popup")
            self.osd.emit("Disc menu")
        self.refresh_chapters()

    def navigate(self, action: str) -> None:
        if self.has_media and action in NAVIGATE_ACTIONS:
            self.backend.navigate(action)
            if action == "activate":
                self.refresh_chapters()

    # =================================================================== loop
    def tick(self) -> None:
        """Poll the backend; call ~5-10 times a second from the UI thread."""
        if self.reload_debounce.poll():
            self._reload_in_place()
        st = self.backend.state()
        if st is PlaybackState.PLAYING and not self._started:
            self._on_started()
        if st is PlaybackState.ENDED:
            if self._expand_subitems():
                return
            item = self.playlist.current
            if not self._started and item is not None and mrl_to_path(item.mrl) is None:
                # libVLC "ends" a stream it could not open (HTTP 404, refused…): an error, not the end.
                self._set_state(PlaybackState.ERROR)
                self._on_error()
                return
            self._set_state(st)
            self._on_ended()
            return
        if not self._started and self._opening_timed_out(st):
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
            if self._clock() - self._chapter_poll >= self.CHAPTER_POLL_S:
                self.refresh_chapters()
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
        self._apply_live_effects(initial=True)
        if self.audio_delay:
            self.backend.set_audio_delay(self.audio_delay)
        if self.subtitle_delay:
            self.backend.set_subtitle_delay(self.subtitle_delay)
        for mrl in self._external_subtitles:
            self.backend.add_subtitle_file(mrl)
        if self._restore_tracks is not None:
            audio, spu = self._restore_tracks
            self._restore_tracks = None
            if audio != -1 and audio in [t.id for t in self.backend.audio_tracks()]:
                self.backend.set_audio_track(audio)
            if spu == -1 or spu in [t.id for t in self.backend.subtitle_tracks()]:
                self.backend.set_subtitle_track(spu)
        if self._pending_seek:
            self.backend.set_time(self._pending_seek)
        self._pending_seek = None
        if self._pause_after_start:
            self._pause_after_start = False
            self.backend.pause()
        self._opening_since = None
        self.refresh_chapters()
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

    def _expand_subitems(self) -> bool:
        """A YouTube page or remote playlist resolved to sub-items: play those instead.

        Mirrors VLC's playlist, where such an entry expands into its children.
        """
        subitems = self.backend.subitems()
        index = self.playlist.current_index
        item = self.playlist.current
        if not subitems or item is None:
            return False
        new = [PlaylistItem(mrl, title.strip(), options=list(item.options)) for mrl, title in subitems]
        if len(new) == 1 and (not subitems[0][1].strip() or "://" in subitems[0][1]):
            new[0].title = item.title  # keep the name the user gave the link
        self.playlist.replace(index, new)
        self._start_current()
        return True

    def _opening_timed_out(self, st: PlaybackState) -> bool:
        """Network media that never starts must fail with a message, not hang."""
        item = self.playlist.current
        timeout = self.settings.network_timeout_s
        if (self._opening_since is None or item is None or timeout <= 0 or not is_network_mrl(item.mrl)
                or st not in (PlaybackState.OPENING, PlaybackState.BUFFERING, PlaybackState.IDLE)):
            return False
        if self._clock() - self._opening_since < timeout:
            return False
        self._opening_since = None
        self.backend.stop()
        self._report_open_failure(item, f"no response after {timeout} s")
        self._set_state(PlaybackState.ERROR)
        self._set_state(PlaybackState.STOPPED)
        return True

    def _report_open_failure(self, item: PlaylistItem, reason: str = "") -> None:
        self.error.emit(f"Could not play {item.title}")  # short form (OSD), as before phase 3
        self.open_failed.emit(item, describe_open_error(item.mrl, reason))

    def _on_error(self) -> None:
        item = self.playlist.current
        self._opening_since = None
        if item is not None and not self._started:
            self._report_open_failure(item)
        else:
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
        for store in (self.resume, self.effects_store):
            if store:
                try:
                    store.save()
                except OSError:
                    pass

    def shutdown(self) -> None:
        self._remember_position()
        self._store_effects()
        self._save_resume()
        self.backend.release()
