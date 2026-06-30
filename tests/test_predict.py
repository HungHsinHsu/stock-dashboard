import pandas as pd
from core.predict import (
    make_prediction, format_prediction, PREDICTION_SCHEMA,
    make_market_prediction, format_market_prediction, MARKET_PRED_SCHEMA,
)
import jobs.morning as morning


def _fake_llm(system, user, schema, client=None):
    if schema is MARKET_PRED_SCHEMA:
        return {"direction": "漲", "confidence": "中",
                "drivers": ["費半隔夜 +1.8%", "台指期夜盤偏多"],
                "reason": "美股偏多帶動"}
    assert schema is PREDICTION_SCHEMA
    return {
        "signal": "觀望", "direction": "跌", "confidence": "中",
        "bull_signals": ["站穩 MA20"], "bear_signals": ["MACD 翻空", "KD 死亡交叉"],
        "hold_ma20": False, "hold_support1": False, "reason": "量縮跌破MA20",
    }


_NO_LEAD = {"fetch_us": lambda: {}, "fetch_tf": lambda: None}


def test_make_prediction_includes_market():
    ind = {"close": 203.0, "ma20": 186.5}
    market = {"direction": "跌", "pct": -0.7, "above_ma20": False}
    out = make_prediction(ind, "華邦電 (2344)", market=market, llm=_fake_llm)
    assert out["indicators"]["close"] == 203.0
    assert out["market"]["direction"] == "跌"


def test_format_prediction_shows_market():
    pred = {
        "signal": "觀望", "direction": "跌", "confidence": "低",
        "bull_signals": [], "bear_signals": ["跌破 20 日低點"],
        "hold_ma20": False, "hold_support1": False, "reason": "量縮",
        "indicators": {"close": 203.0, "ma20": 186.5},
        "market": {"direction": "跌", "pct": -0.7, "above_ma20": False},
    }
    s = format_prediction("華邦電 (2344)", "2026-06-30", pred)
    assert "大盤" in s and "跌" in s
    assert "信心低" in s and "跌破 20 日低點" in s  # 顯示信心與技術訊號
    assert "streamlit.app" in s  # 附上圖表連結


def test_make_and_format_market_prediction():
    market = {"direction": "跌", "pct": -0.5, "above_ma20": False}
    out = make_market_prediction(
        {"ma20": 45000}, {"費半SOX": 1.8, "Nasdaq": 0.9}, market,
        taifex_night=None, llm=_fake_llm,
    )
    assert out["direction"] == "漲"
    s = format_market_prediction("2026-06-30", out)
    assert "加權指數" in s and "美股隔夜" in s and "費半SOX" in s
    assert "預測開盤方向" in s and "大盤昨收" in s


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
        stocks={"華邦電 (2344)": {"code": "2344",
                "supports": {"支撐1 (短期)": 222, "支撐3 (長期)": 142}}},
        **_NO_LEAD,
    )
    assert recs and recs[0]["prediction"]["market"]["direction"] == "漲"
    assert "大盤" in sent["text"]


def test_morning_run_empty_data_notifies(tmp_path, monkeypatch):
    monkeypatch.setattr(morning, "HISTORY_PATH", str(tmp_path / "h.json"))
    sends = []
    monkeypatch.setattr(morning, "tg", type("T", (), {
        "send": staticmethod(lambda text: sends.append(text) or True)
    }))
    out = morning.run(
        today=pd.Timestamp("2026-06-30"),
        llm=_fake_llm,
        fetch=lambda code, today=None: pd.DataFrame(),
        fetch_idx=lambda today=None: _idx_df(),
        stocks={"華邦電 (2344)": {"code": "2344"}},
        **_NO_LEAD,
    )
    assert out == []
    assert any("缺漏" in s for s in sends)


def test_morning_run_multiple_stocks(tmp_path, monkeypatch):
    monkeypatch.setattr(morning, "HISTORY_PATH", str(tmp_path / "h.json"))
    sends = []
    monkeypatch.setattr(morning, "tg", type("T", (), {
        "send": staticmethod(lambda text: sends.append(text) or True)}))
    recs = morning.run(
        today=pd.Timestamp("2026-06-30"), llm=_fake_llm,
        fetch=lambda code, today=None: _df_with_ma20(),
        fetch_idx=lambda today=None: _idx_df(),
        stocks={
            "A (1111)": {"code": "1111",
                         "supports": {"支撐1 (短期)": 50, "支撐3 (長期)": 30}},
            "B (2222)": {"code": "2222"},  # 無支撐
        },
        **_NO_LEAD,
    )
    assert len(recs) == 2
    assert {r["stock"] for r in recs} == {"1111", "2222"}
    assert sum("📈" in s for s in sends) == 2          # 兩檔各推一則
    assert sum("加權指數" in s for s in sends) == 1     # 外加一則大盤預測
