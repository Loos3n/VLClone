from vlclone.core.abloop import ABLoop, ABState
from vlclone.core.stepping import cycle, fine_step_rate, step_rate, step_volume


def test_ab_cycle():
    ab = ABLoop()
    assert ab.toggle(1000) is ABState.A_SET
    assert ab.check(5000) is None
    assert ab.toggle(4000) is ABState.ACTIVE
    assert ab.check(3999) is None
    assert ab.check(4000) == 1000
    assert ab.toggle(0) is ABState.OFF
    assert ab.check(10_000) is None


def test_ab_swaps_and_min_length():
    ab = ABLoop()
    ab.toggle(5000)
    ab.toggle(2000)
    assert (ab.a, ab.b) == (2000, 5000)
    ab.clear()
    ab.toggle(1000)
    ab.toggle(1050)
    assert ab.b - ab.a == ABLoop.MIN_LENGTH_MS


def test_step_rate():
    assert step_rate(1.0, +1) == 1.25
    assert step_rate(1.0, -1) == 0.75
    assert step_rate(1.1, -1) == 1.0
    assert step_rate(4.0, +1) == 8.0
    assert step_rate(0.25, -1) == 0.125
    assert fine_step_rate(1.0, +1) == 1.1
    assert fine_step_rate(0.1, -1) == 0.1


def test_step_volume():
    assert step_volume(100, 1) == 105
    assert step_volume(103, -1) == 100
    assert step_volume(103, 1) == 105
    assert step_volume(0, -1) == 0
    assert step_volume(200, 1) == 200
    assert step_volume(95, 1, maximum=100) == 100


def test_cycle():
    assert cycle([None, "a", "b"], None) == "a"
    assert cycle([None, "a", "b"], "b") == None
    assert cycle([None, "a", "b"], "zz") is None
    assert cycle([1, 2, 3], 1, -1) == 3
