from datetime import timedelta
from core.tz import TW, now_tw, today_tw


def test_tw_is_utc_plus_8():
    assert TW.utcoffset(None) == timedelta(hours=8)


def test_now_tw_is_utc_plus_8_aware():
    n = now_tw()
    assert n.tzinfo is not None
    assert n.utcoffset() == timedelta(hours=8)


def test_today_tw_matches_now():
    assert today_tw() == now_tw().date()
