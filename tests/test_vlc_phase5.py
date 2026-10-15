"""Phase 5 against the real libVLC (skipped without it): thumbnails are real RGB frames, and
seek previews show the frame at the requested time."""

import pytest

from vlclone.selftest_media import write_y4m

vlc_backend = pytest.importorskip("vlclone.core.vlc_backend")


@pytest.fixture
def probe():
    try:
        p = vlc_backend.VlcMediaProbe()
    except vlc_backend.LibVlcNotFound as exc:
        pytest.skip(str(exc))
    yield p
    p.close()


def _pixels(path):
    from PySide6.QtGui import QImage

    img = QImage(str(path))
    assert not img.isNull()
    return img, [img.pixelColor(x, y) for y in range(0, img.height(), 3) for x in range(0, img.width(), 3)]


def test_thumbnail_is_rgb_not_planar_luma(probe, tmp_path):
    # Regression: the chroma request never reached libVLC, so thumbnails were I420 luma bytes
    # read as RGB (grey, horizontally squashed). The clip's chroma is reddish (V > 128).
    clip = tmp_path / "clip.y4m"
    write_y4m(str(clip), 128, 96, frames=60)
    out = tmp_path / "thumb.png"
    assert probe.thumbnail(str(clip), str(out), 64)
    img, px = _pixels(out)
    assert (img.width(), img.height()) == (64, 48)
    tint = sum(c.red() - c.blue() for c in px) / len(px)
    assert tint > 15, f"thumbnail looks grey/planar (mean R-B {tint:.1f})"


def test_seek_preview_frame_at_time(probe, tmp_path):
    clip = tmp_path / "clip.y4m"
    write_y4m(str(clip), 128, 96, frames=100)  # 4 s; the bars drift every frame
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    assert probe.thumbnail(str(clip), str(a), 64, at_ms=200)
    assert probe.thumbnail(str(clip), str(b), 64, at_ms=2600)
    _, pa = _pixels(a)
    _, pb = _pixels(b)
    assert [c.rgb() for c in pa] != [c.rgb() for c in pb]
