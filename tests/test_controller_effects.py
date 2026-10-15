"""Effects behaviour of PlayerController, driven through the fake engine."""

import pytest

from vlclone.core.backend import PlaybackState
from vlclone.core.controller import PlayerController
from vlclone.core.effects import EffectsStore, EngineFilters, EqPresetStore
from vlclone.core.fake_backend import FakeBackend
from vlclone.core.settings import Settings


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def ctl(tmp_path, clock):
    return PlayerController(FakeBackend(), Settings(), effects_store=EffectsStore(tmp_path / "fx.json"),
                            eq_presets=EqPresetStore(tmp_path / "eq.json"), clock=clock)


def playing(ctl, urls=("http://x/a.mp4",)):
    ctl.open(list(urls))
    ctl.tick()
    assert ctl.state is PlaybackState.PLAYING
    return ctl


# ----------------------------------------------------------------- live audio
def test_equalizer_is_live(ctl):
    playing(ctl)
    loads = len(ctl.backend.loads)
    ctl.apply_eq_preset("Rock")
    assert ctl.backend.equalizer[1][0] == 8.0
    assert ctl.effects.audio.eq_enabled and ctl.effects.audio.eq_preset == "Rock"
    ctl.set_eq_band(0, 3.0)
    assert ctl.backend.equalizer[1][0] == 3.0
    assert ctl.effects.audio.eq_preset == ""  # edited by hand -> custom
    ctl.set_eq_preamp(-4)
    assert ctl.backend.equalizer[0] == -4
    ctl.set_eq_enabled(False)
    assert ctl.backend.equalizer is None
    assert len(ctl.backend.loads) == loads  # never reloads


def test_custom_preset_save_and_apply(ctl):
    playing(ctl)
    ctl.set_eq_band(3, 6.0)
    ctl.save_eq_preset("Mine")
    assert ctl.custom_eq_presets() == ["Mine"]
    ctl.apply_eq_preset("Flat")
    assert ctl.backend.equalizer[1][3] == 0.0
    assert ctl.apply_eq_preset("Mine")
    assert ctl.backend.equalizer[1][3] == 6.0
    with pytest.raises(ValueError):
        ctl.save_eq_preset("Pop")
    assert ctl.delete_eq_preset("Mine") and ctl.effects.audio.eq_preset == ""
    assert not ctl.apply_eq_preset("Mine")


def test_normalization_stereo_mode(ctl):
    playing(ctl)
    ctl.set_normalization(True)
    ctl.set_normalization_level(4)
    assert ctl.backend.normalization == (True, 4.0)
    ctl.set_stereo_mode("mono")
    assert ctl.backend.stereo_mode == 7
    ctl.set_stereo_mode("bogus")
    assert ctl.effects.audio.stereo_mode == "mono"


def test_normalization_does_not_touch_volume_boost(ctl):
    playing(ctl)
    ctl.set_volume(150)
    ctl.set_normalization(True)
    assert ctl.backend.volume() == 150 and ctl.volume == 150


def test_unsupported_normalization_is_not_sent(ctl):
    ctl.backend.normalization_supported = False
    playing(ctl)
    ctl.set_normalization(True)
    assert ctl.backend.normalization == (False, 2.0)
    assert ctl.effects.audio.normalize  # remembered for an engine that supports it


# ----------------------------------------------------------------- live video
def test_image_adjust_is_live_and_resettable(ctl):
    playing(ctl)
    loads = len(ctl.backend.loads)
    ctl.set_adjust("brightness", 1.3)
    ctl.set_adjust("hue", 400)
    assert ctl.backend.adjust["brightness"] == 1.3 and ctl.backend.adjust["hue"] == 180
    ctl.reset_adjust("hue")
    assert ctl.backend.adjust["hue"] == 0
    ctl.reset_adjust()
    assert ctl.backend.adjust is None  # neutral -> adjust filter off
    assert len(ctl.backend.loads) == loads


# -------------------------------------------------------- reload + debounce
def test_reload_filters_are_batched_into_one_reload(ctl, clock):
    playing(ctl)
    ctl.seek(30_000)
    loads = len(ctl.backend.loads)
    pending = []
    ctl.reload_pending.connect(pending.append)
    ctl.set_flip(horizontal=True)
    ctl.set_sharpen(True)
    for s in (0.3, 0.6, 0.9, 1.2):
        ctl.set_sharpen_strength(s)
        clock.t += 0.2
        ctl.tick()
    assert len(ctl.backend.loads) == loads  # still within the quiet period
    clock.t += ctl.RELOAD_DEBOUNCE_S
    ctl.tick()
    assert len(ctl.backend.loads) == loads + 1  # exactly one reload
    assert ctl.backend.filters == EngineFilters("hflip", 1.2)
    ctl.tick()
    assert ctl.time() >= 30_000  # position kept
    assert pending[0] is True and pending[-1] is False


def test_rotate_and_flip_share_one_reload(ctl, clock):
    playing(ctl)
    loads = len(ctl.backend.loads)
    ctl.rotate()
    ctl.toggle_flip_vertical()
    clock.t += 1
    ctl.tick()
    assert len(ctl.backend.loads) == loads + 1
    assert ctl.backend.filters.transform == "transpose"


def test_toggling_back_skips_the_reload(ctl, clock):
    playing(ctl)
    loads = len(ctl.backend.loads)
    ctl.toggle_flip_horizontal()
    ctl.toggle_flip_horizontal()
    clock.t += 1
    ctl.tick()
    assert len(ctl.backend.loads) == loads


def test_reload_keeps_pause_and_reapplies_live_effects(ctl, clock):
    playing(ctl)
    ctl.apply_eq_preset("Pop")
    ctl.set_adjust("contrast", 1.5)
    ctl.pause()
    ctl.set_sharpen(True)
    ctl.backend.equalizer = None  # a new libVLC player starts without them
    ctl.backend.adjust = None
    ctl.apply_pending_reload()
    ctl.tick()
    assert ctl.backend.state() is PlaybackState.PAUSED
    assert ctl.backend.equalizer is not None and ctl.backend.adjust["contrast"] == 1.5


def test_filter_changes_without_media_apply_on_next_load(ctl):
    ctl.set_flip(vertical=True)
    assert not ctl.reload_debounce.pending
    playing(ctl)
    assert ctl.backend.filters.transform == "vflip"


def test_new_media_cancels_pending_reload(ctl, clock):
    playing(ctl, ["http://x/a.mp4", "http://x/b.mp4"])
    ctl.set_sharpen(True)
    ctl.next()
    assert not ctl.reload_debounce.pending
    loads = len(ctl.backend.loads)
    clock.t += 1
    ctl.tick()
    assert len(ctl.backend.loads) == loads
    assert ctl.backend.filters.sharpen is not None


def test_reset_all(ctl, clock):
    playing(ctl)
    ctl.apply_eq_preset("Rock")
    ctl.set_flip(horizontal=True)
    ctl.set_adjust("gamma", 2)
    ctl.set_stereo_mode("left")
    ctl.apply_pending_reload()
    ctl.tick()
    ctl.reset_all_effects()
    assert ctl.backend.equalizer is None and ctl.backend.adjust is None and ctl.backend.stereo_mode == 1
    ctl.apply_pending_reload()
    assert ctl.backend.filters == EngineFilters()


# -------------------------------------------------------------- persistence
def test_global_effects_persist_in_settings(tmp_path):
    settings = Settings()
    ctl = PlayerController(FakeBackend(), settings)
    playing(ctl)
    ctl.apply_eq_preset("Live")
    ctl.set_adjust("saturation", 2.0)
    ctl.set_flip(horizontal=True)
    path = tmp_path / "settings.json"
    settings.save(path)
    restored = Settings.load(path)
    ctl2 = PlayerController(FakeBackend(), restored)
    assert ctl2.effects.audio.eq_preset == "Live"
    assert ctl2.effects.video.saturation == 2.0
    playing(ctl2)
    assert ctl2.backend.equalizer is not None and ctl2.backend.adjust["saturation"] == 2.0
    assert ctl2.backend.filters.transform == "hflip"


def test_per_file_effects(ctl, tmp_path):
    ctl.set_effects_scope("per_file")
    playing(ctl, ["http://x/a.mp4", "http://x/b.mp4"])
    ctl.set_adjust("brightness", 1.5)
    ctl.next()
    ctl.tick()
    assert ctl.effects.video.brightness == 1.0  # b starts from the global defaults
    assert ctl.backend.adjust is None
    ctl.set_adjust("contrast", 0.5)
    ctl.previous()
    ctl.tick()
    assert ctl.effects.video.brightness == 1.5 and ctl.effects.video.contrast == 1.0
    assert ctl.backend.adjust["brightness"] == 1.5
    ctl.shutdown()
    store = EffectsStore(tmp_path / "fx.json")
    assert store.get("http://x/a.mp4").video.brightness == 1.5
    assert store.get("http://x/b.mp4").video.contrast == 0.5


def test_switching_back_to_global_restores_global_set(ctl):
    playing(ctl)
    ctl.set_adjust("gamma", 2.0)  # global
    ctl.set_effects_scope("per_file")
    ctl.set_adjust("gamma", 3.0)  # this file only
    ctl.set_effects_scope("global")
    assert ctl.effects.video.gamma == 2.0
    assert ctl.backend.adjust["gamma"] == 2.0


def test_untouched_stereo_mode_is_never_forced(ctl):
    ctl.backend.stereo_mode = 0  # "unset": keep the file's native layout
    playing(ctl, ["http://x/a.mp4", "http://x/b.mp4"])
    ctl.apply_eq_preset("Rock")
    ctl.next()
    ctl.tick()
    assert ctl.backend.stereo_mode == 0
