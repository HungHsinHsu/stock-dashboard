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
    assert "streamlit.app" in s  # 附上圖表連結


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
    recs = morning.run(
        today=pd.Timestamp("2026-06-30"),
        llm=_fake_llm,
        fetch=lambda code, today=None: _df_with_ma20(),
        fetch_idx=lambda today=None: _idx_df(),
    )
    assert recs and recs[0]["prediction"]["market"]["direction"] == "漲"
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
    assert out == []
    assert "缺漏" in sent["text"]


def test_morning_run_multiple_stocks(tmp_path, monkeypatch):
    monkeypatch.setattr(morning, "HISTORY_PATH", str(tmp_path / "h.json"))
    monkeypatch.setattr(morning, "STOCKS", {
        "A (1111)": {"code": "1111",
                     "supports": {"支撐1 (短期)": 50, "支撐3 (長期)": 30}},
        "B (2222)": {"code": "2222"},  # 無支撐
    })
    sends = []
    monkeypatch.setattr(morning, "tg", type("T", (), {
        "send": staticmethod(lambda text: sends.append(text) or True)}))
    recs = morning.run(
        today=pd.Timestamp("2026-06-30"), llm=_fake_llm,
        fetch=lambda code, today=None: _df_with_ma20(),
        fetch_idx=lambda today=None: _idx_df(),
    )
    assert len(recs) == 2
    assert {r["stock"] for r in recs} == {"1111", "2222"}
    assert len(sends) == 2  # 兩檔各推一則
