"""libVLC implementation of :class:`MediaBackend` (via python-vlc).

This is the only module in the project that imports ``vlc``.
"""

from __future__ import annotations

import ctypes
import os
import struct
import sys
import threading
import time

from collections import OrderedDict

from .backend import (NAVIGATE_ACTIONS, CaptureDevice, Chapter, MediaBackend, MediaInfo, PlaybackState,
                      StreamInfo, TitleInfo, Track)
from .converter import TranscodeEngine
from .effects import EQ_BANDS, EngineFilters


class LibVlcNotFound(RuntimeError):
    pass


def bundled_vlc_dir() -> str | None:
    """Folder of a libVLC shipped next to the app (the packaged Windows build)."""
    roots = []
    if getattr(sys, "frozen", False):
        roots.append(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)))
        roots.append(os.path.dirname(sys.executable))
    for root in roots:
        folder = os.path.join(root, "vlc")
        if os.path.isfile(os.path.join(folder, "libvlc.dll")):
            return folder
    return None


def _use_vlc_folder(folder: str) -> None:
    os.environ["PYTHON_VLC_LIB_PATH"] = os.path.join(folder, "libvlc.dll")
    plugins = os.path.join(folder, "plugins")
    os.environ["PYTHON_VLC_MODULE_PATH"] = plugins
    os.environ["VLC_PLUGIN_PATH"] = plugins
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(folder)


def _prepare_windows_search() -> None:
    """Help python-vlc find libVLC on Windows: bundled copy first, then a 64-bit VLC install."""
    if sys.platform != "win32" or os.environ.get("PYTHON_VLC_LIB_PATH"):
        return
    bundled = bundled_vlc_dir()
    if bundled:
        _use_vlc_folder(bundled)
        return
    candidates: list[str] = []
    try:
        import winreg

        for view in (winreg.KEY_WOW64_64KEY, 0):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"Software\VideoLAN\VLC", 0,
                                    winreg.KEY_READ | view) as key:
                    candidates.append(winreg.QueryValueEx(key, "InstallDir")[0])
            except OSError:
                pass
    except ImportError:
        pass
    program_files = os.environ.get("ProgramW6432") or os.environ.get("ProgramFiles", r"C:\Program Files")
    candidates.append(os.path.join(program_files, "VideoLAN", "VLC"))
    for folder in candidates:
        if os.path.isfile(os.path.join(folder, "libvlc.dll")):
            _use_vlc_folder(folder)
            return


def import_vlc():
    _prepare_windows_search()
    try:
        import vlc  # noqa: WPS433 (runtime import on purpose)
    except (ImportError, OSError, FileNotFoundError, AttributeError) as exc:
        bits = struct.calcsize("P") * 8
        raise LibVlcNotFound(
            "libVLC could not be loaded.\n\n"
            f"Install VLC media player ({bits}-bit, matching this Python) from "
            "https://www.videolan.org/ and try again.\n\n"
            f"Details: {exc}"
        ) from exc
    try:
        vlc.libvlc_get_version()
    except Exception as exc:  # pragma: no cover - depends on install
        raise LibVlcNotFound(f"libVLC is installed but unusable: {exc}") from exc
    return vlc


def _decode(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def _fourcc(code: int) -> str:
    try:
        return struct.pack("<I", code).decode("ascii", "replace").strip("\x00 ")
    except (struct.error, ValueError):
        return ""


# python-vlc declares the audio/video/subtitle union of libvlc_media_track_t
# as three separate pointers, which misaligns every later field (and can
# crash). These mirror the real libVLC 3 layout.
class _AudioTrack(ctypes.Structure):
    _fields_ = [("channels", ctypes.c_uint), ("rate", ctypes.c_uint)]


class _VideoTrack(ctypes.Structure):
    _fields_ = [("height", ctypes.c_uint), ("width", ctypes.c_uint),
                ("sar_num", ctypes.c_uint), ("sar_den", ctypes.c_uint),
                ("frame_rate_num", ctypes.c_uint), ("frame_rate_den", ctypes.c_uint)]


class _TrackUnion(ctypes.Union):
    _fields_ = [("audio", ctypes.POINTER(_AudioTrack)), ("video", ctypes.POINTER(_VideoTrack)),
                ("subtitle", ctypes.c_void_p)]


class _MediaTrack(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("codec", ctypes.c_uint32), ("original_fourcc", ctypes.c_uint32),
                ("id", ctypes.c_int), ("type", ctypes.c_int), ("profile", ctypes.c_int),
                ("level", ctypes.c_int), ("u", _TrackUnion), ("bitrate", ctypes.c_uint),
                ("language", ctypes.c_char_p), ("description", ctypes.c_char_p)]


def _read_tracks(vlc, media) -> list[dict]:
    """Read elementary stream info with a correct struct layout."""
    lib = vlc.dll
    get = lib.libvlc_media_tracks_get
    get.restype = ctypes.c_uint
    get.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.POINTER(ctypes.POINTER(_MediaTrack)))]
    release = lib.libvlc_media_tracks_release
    release.restype = None
    release.argtypes = [ctypes.POINTER(ctypes.POINTER(_MediaTrack)), ctypes.c_uint]
    array = ctypes.POINTER(ctypes.POINTER(_MediaTrack))()
    handle = ctypes.cast(media._as_parameter_, ctypes.c_void_p)
    count = get(handle, ctypes.byref(array))
    tracks = []
    try:
        for i in range(count):
            t = array[i].contents
            entry = {
                "type": t.type, "codec": t.codec, "bitrate": t.bitrate,
                "language": _decode(t.language), "description": _decode(t.description),
            }
            if t.type == 0 and t.audio:
                entry["channels"], entry["rate"] = t.audio.contents.channels, t.audio.contents.rate
            elif t.type == 1 and t.video:
                v = t.video.contents
                entry["width"], entry["height"] = v.width, v.height
                entry["fps"] = v.frame_rate_num / v.frame_rate_den if v.frame_rate_den else 0.0
            tracks.append(entry)
    finally:
        if count:
            release(array, count)
    return tracks


def _load_libvlccore(vlc):
    """The libvlccore library next to the loaded libVLC (None if not found)."""
    names = ["libvlccore.dll", "libvlccore.so.9", "libvlccore.dylib"]
    folder = os.path.dirname(getattr(vlc.dll, "_name", "") or "")
    candidates = [os.path.join(folder, n) for n in names if folder] + names
    import ctypes.util

    found = ctypes.util.find_library("vlccore")
    if found:
        candidates.append(found)
    for candidate in candidates:
        try:
            return ctypes.CDLL(candidate)
        except OSError:
            continue
    return None


def config_choices(vlc, instance, name: str) -> list[tuple[str, str]] | None:
    """(value, label) choices VLC offers for a string option, e.g. "dshow-vdev".

    This is how VLC's own preferences list DirectShow devices: the option's
    choices callback lives in the dshow module and enumerates the devices.
    Uses libvlccore's exported ``config_GetPszChoices`` (stable in 3.0.x);
    returns None when unavailable.
    """
    try:
        if not _decode(vlc.libvlc_get_version()).startswith("3."):
            return None
        core = _load_libvlccore(vlc)
        if core is None:
            return None
        get = core.config_GetPszChoices
        get.restype = ctypes.c_ssize_t
        get.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.POINTER(ctypes.c_char_p)),
                        ctypes.POINTER(ctypes.POINTER(ctypes.c_char_p))]
        # libvlc_instance_t starts with its libvlc_int_t* (a vlc_object_t).
        libvlc_int = ctypes.cast(instance._as_parameter_, ctypes.POINTER(ctypes.c_void_p))[0]
        values = ctypes.POINTER(ctypes.c_char_p)()
        texts = ctypes.POINTER(ctypes.c_char_p)()
        count = get(libvlc_int, name.encode(), ctypes.byref(values), ctypes.byref(texts))
        if count < 0:
            return None
        free = vlc.dll.libvlc_free
        free.argtypes = [ctypes.c_void_p]
        free.restype = None
        result = []
        for i in range(count):
            result.append((_decode(values[i]), _decode(texts[i])))
        for array in (values, texts):  # free each string, then the array
            raw = ctypes.cast(array, ctypes.POINTER(ctypes.c_void_p))
            for i in range(count):
                free(raw[i])
            free(ctypes.cast(array, ctypes.c_void_p))
        return result
    except Exception:
        return None


class _AoutFilterHook:
    """Set the audio filter chain on a player's audio output (for "normvol").

    libVLC's public API routes the whole audio-filter chain through its
    equalizer call, so volume normalization has no public switch. This uses
    libvlccore's exported variable API instead, on the audio-output child of
    the media player, exactly like VLC's own interface does. The object and
    list layouts used here are part of libvlccore's frozen 3.0.x plugin ABI;
    the hook only enables itself on libVLC 3.0 and fails closed otherwise.
    """

    VLC_VAR_STRING = 0x0040
    VLC_VAR_FLOAT = 0x0050

    class _Value(ctypes.Union):
        _fields_ = [("i", ctypes.c_int64), ("f", ctypes.c_float), ("s", ctypes.c_char_p),
                    ("p", ctypes.c_void_p)]

    class _List(ctypes.Structure):  # vlc_list_t
        _fields_ = [("type", ctypes.c_int), ("count", ctypes.c_int), ("values", ctypes.c_void_p)]

    def __init__(self, vlc) -> None:
        self.core = None
        try:
            if not _decode(vlc.libvlc_get_version()).startswith("3.0"):
                return
            core = self._load_core(vlc)
            if core is None:
                return
            core.vlc_list_children.restype = ctypes.POINTER(self._List)
            core.vlc_list_children.argtypes = [ctypes.c_void_p]
            core.vlc_list_release.restype = None
            core.vlc_list_release.argtypes = [ctypes.POINTER(self._List)]
            core.var_Create.restype = ctypes.c_int
            core.var_Create.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
            core.var_SetChecked.restype = ctypes.c_int
            core.var_SetChecked.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int, self._Value]
            core.var_Type.restype = ctypes.c_int
            core.var_Type.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
            self.core = core
        except Exception:
            self.core = None

    @staticmethod
    def _load_core(vlc):
        return _load_libvlccore(vlc)

    @property
    def available(self) -> bool:
        return self.core is not None

    def set_chain(self, player, chain: str, level: float) -> bool:
        """Apply ``chain`` (e.g. "equalizer:normvol") to the player's audio output."""
        if self.core is None:
            return False
        core = self.core
        handle = ctypes.cast(player._as_parameter_, ctypes.c_void_p)
        lst = core.vlc_list_children(handle)
        if not lst:
            return False
        try:
            count = lst.contents.count
            if not 0 <= count <= 64 or not lst.contents.values:
                return False
            values = ctypes.cast(lst.contents.values, ctypes.POINTER(self._Value))
            for k in range(count):
                obj = values[k].p
                if not obj:
                    continue
                kind = ctypes.cast(obj, ctypes.POINTER(ctypes.c_char_p))[0]  # obj->object_type
                if kind != b"audio output" or core.var_Type(obj, b"audio-filter") != self.VLC_VAR_STRING:
                    continue
                core.var_Create(obj, b"norm-max-level", self.VLC_VAR_FLOAT)
                core.var_SetChecked(obj, b"norm-max-level", self.VLC_VAR_FLOAT, self._Value(f=level))
                return core.var_SetChecked(obj, b"audio-filter", self.VLC_VAR_STRING,
                                           self._Value(s=chain.encode())) == 0
            return False
        finally:
            core.vlc_list_release(lst)


class VlcBackend(MediaBackend):
    def __init__(self, args: list[str] | None = None) -> None:
        self.vlc = import_vlc()
        args = list(args or [])
        if sys.platform.startswith("linux") and "--no-xlib" not in args:
            args.append("--no-xlib")  # we embed via XCB; Xlib needs XInitThreads
        self._args = args
        self.instance = self._new_instance(args)
        # libVLC only honours video filters (rotate/flip, sharpen) at instance
        # level, so each filter set gets its own instance, created lazily and
        # kept in a small LRU cache so toggling back and forth is fast.
        self._instances: OrderedDict[EngineFilters, object] = OrderedDict({EngineFilters(): self.instance})
        self._filters = EngineFilters()
        self._player_filters = EngineFilters()
        self._handle: int | None = None
        self.player = self._new_player()
        self._media = None
        self._subitem_media: dict[str, object] = {}  # resolved YouTube/playlist entries, with their options
        self._eq = None
        self._eq_on = False
        self._normalize = (False, 2.0)
        self._adjust_on = False
        self._aout_hook = _AoutFilterHook(self.vlc)
        self._states = {
            self.vlc.State.NothingSpecial: PlaybackState.IDLE,
            self.vlc.State.Opening: PlaybackState.OPENING,
            self.vlc.State.Buffering: PlaybackState.BUFFERING,
            self.vlc.State.Playing: PlaybackState.PLAYING,
            self.vlc.State.Paused: PlaybackState.PAUSED,
            self.vlc.State.Stopped: PlaybackState.STOPPED,
            self.vlc.State.Ended: PlaybackState.ENDED,
            self.vlc.State.Error: PlaybackState.ERROR,
        }

    def _new_instance(self, args: list[str]):
        instance = self.vlc.Instance(args)
        if instance is None:
            # Bad user arguments make Instance() fail; fall back to defaults.
            instance = self.vlc.Instance([a for a in args if a in ("--quiet", "--no-xlib")])
        if instance is None:
            raise LibVlcNotFound("libVLC failed to initialise")
        return instance

    def _new_player(self):
        player = self.instance.media_player_new()
        player.video_set_mouse_input(False)
        player.video_set_key_input(False)
        if self._handle is not None:
            self._attach(player, self._handle)
        return player

    @staticmethod
    def _attach(player, handle: int) -> None:
        if sys.platform == "win32":
            player.set_hwnd(handle)
        elif sys.platform == "darwin":
            player.set_nsobject(handle)
        else:
            player.set_xwindow(handle)

    # ------------------------------------------------------------ lifecycle
    def set_video_output(self, handle: int) -> None:
        self._handle = handle
        self._attach(self.player, handle)

    def release(self) -> None:
        try:
            self.player.stop()
            self.player.release()
            if self._eq is not None:
                self.vlc.libvlc_audio_equalizer_release(self._eq)
                self._eq = None
            for instance in self._instances.values():
                instance.release()
        except Exception:
            pass

    MAX_INSTANCES = 3

    def set_engine_filters(self, filters: EngineFilters) -> None:
        self._filters = filters

    def _swap_player_for_filters(self) -> None:
        filters = self._filters
        instance = self._instances.get(filters)
        if instance is None:
            instance = self._new_instance(self._args + filters.vlc_args())
            self._instances[filters] = instance
        self._instances.move_to_end(filters)
        volume = self.player.audio_get_volume()
        self.player.stop()
        self.player.release()
        if self._media is not None:
            self._media.release()
            self._media = None
        self.instance = instance
        self.player = self._new_player()
        self._adjust_on = False
        if volume >= 0:
            self.player.audio_set_volume(volume)
        self._player_filters = filters
        # Evict the least recently used instances (never the plain or current one).
        while len(self._instances) > self.MAX_INSTANCES:
            victim = next(k for k in self._instances if k not in (filters, EngineFilters()))
            self._instances.pop(victim).release()

    # ---------------------------------------------------------------- media
    def load(self, mrl: str, options: list[str] | None = None) -> None:
        if self._filters != self._player_filters:
            self._swap_player_for_filters()
        media = self._subitem_media.pop(mrl, None)  # keeps options a Lua script attached
        if media is None:
            media = self.instance.media_new(mrl)
        for stale in self._subitem_media.values():
            stale.release()
        self._subitem_media.clear()
        for opt in options or []:
            media.add_option(opt)
        self.player.set_media(media)
        if self._media is not None:
            self._media.release()
        self._media = media

    def play(self) -> None:
        self.player.play()

    def pause(self) -> None:
        self.player.set_pause(1)

    def stop(self) -> None:
        self.player.stop()

    def state(self) -> PlaybackState:
        return self._states.get(self.player.get_state(), PlaybackState.IDLE)

    def time(self) -> int:
        return max(-1, self.player.get_time())

    def set_time(self, ms: int) -> None:
        self.player.set_time(int(max(0, ms)))

    def length(self) -> int:
        return self.player.get_length()

    def is_seekable(self) -> bool:
        return bool(self.player.is_seekable())

    def next_frame(self) -> None:
        self.player.next_frame()

    # ---------------------------------------------------------------- audio
    def volume(self) -> int:
        return self.player.audio_get_volume()

    def set_volume(self, percent: int) -> None:
        self.player.audio_set_volume(int(percent))

    def set_mute(self, muted: bool) -> None:
        self.player.audio_set_mute(bool(muted))

    def set_rate(self, rate: float) -> None:
        self.player.set_rate(float(rate))

    def rate(self) -> float:
        return float(self.player.get_rate())

    @staticmethod
    def _tracks(descriptions) -> list[Track]:
        result = []
        for entry in descriptions or []:
            if isinstance(entry, tuple):
                track_id, name = entry[0], entry[1]
            else:  # older bindings return objects
                track_id, name = entry.id, entry.name
            result.append(Track(int(track_id), _decode(name)))
        return result

    def audio_tracks(self) -> list[Track]:
        return self._tracks(self.player.audio_get_track_description())

    def audio_track(self) -> int:
        return self.player.audio_get_track()

    def set_audio_track(self, track_id: int) -> None:
        self.player.audio_set_track(int(track_id))

    def set_audio_delay(self, ms: int) -> None:
        self.player.audio_set_delay(int(ms) * 1000)

    # ------------------------------------------------------------ subtitles
    def subtitle_tracks(self) -> list[Track]:
        return self._tracks(self.player.video_get_spu_description())

    def subtitle_track(self) -> int:
        return self.player.video_get_spu()

    def set_subtitle_track(self, track_id: int) -> None:
        self.player.video_set_spu(int(track_id))

    def add_subtitle_file(self, mrl: str) -> bool:
        kind = self.vlc.MediaSlaveType.subtitle
        return self.player.add_slave(kind, mrl, True) == 0

    def set_subtitle_delay(self, ms: int) -> None:
        self.player.video_set_spu_delay(int(ms) * 1000)

    # ---------------------------------------------------------------- video
    def has_video(self) -> bool:
        return self.player.has_vout() > 0

    def video_size(self) -> tuple[int, int]:
        try:
            w, h = self.player.video_get_size(0)
            return int(w), int(h)
        except Exception:
            return 0, 0

    def set_aspect_ratio(self, ratio: str | None) -> None:
        self.player.video_set_aspect_ratio(ratio)

    def set_crop(self, geometry: str | None) -> None:
        self.player.video_set_crop_geometry(geometry)

    def set_zoom(self, factor: float) -> None:
        self.player.video_set_scale(float(factor))

    def set_deinterlace(self, mode: str | None) -> None:
        try:
            self.player.video_set_deinterlace(mode)
        except TypeError:  # libVLC 4 style bindings: (deinterlace, mode)
            self.player.video_set_deinterlace(1 if mode else 0, mode)

    def take_snapshot(self, path: str) -> bool:
        return self.player.video_take_snapshot(0, path, 0, 0) == 0

    # -------------------------------------------------------------- effects
    def equalizer_presets(self) -> dict[str, tuple[float, list[float]]]:
        vlc = self.vlc
        try:
            presets = {}
            for index in range(vlc.libvlc_audio_equalizer_get_preset_count()):
                name = _decode(vlc.libvlc_audio_equalizer_get_preset_name(index))
                eq = vlc.libvlc_audio_equalizer_new_from_preset(index)
                try:
                    preamp = round(vlc.libvlc_audio_equalizer_get_preamp(eq), 1)
                    bands = [round(vlc.libvlc_audio_equalizer_get_amp_at_index(eq, b), 1) + 0.0
                             for b in range(EQ_BANDS)]
                finally:
                    vlc.libvlc_audio_equalizer_release(eq)
                presets[name] = (preamp, bands)
            return presets or super().equalizer_presets()
        except Exception:
            return super().equalizer_presets()

    def set_equalizer(self, preamp: float | None, bands: list[float] | None = None) -> None:
        vlc = self.vlc
        if preamp is None:
            self._eq_on = False
            self.player.set_equalizer(None)
        else:
            if self._eq is None:
                self._eq = vlc.libvlc_audio_equalizer_new()
            vlc.libvlc_audio_equalizer_set_preamp(self._eq, float(preamp))
            for index, gain in enumerate((bands or [])[:EQ_BANDS]):
                vlc.libvlc_audio_equalizer_set_amp_at_index(self._eq, float(gain), index)
            self._eq_on = True
            self.player.set_equalizer(self._eq)
        if self._normalize[0]:
            # set_equalizer rewrites the audio filter chain; put normvol back.
            self._apply_audio_chain()

    def set_stereo_mode(self, mode: int) -> None:
        self.vlc.libvlc_audio_set_channel(self.player, int(mode))

    def set_video_adjust(self, values: dict[str, float] | None) -> None:
        opt = self.vlc.VideoAdjustOption
        if values is None:
            if self._adjust_on:
                self.player.video_set_adjust_int(opt.Enable, 0)
                self._adjust_on = False
            return
        names = {"brightness": opt.Brightness, "contrast": opt.Contrast, "hue": opt.Hue,
                 "saturation": opt.Saturation, "gamma": opt.Gamma}
        for name, value in values.items():
            if name in names:
                self.player.video_set_adjust_float(names[name], float(value))
        if not self._adjust_on:
            # Enabling rebuilds the vout filter chain once; value changes are live.
            self.player.video_set_adjust_int(opt.Enable, 1)
            self._adjust_on = True

    @property
    def supports_normalization(self) -> bool:
        return self._aout_hook.available

    def set_normalization(self, enabled: bool, level: float) -> None:
        was = self._normalize[0]
        self._normalize = (bool(enabled), float(level))
        if enabled or was:
            self._apply_audio_chain()

    def _apply_audio_chain(self) -> None:
        enabled, level = self._normalize
        chain = ":".join((["equalizer"] if self._eq_on else []) + (["normvol"] if enabled else []))
        self._aout_hook.set_chain(self.player, chain, level)

    # --------------------------------------------- titles, chapters and menus
    def titles(self) -> list[TitleInfo]:
        try:
            descriptions = self.player.get_full_title_descriptions() or []
        except Exception:
            return []
        titles = []
        for i, d in enumerate(descriptions):
            name = _decode(d.name).strip() or f"Title {i + 1}"
            titles.append(TitleInfo(i, name, int(d.duration or -1), bool(d.flags & 0x01)))
        return titles

    def title(self) -> int:
        return self.player.get_title()

    def set_title(self, index: int) -> None:
        self.player.set_title(int(index))

    def chapters(self) -> list[Chapter]:
        try:
            descriptions = self.player.get_full_chapter_descriptions(-1) or []
        except Exception:
            return []
        return [Chapter(i, _decode(d.name).strip() or f"Chapter {i + 1}", int(d.time_offset),
                        int(d.duration or -1)) for i, d in enumerate(descriptions)]

    def chapter(self) -> int:
        return self.player.get_chapter()

    def set_chapter(self, index: int) -> None:
        self.player.set_chapter(int(index))

    def navigate(self, action: str) -> None:
        if action in NAVIGATE_ACTIONS:
            self.player.navigate(NAVIGATE_ACTIONS.index(action))

    def subitems(self) -> list[tuple[str, str]]:
        media = self._media
        if media is None:
            return []
        try:
            items = media.subitems()
            if items is None:
                return []
            result = []
            items.lock()
            try:
                for i in range(items.count()):
                    sub = items.item_at_index(i)
                    if sub is None:
                        continue
                    mrl = _decode(sub.get_mrl())
                    result.append((mrl, _decode(sub.get_meta(self.vlc.Meta.Title))))
                    if mrl in self._subitem_media:
                        sub.release()
                    else:
                        self._subitem_media[mrl] = sub
            finally:
                items.unlock()
                items.release()
            return result
        except Exception:
            return []

    def capture_devices(self) -> dict[str, list[CaptureDevice]]:
        devices = super().capture_devices()
        if sys.platform == "win32":
            for kind, option, default in (("video", "dshow-vdev", "Default camera"),
                                          ("audio", "dshow-adev", "Default microphone")):
                choices = config_choices(self.vlc, self.instance, option)
                if choices is not None:
                    devices[kind] = [CaptureDevice("", default)] + [
                        CaptureDevice(value, label or value) for value, label in choices if value not in ("", "none")]
        return devices

    # ----------------------------------------------------------------- info
    def media_info(self) -> MediaInfo:
        media = self.player.get_media()
        info = MediaInfo()
        if media is None:
            return info
        info.mrl = _decode(media.get_mrl())
        info.duration_ms = self.player.get_length()
        for meta_id, name in self.vlc.Meta._enum_names_.items():
            value = media.get_meta(self.vlc.Meta(meta_id))
            if value:
                info.meta[name] = _decode(value)
        info.title = info.meta.get("Title", "")
        try:
            tracks = _read_tracks(self.vlc, media)
        except Exception:
            tracks = []
        type_names = {0: "audio", 1: "video", 2: "subtitle"}
        for t in tracks:
            kind = type_names.get(t["type"], "unknown")
            codec = ""
            try:
                codec = _decode(self.vlc.libvlc_media_get_codec_description(
                    self.vlc.TrackType(t["type"]), t["codec"]))
            except Exception:
                pass
            fourcc = _fourcc(t["codec"])
            info.streams.append(StreamInfo(
                kind=kind,
                codec=f"{codec} ({fourcc})" if codec and fourcc else (codec or fourcc),
                language=t["language"],
                description=t["description"],
                width=t.get("width", 0),
                height=t.get("height", 0),
                fps=t.get("fps", 0.0),
                channels=t.get("channels", 0),
                sample_rate=t.get("rate", 0),
                bitrate=int(t["bitrate"] or 0),
            ))
        try:
            stats = self.vlc.MediaStats()
            if media.get_stats(stats):
                # libVLC reports bytes per microsecond-ish units; VLC's own
                # dialog multiplies by 8000 to show kb/s.
                info.input_bitrate_kbps = stats.input_bitrate * 8000
                info.demux_bitrate_kbps = stats.demux_bitrate * 8000
                info.decoded_video = stats.decoded_video
                info.displayed_pictures = stats.displayed_pictures
                info.lost_pictures = stats.lost_pictures
                info.decoded_audio = stats.decoded_audio
                info.lost_audio_buffers = stats.lost_abuffers
        except Exception:
            pass
        return info

    def engine_version(self) -> str:
        return _decode(self.vlc.libvlc_get_version())


_FORMAT_CB_PTR = ctypes.CFUNCTYPE(ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
                                  ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint),
                                  ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint))


class VlcMediaProbe:
    """Media-library metadata and thumbnails through libVLC (a ``MetadataProbe``).

    Uses its own libVLC instance on the library's worker thread. Metadata
    comes from libVLC's local preparser (tags + demuxer: duration, tracks);
    thumbnails decode one frame into memory through libVLC's "vmem" video
    callbacks, so no window or video output is needed. (libVLC 3 has no
    thumbnailer API; VLC 4 adds one.)
    """

    PARSE_TIMEOUT_MS = 5000
    FRAME_TIMEOUT_S = 6.0

    def __init__(self, args: list[str] | None = None) -> None:
        self.vlc = import_vlc()
        base = ["--quiet", "--no-video-title-show", "--no-sub-autodetect-file", "--aout=dummy",
                "--avcodec-hw=none", "--no-stats"]
        if sys.platform.startswith("linux"):
            base.append("--no-xlib")
        self.instance = self.vlc.Instance(base + list(args or []))
        if self.instance is None:
            raise LibVlcNotFound("libVLC failed to initialise for the media library")
        self._info_cache: dict = {}

    def close(self) -> None:
        try:
            self.instance.release()
        except Exception:
            pass

    def _parse(self, path: str):
        vlc = self.vlc
        media = self.instance.media_new_path(path)
        media.parse_with_options(vlc.MediaParseFlag.local, self.PARSE_TIMEOUT_MS)
        deadline = time.monotonic() + self.PARSE_TIMEOUT_MS / 1000 + 1
        while media.get_parsed_status() == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        return media

    def probe(self, path: str):
        from .library import MediaMeta

        vlc = self.vlc
        media = self._parse(path)
        try:
            def meta(key: str) -> str:
                return _decode(media.get_meta(getattr(vlc.Meta, key)))

            info = MediaMeta(title=meta("Title"), artist=meta("Artist"), album=meta("Album"),
                             album_artist=meta("AlbumArtist"), genre=meta("Genre"), year=meta("Date"),
                             duration_ms=media.get_duration())
            try:
                info.track = int((meta("TrackNumber") or "0").split("/")[0] or 0)
            except ValueError:
                info.track = 0
            try:
                tracks = _read_tracks(vlc, media)
            except Exception:
                tracks = []
            video = next((t for t in tracks if t["type"] == 1 and t.get("width")), None)
            if video:
                info.width, info.height, info.has_video = video["width"], video["height"], True
            return info
        finally:
            media.release()

    def thumbnail(self, path: str, out_path: str, width: int = 320, duration_ms: int = -1,
                  at_ms: int | None = None) -> bool:
        from .png import bgra_to_rgb, write_png

        vlc = self.vlc
        info = self._info_cache.get(path) if at_ms is not None else None
        if info is None:
            try:
                info = self.probe(path)
            except Exception:
                info = None
            if at_ms is not None:  # seek previews ask for many frames of one file
                self._info_cache = {path: info}
        aspect = info.width / info.height if info and info.width and info.height else None
        lock = threading.Lock()
        # Filled by the format callback: the decoder tells us the picture size and
        # we ask libVLC to scale it to our width in RV32 (B, G, R, X).
        state: dict = {"frames": 0, "data": None, "buf": None, "size": None}

        # python-vlc declares ``chroma`` as c_char_p, which hands the callback a *copy*: writing
        # "RV32" into it would never reach libVLC (frames would stay planar I420 and come out as
        # garbled grey). So the callback is declared with a real pointer and cast to python-vlc's type.
        @_FORMAT_CB_PTR
        def on_format(opaque, chroma, w_ptr, h_ptr, pitches, lines):
            src_w, src_h = w_ptr[0], h_ptr[0]
            if not src_w or not src_h:
                return 0
            # Decoders may report padded sizes (240 → 256); prefer the stream's own aspect.
            tw = max(16, min(width, src_w)) // 2 * 2
            th = max(2, int(round(tw / (aspect or src_w / src_h))) // 2 * 2)
            ctypes.memmove(chroma, b"RV32", 4)
            w_ptr[0], h_ptr[0] = tw, th
            pitches[0], lines[0] = tw * 4, th
            with lock:
                state["buf"] = (ctypes.c_ubyte * (tw * 4 * th))()
                state["size"] = (tw, th)
            return 1

        @vlc.CallbackDecorators.VideoCleanupCb
        def on_cleanup(opaque):
            pass

        @vlc.CallbackDecorators.VideoLockCb
        def on_lock(opaque, planes):
            planes[0] = ctypes.cast(state["buf"], ctypes.c_void_p)
            return None

        @vlc.CallbackDecorators.VideoUnlockCb
        def on_unlock(opaque, picture, planes):
            pass

        @vlc.CallbackDecorators.VideoDisplayCb
        def on_display(opaque, picture):
            with lock:
                state["frames"] += 1
                if state["frames"] >= 2 and state["data"] is None:  # first frame after a seek can be stale
                    state["data"] = bytes(state["buf"])

        player = self.instance.media_player_new()
        player.video_set_callbacks(on_lock, on_unlock, on_display, None)
        player.video_set_format_callbacks(ctypes.cast(on_format, vlc.CallbackDecorators.VideoFormatCb),
                                          on_cleanup)
        media = self.instance.media_new_path(path)
        duration_ms = duration_ms if duration_ms and duration_ms > 0 else (info.duration_ms if info else -1)
        if at_ms is not None:
            media.add_option(f":start-time={max(0, at_ms) / 1000:.2f}")
        elif duration_ms and duration_ms > 0:
            # A third in, capped so long films don't need a far seek; skips black intros.
            media.add_option(f":start-time={min(duration_ms * 0.33, 300_000) / 1000:.2f}")
        # Seek previews must show the real frame at that time, so they seek precisely (decode from
        # the keyframe before); the library thumbnail only needs some frame and seeks fast.
        for opt in (":no-audio", ":no-spu") + (() if at_ms is not None else (":input-fast-seek",)):
            media.add_option(opt)
        player.set_media(media)
        try:
            player.play()
            deadline = time.monotonic() + self.FRAME_TIMEOUT_S
            while time.monotonic() < deadline:
                with lock:
                    if state["data"] is not None:
                        break
                if player.get_state() in (vlc.State.Error, vlc.State.Ended):
                    break
                time.sleep(0.02)
        finally:
            player.stop()
            player.release()
            media.release()
        data, size = state["data"], state["size"]
        if data is None or size is None:
            return False
        tw, th = size
        write_png(out_path, tw, th, bgra_to_rgb(data, tw, th, tw * 4))
        return True


class VlcTranscoder(TranscodeEngine):
    """Conversions, recordings and HTTP streaming on a separate libVLC instance.

    Implements ``converter.TranscodeEngine``: each job is a media player
    whose media carries a ``:sout=`` chain, so nothing is shown or heard and
    the main player is never touched. Several jobs can run at once.
    """

    PARSE_TIMEOUT_MS = 5000

    def __init__(self, args: list[str] | None = None) -> None:
        self.vlc = import_vlc()
        base = ["--quiet", "--no-video-title-show", "--no-sub-autodetect-file", "--no-stats",
                "--no-metadata-network-access"]
        if sys.platform.startswith("linux"):
            base.append("--no-xlib")
        self.instance = self.vlc.Instance(base + list(args or []))
        if self.instance is None:
            raise LibVlcNotFound("libVLC failed to initialise for conversions")
        self._jobs: list[tuple[object, object]] = []
        vlc = self.vlc
        self._states = {vlc.State.NothingSpecial: "opening", vlc.State.Opening: "opening",
                        vlc.State.Buffering: "running", vlc.State.Playing: "running",
                        vlc.State.Paused: "running", vlc.State.Stopped: "ended",
                        vlc.State.Ended: "ended", vlc.State.Error: "error"}

    def start(self, mrl: str, options: list[str]):
        media = self.instance.media_new(mrl)
        for opt in options:
            media.add_option(opt)
        player = self.instance.media_player_new()
        player.set_media(media)
        handle = (player, media)
        self._jobs.append(handle)
        if player.play() != 0:
            raise RuntimeError("libVLC refused to start the job")
        return handle

    def poll(self, handle):
        from .converter import EngineStatus

        player = handle[0]
        return EngineStatus(self._states.get(player.get_state(), "opening"), player.get_time(), player.get_length())

    def stop(self, handle) -> None:
        if handle not in self._jobs:
            return
        self._jobs.remove(handle)
        player, media = handle
        player.stop()  # closes the stream output: muxers write their index here
        player.release()
        media.release()

    def media_info(self, path_or_mrl: str):
        from .media_sources import is_url
        from .transcode import SourceInfo

        vlc = self.vlc
        local = not is_url(path_or_mrl)
        media = self.instance.media_new_path(path_or_mrl) if local else self.instance.media_new(path_or_mrl)
        try:
            media.parse_with_options(vlc.MediaParseFlag.local if local else vlc.MediaParseFlag.network,
                                     self.PARSE_TIMEOUT_MS)
            deadline = time.monotonic() + self.PARSE_TIMEOUT_MS / 1000 + 1
            while media.get_parsed_status() == 0 and time.monotonic() < deadline:
                time.sleep(0.01)
            info = SourceInfo(duration_ms=media.get_duration())
            for t in _read_tracks(vlc, media):
                code = _fourcc(t["codec"])
                if t["type"] == 1:
                    info.video.append(code)
                elif t["type"] == 0:
                    info.audio.append(code)
                elif t["type"] == 2:
                    info.subtitles += 1
            return info
        finally:
            media.release()

    def has_module(self, name: str) -> bool | None:
        core = _load_libvlccore(self.vlc)
        if core is None:
            return None
        try:
            fn = core.module_exists
            fn.restype = ctypes.c_bool
            fn.argtypes = [ctypes.c_char_p]
            return bool(fn(name.encode()))
        except Exception:
            return None

    def version(self) -> str:
        return _decode(self.vlc.libvlc_get_version())

    def release(self) -> None:
        for handle in list(self._jobs):
            try:
                self.stop(handle)
            except Exception:
                pass
        try:
            self.instance.release()
        except Exception:
            pass
