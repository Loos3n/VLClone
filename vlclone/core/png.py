"""Minimal PNG writer for thumbnails (keeps the core free of Qt and Pillow)."""

from __future__ import annotations

import os
import struct
import tempfile
import zlib
from pathlib import Path


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def encode_png(width: int, height: int, rgb: bytes) -> bytes:
    """Encode 8-bit RGB pixels (``width * height * 3`` bytes, rows top to bottom)."""
    if width <= 0 or height <= 0 or len(rgb) < width * height * 3:
        raise ValueError("bad image size")
    stride = width * 3
    raw = b"".join(b"\x00" + rgb[y * stride:(y + 1) * stride] for y in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header) + _chunk(b"IDAT", zlib.compress(raw, 6))
            + _chunk(b"IEND", b""))


def write_png(path: str | Path, width: int, height: int, rgb: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(encode_png(width, height, rgb))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def bgra_to_rgb(data: bytes, width: int, height: int, pitch: int | None = None) -> bytes:
    """libVLC "RV32" frames are B, G, R, X bytes per pixel."""
    pitch = pitch or width * 4
    if pitch != width * 4:
        data = b"".join(data[y * pitch:y * pitch + width * 4] for y in range(height))
    src = memoryview(bytes(data[:width * height * 4]))
    rgb = bytearray(width * height * 3)
    rgb[0::3] = src[2::4]
    rgb[1::3] = src[1::4]
    rgb[2::3] = src[0::4]
    return bytes(rgb)


def png_size(path: str | Path) -> tuple[int, int] | None:
    try:
        with open(path, "rb") as fh:
            head = fh.read(24)
    except OSError:
        return None
    if len(head) < 24 or not head.startswith(b"\x89PNG"):
        return None
    return struct.unpack(">II", head[16:24])
