import json

import pytest

from vlclone.core.effects import (BUILTIN_EQ_PRESETS, EQ_BANDS, Debouncer, Effects, EffectsStore, EngineFilters,
                                  EqPresetStore, VideoEffects, clean_bands, engine_filters, transform_type)


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


# ---------------------------------------------------------------- presets
def test_builtin_presets_complete():
    assert len(BUILTIN_EQ_PRESETS) == 18
    assert {"Flat", "Rock", "Pop", "Classical", "Techno", "Full bass"} <= set(BUILTIN_EQ_PRESETS)
    for preamp, bands in BUILTIN_EQ_PRESETS.values():
        assert len(bands) == EQ_BANDS and -20 <= preamp <= 20


def test_eq_preset_store_save_load_delete(tmp_path):
    path = tmp_path / "eq.json"
    store = EqPresetStore(path)
    assert store.names() == []
    store.save("  My Bass ", 6.0, [3.0] * EQ_BANDS)
    store.save("vocals", 99, [1, 2, 3])  # clamped / padded
    again = EqPresetStore(path)
    assert again.names() == ["My Bass", "vocals"]
    assert again.get("My Bass") == (6.0, [3.0] * EQ_BANDS)
    preamp, bands = again.get("vocals")
    assert preamp == 20.0 and bands == [1.0, 2.0, 3.0] + [0.0] * 7
    assert again.delete("vocals") and not again.delete("vocals")
    assert EqPresetStore(path).names() == ["My Bass"]


def test_eq_preset_store_rejects_bad_names(tmp_path):
    store = EqPresetStore(tmp_path / "eq.json")
    with pytest.raises(ValueError):
        store.save("   ", 0, [])
    with pytest.raises(ValueError):
        store.save("rock", 0, [])  # built-in, case-insensitive


def test_eq_preset_store_ignores_corrupt_entries(tmp_path):
    path = tmp_path / "eq.json"
    path.write_text(json.dumps({"ok": {"preamp": 1, "bands": [1] * 10}, "bad": 5, "": {}, "x": {"bands": "no"}}))
    store = EqPresetStore(path)
    assert store.names() == ["ok", "x"]
    assert store.get("x") == (0.0, [0.0] * 10)


def test_clean_bands():
    assert clean_bands(None) == [0.0] * 10
    assert clean_bands([50, -50, float("nan")] + [0] * 20) == [20.0, -20.0, 0.0] + [0.0] * 7


# ------------------------------------------------------------- effects model
def test_effects_round_trip_and_validation():
    fx = Effects()
    fx.audio.eq_enabled = True
    fx.audio.eq_bands = [1.5] * 10
    fx.audio.stereo_mode = "mono"
    fx.video.brightness = 1.4
    fx.video.flip_h = True
    again = Effects.from_dict(json.loads(json.dumps(fx.to_dict())))
    assert again == fx

    bad = Effects.from_dict({"audio": {"stereo_mode": "surround", "eq_enabled": 1, "normalize_level": 99},
                             "video": {"hue": 999, "gamma": "x", "sharpen": "yes"}})
    assert bad.audio.stereo_mode == "stereo" and bad.audio.eq_enabled is False
    assert bad.audio.normalize_level == 10.0
    assert bad.video.hue == 180.0 and bad.video.gamma == 1.0 and bad.video.sharpen is False
    assert Effects.from_dict("garbage") == Effects()


def test_adjust_active():
    v = VideoEffects()
    assert not v.adjust_active
    v.gamma = 1.2
    assert v.adjust_active


def test_effects_store_per_file(tmp_path):
    path = tmp_path / "fx.json"
    store = EffectsStore(path)
    fx = Effects()
    fx.video.contrast = 1.5
    store.put("file:///a.mkv", fx)
    store.save()
    again = EffectsStore(path)
    assert again.get("file:///a.mkv").video.contrast == 1.5
    assert again.get("file:///b.mkv") is None
    again.forget("file:///a.mkv")
    again.save()
    assert EffectsStore(path).get("file:///a.mkv") is None


# -------------------------------------------------------- transform / filters
@pytest.mark.parametrize("rotation,h,v,expected", [
    (0, False, False, None), (90, False, False, "90"), (270, False, False, "270"),
    (0, True, False, "hflip"), (0, False, True, "vflip"), (0, True, True, "180"),
    (180, True, True, None), (90, True, False, "antitranspose"), (270, True, False, "transpose"),
    (90, False, True, "transpose"), (180, True, False, "vflip"),
])
def test_transform_type(rotation, h, v, expected):
    assert transform_type(rotation, h, v) == expected


def test_engine_filters_args():
    assert EngineFilters().vlc_args() == []
    assert EngineFilters().is_default
    args = EngineFilters("hflip", 0.75).vlc_args()
    assert args == ["--video-filter=transform:sharpen", "--transform-type=hflip", "--sharpen-sigma=0.75"]
    video = VideoEffects(sharpen=True, sharpen_strength=1.234)
    assert engine_filters(90, video) == EngineFilters("90", 1.23)
    video.sharpen = False
    assert engine_filters(0, video) == EngineFilters()


# ---------------------------------------------------------------- debounce
def test_debouncer_batches_requests():
    clock = Clock()
    d = Debouncer(0.5, clock)
    assert not d.poll() and not d.pending
    d.request()
    clock.t += 0.3
    d.request()  # restarts the quiet period
    clock.t += 0.3
    assert not d.poll()
    clock.t += 0.25
    assert d.poll()
    assert not d.poll()  # fires once


def test_debouncer_flush_and_cancel():
    clock = Clock()
    d = Debouncer(0.5, clock)
    assert not d.flush()
    d.request()
    assert d.flush() and not d.pending
    d.request()
    d.cancel()
    clock.t += 1
    assert not d.poll()
