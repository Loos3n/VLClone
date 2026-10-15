"""libVLC implementation of :class:`MediaBackend` (via python-vlc).

This is the only module in the project that imports ``vlc``.
"""

from __future__ import annotations

import ctypes
import os
import struct
import sys

from .backend import MediaBackend, MediaInfo, PlaybackState, StreamInfo, Track


class LibVlcNotFound(RuntimeError):
    pass


def _prepare_windows_search() -> None:
    """Help python-vlc find a 64-bit VLC install on Windows."""
    if sys.platform != "win32" or os.environ.get("PYTHON_VLC_LIB_PATH"):
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
        dll = os.path.join(folder, "libvlc.dll")
        if os.path.isfile(dll):
            os.environ["PYTHON_VLC_LIB_PATH"] = dll
            os.environ.setdefault("PYTHON_VLC_MODULE_PATH", os.path.join(folder, "plugins"))
            if hasattr(os, "add_dll_directory"):
                os.add_dll_directory(folder)
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


class VlcBackend(MediaBackend):
    def __init__(self, args: list[str] | None = None) -> None:
        self.vlc = import_vlc()
        args = list(args or [])
        if sys.platform.startswith("linux") and "--no-xlib" not in args:
            args.append("--no-xlib")  # we embed via XCB; Xlib needs XInitThreads
        self.instance = self.vlc.Instance(args)
        if self.instance is None:
            # Bad user arguments make Instance() fail; fall back to defaults.
            self.instance = self.vlc.Instance(["--quiet"])
        self.player = self.instance.media_player_new()
        self.player.video_set_mouse_input(False)
        self.player.video_set_key_input(False)
        self._media = None
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

    # ------------------------------------------------------------ lifecycle
    def set_video_output(self, handle: int) -> None:
        if sys.platform == "win32":
            self.player.set_hwnd(handle)
        elif sys.platform == "darwin":
            self.player.set_nsobject(handle)
        else:
            self.player.set_xwindow(handle)

    def release(self) -> None:
        try:
            self.player.stop()
            self.player.release()
            self.instance.release()
        except Exception:
            pass

    # ---------------------------------------------------------------- media
    def load(self, mrl: str, options: list[str] | None = None) -> None:
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

    def rotation_options(self, degrees: int) -> list[str]:
        if degrees % 360 == 0:
            return []
        return [":video-filter=transform", f":transform-type={degrees % 360}"]

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
