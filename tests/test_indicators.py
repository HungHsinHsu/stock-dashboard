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
    ind = compute_indicators(df, {"支撐1 (短期)": 100, "支撐3 (長期)": 90})
    assert ind["close"] == 159.0
    assert ind["prev_close"] == 158.0
    assert ind["ma5"] is not None and ind["ma60"] is not None
    assert 0 <= ind["rsi14"] <= 100
    assert ind["dist_support1_pct"] == round((159.0 - 100) / 100 * 100, 2)


def test_compute_indicators_short_history():
    df = _df([100.0])
    df["MA20"] = df["Close"].rolling(20).mean()
    ind = compute_indicators(df, {"支撐1 (短期)": 100, "支撐3 (長期)": 90})
    assert ind["prev_close"] is None
    assert ind["ma20"] is None
    assert ind["rsi14"] is None
