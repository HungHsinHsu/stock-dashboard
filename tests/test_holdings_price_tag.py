"""推播的價格必須標明是哪一天的收盤，不能標成「現」。"""
import datetime as dt
import pytest

from jobs.holdings import _price_tag, _stale_note, _item_lines


def _tw(y, m, d, hh, mm=0):
    return dt.datetime(y, m, d, hh, mm)


def test_price_tag_shows_the_data_date():
    assert _price_tag("2026-08-04") == "8/04收"
    assert _price_tag("2026-12-31") == "12/31收"


@pytest.mark.parametrize("bad", [None, "", "not-a-date", 20260804])
def test_price_tag_degrades_without_lying(bad):
    assert _price_tag(bad) == "收"          # 不得回「現」


def test_item_line_never_says_now():
    it = {"action": "續抱", "name": "南亞科", "code": "2408", "mode": "波段",
          "avg_cost": 418.0, "close": 436.0, "pnl_pct": 4.3,
          "date": "2026-08-04", "reason": "站穩月線"}
    line = next(l for l in _item_lines(it) if "損益" in l)
    assert "8/04收 436.00" in line
    assert "現" not in line


def test_stale_note_fires_after_open_with_yesterdays_data():
    items = [{"date": "2026-08-04"}]
    note = _stale_note(items, now=_tw(2026, 8, 5, 11, 29))
    assert note and "2026-08-04" in note and "排程遲到" in note


def test_no_note_before_open_or_when_fresh():
    # 開盤前推昨收是正常設計，不該警告
    assert _stale_note([{"date": "2026-08-04"}], now=_tw(2026, 8, 5, 8, 30)) is None
    # 資料就是今天的，也不該警告
    assert _stale_note([{"date": "2026-08-05"}], now=_tw(2026, 8, 5, 11, 29)) is None
    # 完全沒有日期時不亂警告
    assert _stale_note([{"date": None}], now=_tw(2026, 8, 5, 11, 29)) is None
