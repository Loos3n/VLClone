"""Download the official VLC Windows build and stage the parts libVLC needs.

Usage: python packaging/fetch_vlc.py [--version 3.0.21] [--out build/vlc]

Produces <out>/libvlc.dll, libvlccore.dll, plugins/ and VLC's license, ready
to be bundled next to the app. The zip is verified against the SHA-256 that
VideoLAN publishes beside it.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

BASE = "https://download.videolan.org/pub/videolan/vlc/{v}/win64/vlc-{v}-win64.zip"
# Plugins that only matter to VLC's own interface; VLClone is the interface.
DROP_PLUGIN_DIRS = {"gui", "control", "visualization"}


def download(url: str, dest: Path) -> None:
    print(f"downloading {url}")
    with urllib.request.urlopen(url, timeout=120) as resp, open(dest, "wb") as fh:
        shutil.copyfileobj(resp, fh)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="3.0.21")
    ap.add_argument("--out", default="build/vlc")
    ap.add_argument("--cache", default="build/downloads")
    args = ap.parse_args()

    cache = Path(args.cache)
    cache.mkdir(parents=True, exist_ok=True)
    url = BASE.format(v=args.version)
    archive = cache / url.rsplit("/", 1)[-1]
    checksum = cache / (archive.name + ".sha256")
    if not checksum.exists():
        download(url + ".sha256", checksum)
    expected = checksum.read_text().split()[0].lower()
    if not archive.exists() or sha256(archive) != expected:
        download(url, archive)
    actual = sha256(archive)
    if actual != expected:
        print(f"checksum mismatch: {actual} != {expected}", file=sys.stderr)
        return 1
    print(f"verified sha256 {actual}")

    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    root = f"vlc-{args.version}/"
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            if not info.filename.startswith(root) or info.is_dir():
                continue
            rel = info.filename[len(root):]
            parts = rel.split("/")
            keep = (
                rel in ("libvlc.dll", "libvlccore.dll", "vlc-cache-gen.exe", "COPYING.txt")
                or (parts[0] == "plugins" and len(parts) > 2 and parts[1] not in DROP_PLUGIN_DIRS)
                or (parts[0] == "lua")  # playlist scripts (e.g. YouTube links)
            )
            if not keep:
                continue
            target = out / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
    (out / "COPYING.txt").rename(out / "VLC-LICENSE.txt")
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"staged libVLC {args.version} in {out} ({size / 1e6:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
