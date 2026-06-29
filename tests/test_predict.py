import pandas as pd
from core.predict import make_prediction, format_prediction, PREDICTION_SCHEMA
from core.store import HISTORY_PATH
import jobs.morning as morning


def _fake_llm(system, user, schema, client=None):
    assert schema is PREDICTION_SCHEMA
    return {
        "signal": "觀望",
        "direction": "跌",
        "hold_ma20": False,
        "hold_support1": False,
        "reason": "量縮跌破MA20",
    }


def test_make_prediction_includes_indicators_and_fields():
    ind = {"close": 203.0, "ma20": 186.5, "rsi14": 42.0}
    out = make_prediction(ind, "華邦電 (2344)", llm=_fake_llm)
    assert out["signal"] == "觀望"
    assert out["direction"] == "跌"
    assert out["indicators"]["close"] == 203.0


def test_format_prediction_contains_key_text():
    pred = {
        "signal": "觀望", "direction": "跌", "hold_ma20": False,
        "hold_support1": False, "reason": "量縮",
        "indicators": {"close": 203.0, "ma20": 186.5},
    }
    s = format_prediction("華邦電 (2344)", "2026-06-30", pred)
    assert "華邦電 (2344)" in s and "觀望" in s and "2026-06-30" in s


def _df_with_ma20(n=30):
    closes = [float(100 + i) for i in range(n)]
    idx = pd.date_range("2026-05-01", periods=n, freq="D")
    df = pd.DataFrame(
        {"Open": closes, "High": closes, "Low": closes,
         "Close": closes, "Volume": [1000.0] * n}, index=idx)
    df["MA20"] = df["Close"].rolling(20).mean()
    return df


def test_morning_run_writes_and_notifies(tmp_path, monkeypatch):
    monkeypatch.setattr(morning, "HISTORY_PATH", str(tmp_path / "h.json"))
    sent = {}
    monkeypatch.setattr(morning, "tg", type("T", (), {
        "send": staticmethod(lambda text: sent.setdefault("text", text) or True)
    }))
    rec = morning.run(
        today=pd.Timestamp("2026-06-30"),
        llm=_fake_llm,
        fetch=lambda code, today=None: _df_with_ma20(),
    )
    assert rec is not None
    assert rec["prediction"]["signal"] == "觀望"
    assert "觀望" in sent["text"]


def test_morning_run_empty_data_notifies_and_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(morning, "HISTORY_PATH", str(tmp_path / "h.json"))
    sent = {}
    monkeypatch.setattr(morning, "tg", type("T", (), {
        "send": staticmethod(lambda text: sent.setdefault("text", text) or True)
    }))
    out = morning.run(
        today=pd.Timestamp("2026-06-30"),
        llm=_fake_llm,
        fetch=lambda code, today=None: pd.DataFrame(),
    )
    assert out is None
    assert "缺漏" in sent["text"]
