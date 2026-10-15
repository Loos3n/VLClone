"""Phase 5 core: settings migration, update check, handoff protocol, window restore, desktop state."""

import json

import pytest

from vlclone import __version__
from vlclone.core import associations as assoc
from vlclone.core.backend import PlaybackState
from vlclone.core.desktop import (CommandDebouncer, SessionState, should_inhibit_sleep, status_for,
                                  taskbar_state)
from vlclone.core.instance import HandoffBatcher, HandoffMessage, decode_stream, server_name
from vlclone.core.settings import SECTIONS, SETTINGS_VERSION, Settings, migrate, migrate_shortcuts
from vlclone.core.shortcuts import DEFAULT_SHORTCUTS, find_conflicts, resolve_shortcuts
from vlclone.core.updates import (Release, UpdateChecker, check_for_update, is_due, is_newer,
                                  parse_release, parse_version)
from vlclone.core.windowstate import Rect, SavedWindow, ScreenInfo, plan_mini, plan_restore


# ------------------------------------------------------------------ settings migration
def _write(path, data):
    path.write_text(json.dumps(data))
    return path


def test_fresh_install_is_first_run(tmp_path):
    s = Settings.load(tmp_path / "missing.json")
    assert s.first_run_done is False
    assert s.settings_version == SETTINGS_VERSION
    assert s.theme == "dark" and s.single_instance and s.instance_open_mode == "play"


def test_phase4_settings_file_is_migrated(tmp_path):
    # A settings.json as written by phases 1-4: no version, the old geometry blob, custom shortcuts.
    old = {"volume": 80, "always_on_top": True, "window_geometry": "AdnQywADAAA=",
           "shortcuts": {"effects": ["Ctrl+Shift+M"], "stop": ["X", "Media Stop"]},
           "playlist_width": 300}
    s = Settings.load(_write(tmp_path / "s.json", old))
    assert s.first_run_done is True        # existing users skip the welcome screen
    assert s.volume == 80 and s.always_on_top is True and s.playlist_width == 300
    assert s.window_geometry == "AdnQywADAAA=" and s.window_state == {}
    assert s.settings_version == SETTINGS_VERSION
    # The user's Ctrl+Shift+M stays on Effects; the new mini player default gives it up.
    bindings = resolve_shortcuts(s.shortcuts)
    assert bindings["effects"] == ["Ctrl+Shift+M"]
    assert "Ctrl+Shift+M" not in bindings["mini_player"]
    assert bindings["stop"] == ["X", "Media Stop"]
    assert not find_conflicts(bindings)
    # Saving and loading again is stable (no second migration).
    s.save(tmp_path / "s.json")
    again = Settings.load(tmp_path / "s.json")
    assert again.shortcuts == s.shortcuts and again.first_run_done


def test_migration_keeps_unrelated_overrides_and_new_defaults():
    out = migrate_shortcuts({"stop": ["X"]}, ["mini_player", "always_on_top"])
    assert out == {"stop": ["X"]}  # nothing to change: new keys stay default
    out = migrate_shortcuts({"stop": ["Ctrl+Shift+T"]}, ["always_on_top"])
    assert out["always_on_top"] == [] and out["stop"] == ["Ctrl+Shift+T"]


def test_migrate_handles_future_and_bad_versions():
    assert migrate({"settings_version": 99})["settings_version"] == 99
    assert migrate({"settings_version": "x"})["first_run_done"] is True
    assert migrate({"settings_version": SETTINGS_VERSION}).get("first_run_done") is None


def test_new_settings_are_validated(tmp_path):
    s = Settings.load(_write(tmp_path / "s.json", {
        "settings_version": 2, "theme": "neon", "accent_color": "orange", "instance_open_mode": "x",
        "last_update_check": 12, "window_state": [1, 2]}))
    assert s.theme == "dark" and s.accent_color == "#ff8a2a" and s.instance_open_mode == "play"
    assert s.last_update_check == 12.0 and isinstance(s.last_update_check, float)
    assert s.window_state == {}


def test_sections_cover_known_fields_and_reset():
    names = set(Settings.__dataclass_fields__)
    seen = []
    for key, (title, fields) in SECTIONS.items():
        assert title
        seen.extend(fields)
        assert set(fields) <= names, key
    assert len(seen) == len(set(seen))  # each field in one section
    s = Settings(theme="light", accent_color="#00ff00", show_osd=False, volume=40, single_instance=False)
    s.reset_section("appearance")
    assert s.theme == "dark" and s.accent_color == "#ff8a2a" and s.show_osd is True
    assert s.volume == 40 and s.single_instance is False  # other sections untouched


def test_new_shortcuts_have_no_conflicts():
    assert not find_conflicts(resolve_shortcuts())
    assert "Toggle Media Play/Pause" in DEFAULT_SHORTCUTS["play_pause"][1]


# ------------------------------------------------------------------ update check
@pytest.mark.parametrize("older,newer", [
    ("0.1.0", "1.0.0"), ("1.0.0", "1.0.1"), ("1.9.0", "1.10.0"), ("1.0.0-beta.2", "1.0.0-rc.1"),
    ("1.0.0-rc.1", "1.0.0"), ("1.0.0-alpha", "1.0.0-beta"), ("1.0.0-beta.2", "1.0.0-beta.10"),
    ("v1.0.0", "v2"),
])
def test_version_ordering(older, newer):
    assert parse_version(older) < parse_version(newer)
    assert is_newer(newer, older) and not is_newer(older, newer)


def test_version_equality_and_garbage():
    assert parse_version("1.2") == parse_version("v1.2.0") == parse_version("1.2.0+build5")
    assert not is_newer("1.2.0", "1.2")
    for bad in ("build-15", "", "latest", "v", "1.x"):
        assert parse_version(bad) is None
        assert not is_newer(bad, "0.0.1")
    assert parse_version(__version__) is not None


def test_once_a_day():
    day = 24 * 3600
    assert is_due(0, 1000)
    assert not is_due(10_000, 10_000 + day - 1)
    assert is_due(10_000, 10_000 + day)
    assert is_due(10_000 + 10 * day, 10_000)  # clock went back a lot: check again


def test_parse_release_skips_drafts_and_prereleases():
    good = {"tag_name": "v1.2.0", "html_url": "https://x/r", "name": "VLClone 1.2.0", "body": "notes"}
    assert parse_release(good) == Release("1.2.0", "https://x/r", "VLClone 1.2.0", "notes")
    assert parse_release({**good, "prerelease": True}) is None
    assert parse_release({**good, "draft": True}) is None
    assert parse_release({"tag_name": "build-15"}) is None
    assert parse_release([1, 2]) is None
    assert parse_release({"tag_name": "v3"}).url.endswith("/releases/tag/v3")


def test_check_for_update_with_mocked_github():
    calls = []

    def fetch(url):
        calls.append(url)
        return {"tag_name": "v9.9.9", "html_url": "https://example/r"}

    result = check_for_update("1.0.0", fetch, url="http://mock/latest")
    assert result.checked and result.release.version == "9.9.9" and calls == ["http://mock/latest"]
    assert check_for_update("9.9.9", fetch).release is None
    assert check_for_update("10.0.0", fetch).release is None

    def offline(url):
        raise OSError("no network")

    result = check_for_update("1.0.0", offline)
    assert not result.checked and "no network" in result.error


def test_update_checker_runs_in_background():
    checker = UpdateChecker(lambda url: {"tag_name": "v99.0"}, current="1.0.0")
    assert checker.poll() is None
    assert checker.start()
    result = checker.wait(5)
    assert result.release.version == "99.0"
    assert checker.poll() is None  # delivered once


# ------------------------------------------------------------------ single instance protocol
def test_handoff_message_round_trip_in_chunks():
    msgs = [HandoffMessage(["D:/Films/a é.mkv", "http://x/s.m3u8"], "enqueue", "D:/"),
            HandoffMessage([], None, "")]
    data = b"".join(m.encode() for m in msgs)
    out, rest = [], b""
    for i in range(0, len(data), 7):  # sockets deliver arbitrary chunks
        got, rest = decode_stream(rest + data[i:i + 7])
        out += got
    assert rest == b""
    assert [(m.inputs, m.mode, m.cmd) for m in out] == [(msgs[0].inputs, "enqueue", "open"), ([], None, "open")]


def test_handoff_rejects_garbage():
    got, rest = decode_stream(b'not json\n{"cmd": "open", "inputs": [1]}\n{"cmd":"open","mode":"evil"}\npartial')
    assert len(got) == 1 and got[0].mode is None and rest == b"partial"
    got, rest = decode_stream(b"x" * (5 * 1024 * 1024))
    assert got == [] and rest == b""


def test_relative_inputs_resolve_against_sender_cwd(tmp_path):
    msg = HandoffMessage(["clip.mkv", str(tmp_path / "abs.mkv"), "https://a/b"], cwd=str(tmp_path / "dir"))
    assert msg.resolved_inputs() == [str(tmp_path / "dir" / "clip.mkv"), str(tmp_path / "abs.mkv"), "https://a/b"]


def test_server_name_is_per_user_and_config_dir():
    a = server_name("/home/u/.config/vlclone", "alice")
    assert a == server_name("/home/u/.config/vlclone", "alice")
    assert a != server_name("/home/u/.config/vlclone", "bob")
    assert a != server_name("/portable/config", "alice")
    assert a.startswith("VLClone-") and "/" not in a and "\\" not in a


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_batcher_merges_explorer_multi_select():
    clock = Clock()
    b = HandoffBatcher("play", window_s=0.5, clock=clock)
    for i in range(5):  # Explorer starts one process per selected file
        b.add(HandoffMessage([f"/m/{i}.mkv"]))
        clock.t += 0.05
    assert b.take() is None  # still arriving
    clock.t += 0.5
    batch = b.take()
    assert len(batch) == 1 and batch[0].play and batch[0].inputs == [f"/m/{i}.mkv" for i in range(5)]
    assert b.take() is None and not b.pending


def test_batcher_modes():
    clock = Clock()
    b = HandoffBatcher("enqueue", clock=clock)
    b.add(HandoffMessage(["/a"]))              # setting: enqueue
    b.add(HandoffMessage(["/b"], "play"))      # "Play with VLClone"
    b.add(HandoffMessage(["/c"], "enqueue"))
    b.add(HandoffMessage(["/d"], "play"))
    b.add(HandoffMessage([]))                  # plain second launch: nothing to open
    batch = b.take(force=True)
    assert [(r.inputs, r.play) for r in batch] == [(["/a"], False), (["/b"], True), (["/c"], False),
                                                   (["/d"], False)]


# ------------------------------------------------------------------ window state
LEFT = ScreenInfo("DISPLAY1", Rect(0, 0, 1920, 1040), 1.0, primary=True)
RIGHT_4K = ScreenInfo("DISPLAY2", Rect(1920, 0, 2560, 1400), 1.5)  # 3840x2160 at 150 %


def saved(rect, screen="DISPLAY2", screen_rect=RIGHT_4K.available, **kw):
    return SavedWindow(Rect(*rect), screen, screen_rect, **kw)


def test_restore_on_same_monitor():
    plan = plan_restore(saved((2100, 100, 1400, 800)), [LEFT, RIGHT_4K])
    assert plan.rect == Rect(2100, 100, 1400, 800) and plan.screen == "DISPLAY2"
    assert not plan.maximized and not plan.fullscreen


def test_restore_when_monitor_unplugged_moves_to_primary():
    plan = plan_restore(saved((2100, 100, 1400, 800), fullscreen=True), [LEFT])
    assert plan.screen == "DISPLAY1" and plan.fullscreen
    assert plan.rect == Rect(260, 120, 1400, 800)  # centred on the laptop screen


def test_restore_after_scaling_change_shrinks_into_work_area():
    # The 4K screen went from 150 % to 200 %: its logical work area shrank to 1920x1040.
    small = ScreenInfo("DISPLAY2", Rect(1920, 0, 1920, 1040), 2.0)
    plan = plan_restore(saved((2100, 100, 2400, 1300), dpr=1.5), [LEFT, small])
    assert plan.screen == "DISPLAY2"
    assert plan.rect == Rect(1920, 0, 1920, 1040)


def test_restore_when_monitors_rearranged():
    # DISPLAY2 now sits left of the primary.
    moved = ScreenInfo("DISPLAY2", Rect(-2560, 0, 2560, 1400), 1.5)
    plan = plan_restore(saved((2100, 100, 1400, 800)), [LEFT, moved])
    assert plan.screen == "DISPLAY2"
    assert plan.rect.x >= -2560 and plan.rect.right <= 0


def test_restore_offscreen_or_partly_offscreen():
    plan = plan_restore(SavedWindow(Rect(1500, 600, 800, 600), "GONE"), [LEFT])
    assert plan.rect == Rect(1120, 440, 800, 600)  # mostly on DISPLAY1: pulled in, not centred
    plan = plan_restore(SavedWindow(Rect(-5000, -5000, 800, 600), "GONE"), [LEFT])
    assert plan.rect == Rect(560, 220, 800, 600)


def test_restore_defaults_and_round_trip():
    plan = plan_restore(None, [RIGHT_4K, LEFT])
    assert plan.screen == "DISPLAY1" and plan.rect == Rect(320, 140, 1280, 760)
    state = saved((10, 20, 900, 600), maximized=True)
    assert SavedWindow.from_dict(json.loads(json.dumps(state.to_dict()))) == state
    assert SavedWindow.from_dict({"rect": [1, 2, -3, 4]}).rect is None
    assert SavedWindow.from_dict("junk") is None


def test_mini_player_placement():
    assert plan_mini(None, [LEFT]) == Rect(1920 - 420 - 24, 1040 - 236 - 24, 420, 236)
    assert plan_mini([2000, 50, 400, 225], [LEFT, RIGHT_4K]) == Rect(2000, 50, 400, 225)
    assert plan_mini([9000, 50, 400, 225], [LEFT]).x == 1920 - 420 - 24


# ------------------------------------------------------------------ desktop integration state
def test_sleep_only_while_video_plays():
    video = SessionState("playing", has_video=True)
    assert should_inhibit_sleep(video)
    assert not should_inhibit_sleep(video, enabled=False)
    assert not should_inhibit_sleep(SessionState("playing", has_video=False))  # audio
    assert not should_inhibit_sleep(SessionState("paused", has_video=True))
    assert not should_inhibit_sleep(SessionState("stopped", has_video=True))
    assert status_for(PlaybackState.BUFFERING) == "playing" and status_for(PlaybackState.ERROR) == "stopped"


def test_taskbar_progress_and_buttons():
    t = taskbar_state(SessionState("playing", position_ms=30_000, length_ms=120_000, can_next=True))
    assert (t.progress, t.value, t.play_icon, t.next_enabled, t.previous_enabled) == ("normal", 250, "pause", True, False)
    assert taskbar_state(SessionState("paused", position_ms=1, length_ms=10)).progress == "paused"
    assert taskbar_state(SessionState("playing", length_ms=-1)).progress == "indeterminate"  # live stream
    assert taskbar_state(SessionState("stopped")).progress == "none"


def test_media_key_debounce():
    clock = Clock()
    d = CommandDebouncer(0.35, clock)
    assert d.accept("play_pause", "shortcut")
    clock.t = 0.1
    assert not d.accept("play_pause", "smtc")  # the same key press via the media overlay
    clock.t = 1.0
    assert d.accept("play_pause", "smtc")
    clock.t = 1.1
    assert d.accept("play_pause", "smtc")      # repeated presses from one source all count
    assert d.accept("next", "shortcut")


# ------------------------------------------------------------------ file associations
def test_association_entries():
    exe = r"C:\Program Files\VLClone\VLClone.exe"
    entries = assoc.association_entries(["video"], exe)
    keys = {(e.key, e.name) for e in entries}
    assert (r"Software\Classes\.mkv\OpenWithProgids", "VLClone.Video") in keys
    assert (r"Software\Classes\.mp3\OpenWithProgids", "VLClone.Audio") not in keys
    assert (r"Software\RegisteredApplications", "VLClone") in keys
    cmd = next(e.value for e in entries if e.key.endswith(r"VLClone.Video\shell\open\command"))
    assert cmd == f'"{exe}" "%1"'
    # Shared keys (.mkv, RegisteredApplications) are never deleted whole.
    assert all(e.owned_key == "" for e in entries if r"\.mkv" in e.key or "RegisteredApplications" in e.key)
    assert assoc.association_entries([], exe) == []


def test_context_menu_entries():
    entries = assoc.context_menu_entries("C:\\V\\VLClone.exe")
    cmds = {e.key: e.value for e in entries if e.key.endswith("command")}
    assert cmds[r"Software\Classes\SystemFileAssociations\.mp4\shell\VLClone.play\command"] == \
        '"C:\\V\\VLClone.exe" --play "%1"'
    assert cmds[r"Software\Classes\Directory\shell\VLClone.enqueue\command"] == \
        '"C:\\V\\VLClone.exe" --enqueue "%1"'
    assert all(e.owned_key and e.key.startswith(e.owned_key) for e in entries)


# ------------------------------------------------------------------ seek previews
def test_seek_previews_cache_and_reuse(tmp_path):
    from vlclone.core.fake_backend import FakeProbe
    from vlclone.core.seek_preview import SeekPreviewService

    media = tmp_path / "film.mkv"
    media.write_bytes(b"x")
    probes = []

    def factory():
        probes.append(FakeProbe())
        return probes[-1]

    svc = SeekPreviewService(factory, tmp_path / "seek", width=64, max_frames=100, prefill=4)
    assert svc.frame_for(1000) is None  # nothing playing
    svc.set_media(str(media), 100_000)
    assert svc.wait_idle()
    made = {at for p in probes for (_, at) in p.thumbnailed}
    assert len(made) == 4  # the coarse prefill
    assert svc.frame_for(42_000) is None or svc.slot(42_000) not in made  # queued, maybe a neighbour
    assert svc.wait_idle()
    exact = svc.frame_for(42_000)
    assert exact and exact.endswith(f"{svc.slot(42_000)}.png") and svc.is_exact(42_400)
    # Same file again later (new service = new launch): served from the cache, no decoding.
    svc.shutdown()
    again = SeekPreviewService(factory, tmp_path / "seek", width=64, max_frames=100, prefill=4)
    again.set_media(str(media), 100_000)
    assert again.frame_for(42_000) == exact
    assert again.wait_idle()
    assert sum(len(p.thumbnailed) for p in probes[1:]) == 0
    again.set_media(None)
    assert again.frame_for(42_000) is None and not again.active
    again.shutdown()


def test_seek_previews_skip_streams(tmp_path):
    from vlclone.core.fake_backend import FakeProbe
    from vlclone.core.seek_preview import SeekPreviewService

    svc = SeekPreviewService(FakeProbe, tmp_path)
    svc.set_media("http://example/stream", 100_000)
    assert not svc.active
    svc.set_media(str(tmp_path / "missing.mkv"), 100_000)
    assert not svc.active
    svc.shutdown()
