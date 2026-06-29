import pandas as pd
from core.data import parse_index_json
from core.market import market_summary


def test_parse_index_json_ok():
    j = {"stat": "OK", "data": [
        ["115/06/27", "45,000.00", "45,500.00", "44,800.00", "45,337.91"],
    ]}
    rows = parse_index_json(j)
    assert len(rows) == 1
    r = rows[0]
    assert r["Open"] == 45000.0 and r["Close"] == 45337.91
    assert str(r["Date"].date()) == "2026-06-27"


def test_parse_index_json_not_ok():
    assert parse_index_json({"stat": "x"}) == []


def _df(closes):
    idx = pd.date_range("2026-01-01", periods=len(closes), freq="D")
    return pd.DataFrame({
        "Open": closes, "High": closes, "Low": closes, "Close": closes,
    }, index=idx)


def test_market_summary_up():
    df = _df([float(100 + i) for i in range(30)])
    df["MA20"] = df["Close"].rolling(20).mean()
    m = market_summary(df)
    assert m["close"] == 129.0 and m["prev_close"] == 128.0
    assert m["direction"] == "漲"
    assert m["above_ma20"] is True
    assert m["pct"] == round((129.0 - 128.0) / 128.0 * 100, 2)


def test_market_summary_empty_is_none():
    assert market_summary(pd.DataFrame()) is None
    assert market_summary(None) is None
