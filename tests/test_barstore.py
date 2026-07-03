import pandas as pd
from core.barstore import dump_bars, load_bars


def test_dump_load_roundtrip():
    idx = pd.date_range(end="2026-07-03", periods=30, freq="D")
    df = pd.DataFrame({"Open": [10.0] * 30, "High": [11.0] * 30, "Low": [9.0] * 30,
                       "Close": [float(i) for i in range(30)], "Volume": [100.0] * 30},
                      index=idx)
    out = load_bars(dump_bars(df))
    assert not out.empty
    assert str(out.index[-1].date()) == "2026-07-03"      # 最後日期正確
    assert out["Close"].iloc[-1] == 29.0                   # 收盤還原正確
    assert "MA20" in out.columns                           # 有重算均線


def test_dump_keep_limits_rows():
    idx = pd.date_range(end="2026-07-03", periods=500, freq="D")
    df = pd.DataFrame({"Open": [1.0] * 500, "High": [1.0] * 500, "Low": [1.0] * 500,
                       "Close": [1.0] * 500, "Volume": [1.0] * 500}, index=idx)
    assert len(dump_bars(df, keep=420)) == 420


def test_dump_load_empty():
    assert dump_bars(pd.DataFrame()) == []
    assert load_bars([]).empty
