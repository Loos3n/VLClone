from vlclone.core.timefmt import format_delay, format_ms, parse_time


def test_format_ms():
    assert format_ms(0) == "0:00"
    assert format_ms(61_500) == "1:01"
    assert format_ms(3_600_000) == "1:00:00"
    assert format_ms(5_000, force_hours=True) == "0:00:05"
    assert format_ms(-1) == "--:--"
    assert format_ms(None) == "--:--"


def test_parse_time():
    assert parse_time("90") == 90_000
    assert parse_time("1:30") == 90_000
    assert parse_time("1:00:00") == 3_600_000
    assert parse_time("0:01.5") == 1_500
    assert parse_time("") is None
    assert parse_time("a:b") is None
    assert parse_time("1:2:3:4") is None
    assert parse_time("-5") is None


def test_format_delay():
    assert format_delay(0) == "0 ms"
    assert format_delay(50) == "+50 ms"
    assert format_delay(-150) == "-150 ms"
