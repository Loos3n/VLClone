import random

from vlclone.core.playlist import Playlist, RepeatMode


def make(n=5, **kw):
    p = Playlist(rng=random.Random(1234))
    p.add([f"http://x/{i}.mp4" for i in range(n)])
    return p


def titles(p):
    return [it.title for it in p]


def test_add_and_titles():
    p = make(3)
    assert titles(p) == ["0.mp4", "1.mp4", "2.mp4"]
    assert p.current_index == -1
    p.add(["http://x/new.mp4"], at=1)
    assert titles(p)[1] == "new.mp4"


def test_sequential_navigation_no_repeat():
    p = make(3)
    assert p.next().title == "0.mp4"
    assert p.next().title == "1.mp4"
    assert p.next().title == "2.mp4"
    assert p.next() is None
    assert p.current_index == 2
    assert p.previous().title == "1.mp4"


def test_repeat_all_wraps_both_ways():
    p = make(3)
    p.repeat = RepeatMode.ALL
    p.set_current(2)
    assert p.next().title == "0.mp4"
    assert p.previous().title == "2.mp4"


def test_repeat_one_only_on_auto_advance():
    p = make(3)
    p.repeat = RepeatMode.ONE
    p.set_current(1)
    assert p.next(auto=True).title == "1.mp4"
    assert p.next().title == "2.mp4"


def test_current_tracks_insertions_and_moves():
    p = make(4)
    p.set_current(2)
    p.add(["http://x/a.mp4"], at=0)
    assert p.current.title == "2.mp4" and p.current_index == 3
    p.move([3], 0)
    assert p.current_index == 0 and titles(p)[0] == "2.mp4"


def test_move_semantics():
    p = make(5)
    new = p.move([0, 1], 4)
    assert titles(p) == ["2.mp4", "3.mp4", "0.mp4", "1.mp4", "4.mp4"]
    assert new == 2
    p.move([4], 0)
    assert titles(p)[0] == "4.mp4"
    p.move([0], 5)
    assert titles(p)[-1] == "4.mp4"


def test_remove_current_and_clear():
    p = make(3)
    p.set_current(1)
    events = []
    p.current_changed.connect(events.append)
    p.remove([1])
    assert p.current is None and events == [-1]
    assert titles(p) == ["0.mp4", "2.mp4"]
    p.set_current(1)
    p.remove([0])
    assert p.current_index == 0
    p.clear()
    assert len(p) == 0 and p.current is None


def test_shuffle_visits_everything_once_per_cycle():
    p = make(8)
    p.shuffle = True
    seen = [p.next().uid]
    for _ in range(7):
        seen.append(p.next().uid)
    assert len(set(seen)) == 8
    assert p.next() is None  # no repeat: stop after a full cycle
    assert titles(p) == [f"{i}.mp4" for i in range(8)]  # visible order untouched


def test_shuffle_repeat_all_keeps_going():
    p = make(4)
    p.shuffle = True
    p.repeat = RepeatMode.ALL
    played = [p.next().uid for _ in range(12)]
    assert len(played) == 12
    for i in range(0, 12, 4):
        assert len(set(played[i:i + 4])) == 4


def test_shuffle_includes_items_added_later():
    p = make(3)
    p.shuffle = True
    p.next()
    p.add(["http://x/late.mp4"])
    seen = {p.current.title}
    while (item := p.next()) is not None:
        seen.add(item.title)
    assert "late.mp4" in seen and len(seen) == 4


def test_repeat_mode_cycle():
    assert RepeatMode.NONE.next() is RepeatMode.ALL
    assert RepeatMode.ALL.next() is RepeatMode.ONE
    assert RepeatMode.ONE.next() is RepeatMode.NONE


def test_empty_playlist():
    p = Playlist()
    assert p.next() is None and p.previous() is None
    assert p.peek_next() == -1
