"""Windows taskbar: previous / play-pause / next under the window's thumbnail, and progress.

Uses ``ITaskbarList3`` through ``ctypes`` COM calls (Qt 6 dropped
QWinTaskbarButton). Buttons can only be added after Explorer sends the
registered "TaskbarButtonCreated" message to the window, and clicks arrive
as ``WM_COMMAND`` with ``THBN_CLICKED``; the window forwards its native
messages to :meth:`TaskbarControls.handle_message`.
"""

from __future__ import annotations

import ctypes
import logging
import uuid
from ctypes import POINTER, byref, c_void_p
from typing import Callable

from ..core.desktop import (PROGRESS_INDETERMINATE, PROGRESS_NONE, PROGRESS_NORMAL, PROGRESS_PAUSED,
                            TaskbarState)

log = logging.getLogger("vlclone.taskbar")

if hasattr(ctypes, "WINFUNCTYPE"):
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16),
                    ("Data4", ctypes.c_ubyte * 8)]

    class THUMBBUTTON(ctypes.Structure):
        _fields_ = [("dwMask", wintypes.DWORD), ("iId", wintypes.UINT), ("iBitmap", wintypes.UINT),
                    ("hIcon", wintypes.HICON), ("szTip", wintypes.WCHAR * 260), ("dwFlags", wintypes.DWORD)]

    class ICONINFO(ctypes.Structure):
        _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD), ("yHotspot", wintypes.DWORD),
                    ("hbmMask", wintypes.HBITMAP), ("hbmColor", wintypes.HBITMAP)]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

CLSID_TASKBARLIST = "56FDF344-FD6D-11d0-958A-006097C9A090"
IID_ITASKBARLIST3 = "EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF"
# ITaskbarList3 vtable slots (IUnknown 0-2, ITaskbarList 3-7, ITaskbarList2 8, ITaskbarList3 9-)
_RELEASE, _HRINIT, _SET_PROGRESS_VALUE, _SET_PROGRESS_STATE = 2, 3, 9, 10
_THUMBBAR_ADD, _THUMBBAR_UPDATE = 15, 16
THB_ICON, THB_TOOLTIP, THB_FLAGS = 0x2, 0x4, 0x8
THBF_ENABLED, THBF_DISABLED = 0x0, 0x1
THBN_CLICKED = 0x1800
WM_COMMAND = 0x0111
TBPF = {PROGRESS_NONE: 0x0, PROGRESS_INDETERMINATE: 0x1, PROGRESS_NORMAL: 0x2, PROGRESS_PAUSED: 0x8}

BUTTONS = [(0, "previous", "Previous"), (1, "play_pause", "Play"), (2, "next", "Next")]


def _guid(text: str) -> "GUID":
    return GUID.from_buffer_copy(uuid.UUID(text).bytes_le)


def _method(ptr, index: int, *argtypes):
    vtbl = ctypes.cast(ptr, POINTER(POINTER(c_void_p)))[0]
    return ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, *argtypes)(vtbl[index])


def create_taskbar_list():
    """CoCreate an ITaskbarList3 and HrInit it; raises OSError if unavailable."""
    ole32 = ctypes.OleDLL("ole32")
    ctypes.windll.ole32.CoInitializeEx(None, 0x2)  # STA; S_FALSE / RPC_E_CHANGED_MODE are fine
    ptr = c_void_p()
    clsid, iid = _guid(CLSID_TASKBARLIST), _guid(IID_ITASKBARLIST3)
    ole32.CoCreateInstance(byref(clsid), None, 0x1, byref(iid), byref(ptr))  # CLSCTX_INPROC_SERVER
    _method(ptr, _HRINIT)(ptr)
    return ptr


def hicon_from_image(image) -> int:
    """A QImage -> HICON (32-bit with alpha)."""
    from PySide6.QtGui import QImage

    img = image.convertToFormat(QImage.Format.Format_ARGB32)
    w, h = img.width(), img.height()
    gdi32, user32 = ctypes.windll.gdi32, ctypes.windll.user32
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.CreateBitmap.restype = wintypes.HBITMAP
    user32.CreateIconIndirect.restype = wintypes.HICON
    bih = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    bits = c_void_p()
    color = gdi32.CreateDIBSection(None, byref(bih), 0, byref(bits), None, 0)
    if not color:
        raise OSError("CreateDIBSection failed")
    ptr = img.constBits()
    data = bytes(ptr) if not hasattr(ptr, "tobytes") else ptr.tobytes()
    ctypes.memmove(bits, data[: w * h * 4], w * h * 4)
    mask = gdi32.CreateBitmap(w, h, 1, 1, None)
    info = ICONINFO(True, 0, 0, mask, color)
    hicon = user32.CreateIconIndirect(byref(info))
    gdi32.DeleteObject(mask)
    gdi32.DeleteObject(color)
    if not hicon:
        raise OSError("CreateIconIndirect failed")
    return hicon


class TaskbarControls:
    """One per top-level window; inert until the taskbar button exists."""

    def __init__(self, hwnd: int, on_command: Callable[[str], None], make_icon: Callable[[str], object]) -> None:
        self.hwnd = wintypes.HWND(hwnd)
        self.on_command = on_command
        self.make_icon = make_icon  # icon name -> QImage (painted at the taskbar's size)
        self._created_msg = ctypes.windll.user32.RegisterWindowMessageW("TaskbarButtonCreated")
        self._ptr = None
        self._icons: dict[str, int] = {}
        self._buttons_added = False
        self._last: TaskbarState | None = None

    # --------------------------------------------------------- native events
    def handle_message(self, msg) -> bool:
        """Feed every native window message (a wintypes.MSG); True if it was ours."""
        if msg.message == self._created_msg:
            self._button_created()
            return False
        if msg.message == WM_COMMAND and (msg.wParam >> 16) & 0xFFFF == THBN_CLICKED:
            button = msg.wParam & 0xFFFF
            for bid, command, _ in BUTTONS:
                if bid == button:
                    self.on_command(command)
                    return True
        return False

    def _button_created(self) -> None:
        try:
            if self._ptr is None:
                self._ptr = create_taskbar_list()
            buttons = self._buttons(self._last)
            _method(self._ptr, _THUMBBAR_ADD, wintypes.HWND, wintypes.UINT, POINTER(THUMBBUTTON))(
                self._ptr, self.hwnd, len(buttons), buttons)
            self._buttons_added = True
            log.info("taskbar thumbnail buttons added")
            if self._last is not None:
                last, self._last = self._last, None
                self.update(last)
        except OSError as exc:
            log.warning("taskbar buttons unavailable: %s", exc)

    # ----------------------------------------------------------------- state
    def _icon(self, name: str) -> int:
        if name not in self._icons:
            self._icons[name] = hicon_from_image(self.make_icon(name))
        return self._icons[name]

    def _buttons(self, state: TaskbarState | None):
        arr = (THUMBBUTTON * len(BUTTONS))()
        play_icon = state.play_icon if state else "play"
        enabled = {"previous": state.previous_enabled if state else False,
                   "play_pause": state.play_enabled if state else True,
                   "next": state.next_enabled if state else False}
        for i, (bid, command, tip) in enumerate(BUTTONS):
            name = play_icon if command == "play_pause" else command
            arr[i].dwMask = THB_ICON | THB_TOOLTIP | THB_FLAGS
            arr[i].iId = bid
            arr[i].hIcon = self._icon(name)
            arr[i].szTip = "Pause" if name == "pause" else ("Play" if name == "play" else tip)
            arr[i].dwFlags = THBF_ENABLED if enabled[command] else THBF_DISABLED
        return arr

    def update(self, state: TaskbarState) -> None:
        if state == self._last:
            return
        previous, self._last = self._last, state
        if self._ptr is None or not self._buttons_added:
            return
        try:
            if previous is None or (previous.play_icon, previous.previous_enabled, previous.next_enabled) != (
                    state.play_icon, state.previous_enabled, state.next_enabled):
                buttons = self._buttons(state)
                _method(self._ptr, _THUMBBAR_UPDATE, wintypes.HWND, wintypes.UINT, POINTER(THUMBBUTTON))(
                    self._ptr, self.hwnd, len(buttons), buttons)
            if previous is None or previous.progress != state.progress:
                _method(self._ptr, _SET_PROGRESS_STATE, wintypes.HWND, ctypes.c_int)(
                    self._ptr, self.hwnd, TBPF[state.progress])
            if state.progress in (PROGRESS_NORMAL, PROGRESS_PAUSED):
                _method(self._ptr, _SET_PROGRESS_VALUE, wintypes.HWND, ctypes.c_ulonglong, ctypes.c_ulonglong)(
                    self._ptr, self.hwnd, state.value, 1000)
        except OSError as exc:
            log.warning("taskbar update failed: %s", exc)

    def close(self) -> None:
        if self._ptr is not None:
            try:
                _method(self._ptr, _SET_PROGRESS_STATE, wintypes.HWND, ctypes.c_int)(
                    self._ptr, self.hwnd, 0)
                ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p)(
                    ctypes.cast(self._ptr, POINTER(POINTER(c_void_p)))[0][_RELEASE])(self._ptr)
            except OSError:
                pass
            self._ptr = None
        for hicon in self._icons.values():
            ctypes.windll.user32.DestroyIcon(wintypes.HICON(hicon))
        self._icons.clear()
