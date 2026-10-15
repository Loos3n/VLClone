"""libVLC implementation of :class:`MediaBackend` (via python-vlc).

This is the only module in the project that imports ``vlc``.
"""

from __future__ import annotations

import ctypes
import os
import struct
import sys

from collections import OrderedDict

from .backend import MediaBackend, MediaInfo, PlaybackState, StreamInfo, Track
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
        media = self.instance.media_new(mrl)
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
