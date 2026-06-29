import pandas as pd
from core.predict import make_prediction, format_prediction, PREDICTION_SCHEMA
import jobs.morning as morning


def _fake_llm(system, user, schema, client=None):
    assert schema is PREDICTION_SCHEMA
    return {
        "signal": "觀望", "direction": "跌", "hold_ma20": False,
        "hold_support1": False, "reason": "量縮跌破MA20",
    }


def test_make_prediction_includes_market():
    ind = {"close": 203.0, "ma20": 186.5}
    market = {"direction": "跌", "pct": -0.7, "above_ma20": False}
    out = make_prediction(ind, "華邦電 (2344)", market=market, llm=_fake_llm)
    assert out["indicators"]["close"] == 203.0
    assert out["market"]["direction"] == "跌"


def test_format_prediction_shows_market():
    pred = {
        "signal": "觀望", "direction": "跌", "hold_ma20": False,
        "hold_support1": False, "reason": "量縮",
        "indicators": {"close": 203.0, "ma20": 186.5},
        "market": {"direction": "跌", "pct": -0.7, "above_ma20": False},
    }
    s = format_prediction("華邦電 (2344)", "2026-06-30", pred)
    assert "大盤" in s and "跌" in s


def test_format_prediction_no_market_ok():
    pred = {
        "signal": "觀望", "direction": "跌", "hold_ma20": False,
        "hold_support1": False, "reason": "x",
        "indicators": {"close": 203.0, "ma20": 186.5}, "market": None,
    }
    s = format_prediction("華邦電 (2344)", "2026-06-30", pred)
    assert "華邦電 (2344)" in s  # 不因缺 market 而壞


def _df_with_ma20(n=30):
    closes = [float(100 + i) for i in range(n)]
    idx = pd.date_range("2026-05-01", periods=n, freq="D")
    df = pd.DataFrame({"Open": closes, "High": closes, "Low": closes,
                       "Close": closes, "Volume": [1000.0] * n}, index=idx)
    df["MA20"] = df["Close"].rolling(20).mean()
    return df


def _idx_df(n=30):
    closes = [float(45000 + i) for i in range(n)]
    idx = pd.date_range("2026-05-01", periods=n, freq="D")
    df = pd.DataFrame({"Open": closes, "High": closes, "Low": closes,
                       "Close": closes}, index=idx)
    df["MA20"] = df["Close"].rolling(20).mean()
    return df


def test_morning_run_writes_with_market(tmp_path, monkeypatch):
    monkeypatch.setattr(morning, "HISTORY_PATH", str(tmp_path / "h.json"))
    sent = {}
    monkeypatch.setattr(morning, "tg", type("T", (), {
        "send": staticmethod(lambda text: sent.setdefault("text", text) or True)
    }))
    rec = morning.run(
        today=pd.Timestamp("2026-06-30"),
        llm=_fake_llm,
        fetch=lambda code, today=None: _df_with_ma20(),
        fetch_idx=lambda today=None: _idx_df(),
    )
    assert rec is not None
    assert rec["prediction"]["market"]["direction"] == "漲"
    assert "大盤" in sent["text"]


def test_morning_run_empty_data_notifies(tmp_path, monkeypatch):
    monkeypatch.setattr(morning, "HISTORY_PATH", str(tmp_path / "h.json"))
    sent = {}
    monkeypatch.setattr(morning, "tg", type("T", (), {
        "send": staticmethod(lambda text: sent.setdefault("text", text) or True)
    }))
    out = morning.run(
        today=pd.Timestamp("2026-06-30"),
        llm=_fake_llm,
        fetch=lambda code, today=None: pd.DataFrame(),
        fetch_idx=lambda today=None: _idx_df(),
    )
    assert out is None
    assert "缺漏" in sent["text"]
