"""掛單命中率：限價買單成交條件＝當日最低 ≤ 掛單價（不是收盤、不是開盤）。"""
import pandas as pd
import pytest
from jobs.quote import fill_odds


def _df(rows):
    """rows = [(open, low, close), ...]"""
    idx = pd.date_range("2026-01-01", periods=len(rows), freq="D")
    return pd.DataFrame(
        {"Open": [r[0] for r in rows],
         "Low": [r[1] for r in rows],
         "Close": [r[2] for r in rows]},
        index=idx)


def test_limit_above_every_low_always_fills():
    df = _df([(100, 98, 99)] * 6)
    hits, total, rows = fill_odds(df, 99.0, n=5)
    assert (hits, total) == (5, 5)
    assert all(r[4] for r in rows)


def test_limit_below_every_low_never_fills():
    hits, total, rows = fill_odds(_df([(100, 98, 99)] * 6), 97.0, n=5)
    assert (hits, total) == (0, 5)
    assert not any(r[4] for r in rows)


def test_counts_only_days_whose_low_reaches_the_limit():
    # 最低分別 98、95、99、94、97；掛 96 → 只有 95 和 94 那兩天碰得到
    df = _df([(100, 100, 100), (100, 98, 99), (100, 95, 96),
              (100, 99, 99), (100, 94, 95), (100, 97, 98)])
    hits, total, rows = fill_odds(df, 96.0, n=5)
    assert (hits, total) == (2, 5)
    assert [r[3] for r in rows] == [98.0, 95.0, 99.0, 94.0, 97.0]


def test_close_above_limit_still_fills_if_low_dipped_below():
    """收盤高於掛單價不代表沒成交——盤中摸到就成交了。"""
    df = _df([(100, 100, 100), (100, 90, 105)])
    hits, total, _ = fill_odds(df, 95.0, n=1)
    assert (hits, total) == (1, 1)


def test_delta_is_low_versus_previous_close():
    df = _df([(100, 100, 100), (100, 95, 99)])
    _, _, rows = fill_odds(df, 95.0, n=1)
    assert rows[0][1] == 100.0            # 前收
    assert rows[0][5] == pytest.approx(-5.0)   # 最低比前收低 5%


def test_needs_at_least_two_bars():
    assert fill_odds(_df([(100, 98, 99)]), 99.0) == (0, 0, [])
    assert fill_odds(_df([]), 99.0) == (0, 0, [])


def test_missing_low_column_is_not_an_error():
    idx = pd.date_range("2026-01-01", periods=3, freq="D")
    df = pd.DataFrame({"Close": [1.0, 2.0, 3.0]}, index=idx)
    assert fill_odds(df, 2.0) == (0, 0, [])
