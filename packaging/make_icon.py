"""Render the app's vector cone icon into a multi-size Windows .ico."""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402
from PySide6.QtCore import QBuffer, QIODevice  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from vlclone.ui.icons import icon  # noqa: E402


def main(out: str = "build/vlclone.ico") -> None:
    app = QApplication.instance() or QApplication([])  # noqa: F841 (needed for pixmaps)
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    icon("cone").pixmap(256, 256).save(buf, "PNG")
    image = Image.open(io.BytesIO(bytes(buf.data())))
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    image.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"wrote {out}")


if __name__ == "__main__":
    main(*sys.argv[1:])
