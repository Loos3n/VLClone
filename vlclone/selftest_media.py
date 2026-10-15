"""Tiny media files generated in pure Python, for the packaged self-test and tests.

No ffmpeg needed: a Matroska file with PCM audio and chapters (exercises
libVLC's chapter API), a YUV4MPEG2 video (exercises thumbnails and video
metadata) and a WAV tone.
"""

from __future__ import annotations

import math
import struct
import wave


# --------------------------------------------------------------- Matroska
def _vint_size(n: int) -> bytes:
    return b"\x01" + n.to_bytes(7, "big")  # 8-byte size field: always valid


def _el(element_id: int, payload: bytes) -> bytes:
    id_bytes = element_id.to_bytes((element_id.bit_length() + 7) // 8, "big")
    return id_bytes + _vint_size(len(payload)) + payload


def _uint(element_id: int, value: int) -> bytes:
    return _el(element_id, value.to_bytes(max(1, (value.bit_length() + 7) // 8), "big"))


def _float(element_id: int, value: float) -> bytes:
    return _el(element_id, struct.pack(">d", value))


def _str(element_id: int, value: str) -> bytes:
    return _el(element_id, value.encode("utf-8"))


def write_chapter_mkv(path: str, seconds: float = 6.0, chapters: list[tuple[str, int]] | None = None,
                      rate: int = 8000, title: str = "VLClone chapter test") -> None:
    """Mono 16-bit PCM tone in Matroska with chapters [(name, start ms), …]."""
    chapters = chapters or [("Intro", 0), ("Middle", 2000), ("End", 4000)]
    duration_ms = int(seconds * 1000)
    ebml = _el(0x1A45DFA3, _uint(0x4286, 1) + _uint(0x42F7, 1) + _uint(0x42F2, 4) + _uint(0x42F3, 8)
               + _str(0x4282, "matroska") + _uint(0x4287, 4) + _uint(0x4285, 2))
    info = _el(0x1549A966, _uint(0x2AD7B1, 1_000_000) + _float(0x4489, float(duration_ms))
               + _str(0x4D80, "VLClone") + _str(0x5741, "VLClone") + _str(0x7BA9, title))
    audio = _el(0xE1, _float(0xB5, float(rate)) + _uint(0x9F, 1) + _uint(0x6264, 16))
    tracks = _el(0x1654AE6B, _el(0xAE, _uint(0xD7, 1) + _uint(0x73C5, 1) + _uint(0x83, 2)
                                 + _str(0x86, "A_PCM/INT/LIT") + audio))
    atoms = b""
    for n, (name, start) in enumerate(chapters):
        end = chapters[n + 1][1] if n + 1 < len(chapters) else duration_ms
        display = _el(0x80, _str(0x85, name) + _str(0x437C, "eng"))
        atoms += _el(0xB6, _uint(0x73C4, n + 1) + _uint(0x91, start * 1_000_000) + _uint(0x92, end * 1_000_000)
                     + display)
    chapters_el = _el(0x1043A770, _el(0x45B9, _uint(0x45BC, 1) + atoms))
    clusters = b""
    block_ms = 100
    samples_per_block = rate * block_ms // 1000
    for cluster_start in range(0, duration_ms, 1000):
        blocks = b""
        for offset in range(0, min(1000, duration_ms - cluster_start), block_ms):
            first = (cluster_start + offset) * rate // 1000
            pcm = b"".join(struct.pack("<h", int(9000 * math.sin(2 * math.pi * 440 * (first + i) / rate)))
                           for i in range(samples_per_block))
            blocks += _el(0xA3, b"\x81" + struct.pack(">h", offset) + b"\x80" + pcm)
        clusters += _el(0x1F43B675, _uint(0xE7, cluster_start) + blocks)
    segment = _el(0x18538067, info + tracks + chapters_el + clusters)
    with open(path, "wb") as fh:
        fh.write(ebml + segment)


# --------------------------------------------------------------- YUV4MPEG2
def write_y4m(path: str, width: int = 64, height: int = 48, frames: int = 50, fps: int = 25) -> None:
    """Uncompressed 4:2:0 video: vertical bars that drift each frame.

    The palette avoids 0x47 so libVLC's MPEG-TS probe never mistakes the
    picture data for TS sync bytes.
    """
    luma = [235, 210, 170, 145, 106, 81, 41, 16]
    with open(path, "wb") as fh:
        fh.write(f"YUV4MPEG2 W{width} H{height} F{fps}:1 Ip A1:1 C420jpeg\n".encode())
        cw, ch = width // 2, height // 2
        for f in range(frames):
            row = bytes(luma[((x + f) * len(luma) // width) % len(luma)] for x in range(width))
            fh.write(b"FRAME\n" + row * height)
            fh.write(bytes([96 + f % 32]) * (cw * ch))
            fh.write(bytes([160 - f % 32]) * (cw * ch))


# --------------------------------------------------------------------- WAV
def write_tone(path: str, seconds: float = 3.0, rate: int = 22050) -> None:
    with wave.open(path, "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        frames = bytearray()
        for n in range(int(rate * seconds)):
            sample = int(12000 * math.sin(2 * math.pi * 440 * n / rate))
            frames += struct.pack("<hh", sample, sample)
        wav.writeframes(bytes(frames))
