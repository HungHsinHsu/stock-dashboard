import pandas as pd
from core.indicators import compute_indicators, rsi


def _df(closes):
    idx = pd.date_range("2026-01-01", periods=len(closes), freq="D")
    return pd.DataFrame(
        {
            "Open": closes,
            "High": [c + 1 for c in closes],
            "Low": [c - 1 for c in closes],
            "Close": closes,
            "Volume": [1000.0] * len(closes),
        },
        index=idx,
    )


def test_rsi_all_up_is_100():
    s = pd.Series([float(i) for i in range(1, 30)])
    assert round(rsi(s, 14).iloc[-1], 1) == 100.0


def test_compute_indicators_basic():
    df = _df([float(100 + i) for i in range(60)])  # 穩定上漲 60 天
    df["MA20"] = df["Close"].rolling(20).mean()
    ind = compute_indicators(df, {})
    assert ind["close"] == 159.0
    assert ind["prev_close"] == 158.0
    assert ind["ma5"] == 157.0 and ind["ma60"] is not None
    assert 0 <= ind["rsi14"] <= 100
    # 三段支撐＝均線（每日重算）：支撐1＝MA5、支撐3＝MA60，不再用寫死價位
    assert ind["dist_support1_pct"] == round((159.0 - ind["ma5"]) / ind["ma5"] * 100, 2)
    assert ind["dist_support3_pct"] == round((159.0 - ind["ma60"]) / ind["ma60"] * 100, 2)


def test_compute_indicators_short_history():
    df = _df([100.0])
    df["MA20"] = df["Close"].rolling(20).mean()
    ind = compute_indicators(df, {})
    assert ind["prev_close"] is None
    assert ind["ma20"] is None
    assert ind["rsi14"] is None
