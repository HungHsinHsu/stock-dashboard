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


_NO_LEAD = {"fetch_us": lambda: {}, "fetch_tf": lambda min_date=None: None,
            "fetch_fg": lambda code, today=None: None,
            "fetch_mg": lambda code, today=None: None}


_ENTRY_IND = {"close": 223, "prev_close": 222, "ma20": 181,
              "dist_support1_pct": 0.5, "dist_support3_pct": 57, "vol_ratio": 0.8}
_FOREIGN_OK = {"stopped": True, "net": 100000, "sold_streak": 0}


def _entry_llm(system, user, schema, client=None):
    return {"signal": "進場", "direction": "漲", "confidence": "中",
            "bull_signals": [], "bear_signals": [], "hold_ma20": True,
            "hold_support1": True, "reason": "回測支撐"}


def test_batches_show_next_batch_when_entry():
    out = make_prediction(_ENTRY_IND, "華邦電 (2344)", llm=_entry_llm,
                          code="2344", foreign=_FOREIGN_OK, batches=2)
    assert out["signal"] == "進場"
    assert "第 3 批" in out["signal_rule_note"]


def test_batches_full_downgrades_entry_to_watch():
    out = make_prediction(_ENTRY_IND, "華邦電 (2344)", llm=_entry_llm,
                          code="2344", foreign=_FOREIGN_OK, batches=3)
    assert out["signal"] == "觀望"            # 三批已滿不再加碼
    assert "三批已滿" in out["signal_rule_note"]


def test_prediction_feeds_and_shows_chips():
    captured = {}

    def _spy_llm(system, user, schema, client=None):
        captured["user"] = user
        return {"signal": "觀望", "direction": "跌", "confidence": "中",
                "bull_signals": [], "bear_signals": [], "hold_ma20": False,
                "hold_support1": False, "reason": "x"}

    foreign = {"net": -5000, "sold_streak": 3, "stopped": False, "date": "2026-06-30",
               "trust_net": 2000, "dealer_net": 500, "total_net": -2500}
    margin = {"margin_bal": 1200000, "margin_chg": 200000,
              "short_bal": 280000, "date": "2026-06-30"}
    out = make_prediction({"close": 200, "ma20": 190}, "台積電 (2330)",
                          llm=_spy_llm, code="2330", foreign=foreign, margin=margin)
    # 籌碼面(法人三大＋融資)有進到 LLM 提示
    assert "投信" in captured["user"] and "三大法人合計" in captured["user"]
    assert "融資" in captured["user"]
    assert out["margin"] == margin
    # 卡片有顯示投信/自營/合計與融資
    s = format_prediction("台積電 (2330)", "2026-06-30", out)
    assert "投信" in s and "融資" in s


def test_etf_uses_trend_framework_not_stock_chips():
    # ETF(00830) 走趨勢框架：用 ETF 系統提示、不餵個股籌碼、不套三批
    seen = {}

    def _etf_llm(system, user, schema, client=None):
        seen["system"], seen["user"] = system, user
        return {"signal": "避開", "direction": "跌", "confidence": "高",
                "bull_signals": [], "bear_signals": ["跌破季線"],
                "hold_ma20": False, "hold_support1": False, "reason": "費半崩跌"}

    ind = {"close": 50, "prev_close": 51, "ma20": 55, "ma60": 60,
           "ma_align": "空頭排列", "vol_ratio": 1.5}
    out = make_prediction(ind, "國泰費城半導體 (00830)", llm=_etf_llm, code="00830",
                          foreign={"net": -1_000_000, "stopped": False},
                          margin={"margin_chg": 5000}, batches=2)
    assert "ETF" in seen["system"] and "籌碼面" not in seen["user"]  # 走 ETF 提示、不帶籌碼
    assert out["is_etf"] is True
    assert out["batches"] is None and out["foreign"] is None       # 不套三批、不看個股籌碼
    s = format_prediction("國泰費城半導體 (00830)", "2026-07-02", out)
    assert "明顯轉空避開" in s                # 訊號翻成趨勢語意
    assert "追蹤標的" in s and "費半" in s
    assert "外資" not in s and "3 批" not in s  # 不顯示個股籌碼/批數


def test_etf_uptrend_labels_follow_trend():
    # 多頭排列 ETF → 順勢偏多
    def _up_llm(system, user, schema, client=None):
        return {"signal": "進場", "direction": "漲", "confidence": "中",
                "bull_signals": [], "bear_signals": [], "hold_ma20": True,
                "hold_support1": False, "reason": "站上均線"}

    ind = {"close": 60, "prev_close": 59, "ma20": 58, "ma60": 55,
           "ma_align": "多頭排列", "vol_ratio": 0.9}
    out = make_prediction(ind, "元大台灣50 (0050)", llm=_up_llm, code="0050")
    s = format_prediction("元大台灣50 (0050)", "2026-07-02", out)
    assert "順勢偏多" in s and "大盤" in s


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
    assert "streamlit.app" in s and "code=2344" in s  # 深連結帶該股代號


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


def test_market_drops_taifex_when_conflicts_with_us():
    # 費半 -6.27% 崩，但台指期顯示 +0.97（過時/雜訊）→ 嚴重背離，台指期應被丟棄不餵給模型
    seen = {}

    def _spy(system, user, schema, client=None):
        seen["user"] = user
        return {"direction": "跌", "confidence": "高", "drivers": [], "reason": "費半崩"}

    out = make_market_prediction(
        {"ma20": 45000}, {"費半SOX": -6.27, "Nasdaq": -0.66},
        {"direction": "跌", "pct": -0.5}, taifex_night=0.97,
        llm=_spy, taifex_asof="2026-07-01")
    assert "0.97" not in seen["user"]        # 矛盾的台指期不餵進提示
    assert "背離" in seen["user"]             # 明講背離、不納入
    assert out["taifex_night"] is None        # 記錄裡也標記為未採用


def test_market_keeps_taifex_when_consistent():
    # 費半與台指期同向（都跌）→ 台指期正常納入
    seen = {}

    def _spy(system, user, schema, client=None):
        seen["user"] = user
        return {"direction": "跌", "confidence": "中", "drivers": [], "reason": "x"}

    make_market_prediction(
        {"ma20": 45000}, {"費半SOX": -3.0}, {"direction": "跌", "pct": -0.5},
        taifex_night=-1.2, llm=_spy, taifex_asof="2026-07-01")
    assert "-1.2" in seen["user"]            # 同向的台指期照常納入


def test_format_prediction_forecast_labels_basis_date():
    pred = {
        "signal": "觀望", "direction": "漲", "confidence": "中",
        "bull_signals": [], "bear_signals": [], "hold_ma20": True,
        "hold_support1": False, "reason": "x",
        "indicators": {"close": 203.0, "ma20": 186.5}, "market": None,
    }
    s = format_prediction("台積電 (2330)", "2026-06-30", pred, forecast=True)
    assert "下一交易日" in s and "依 2026-06-30 收盤試算" in s
    # 一般(早盤)模式仍只顯示日期、不加基準說明
    s2 = format_prediction("台積電 (2330)", "2026-06-30", pred)
    assert "收盤試算" not in s2


def test_format_market_prediction_forecast_labels_basis_date():
    out = make_market_prediction(
        {"ma20": 45000}, {"費半SOX": 1.8}, {"direction": "跌", "pct": -0.5},
        taifex_night=None, llm=_fake_llm,
    )
    s = format_market_prediction("2026-06-30", out, forecast=True)
    assert "下一交易日" in s and "依 2026-06-30 收盤試算" in s


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


def test_morning_does_not_overwrite_existing_prediction(tmp_path, monkeypatch):
    import json
    hp = str(tmp_path / "h.json")
    seed = [{"date": "2026-06-30", "stock": "1111",
             "prediction": {"signal": "觀望", "direction": "漲", "confidence": "高",
                            "reason": "ORIGINAL"}, "review": None}]
    with open(hp, "w", encoding="utf-8") as f:
        json.dump(seed, f, ensure_ascii=False)
    monkeypatch.setattr(morning, "HISTORY_PATH", hp)
    monkeypatch.setattr(morning, "tg", type("T", (), {
        "send": staticmethod(lambda t: True)}))

    recs = morning.run(
        today=pd.Timestamp("2026-06-30"), llm=_fake_llm,
        fetch=lambda code, today=None: _df_with_ma20(),
        fetch_idx=lambda today=None: _idx_df(),
        stocks={"A (1111)": {"code": "1111"}},
        **_NO_LEAD,
    )
    # 已存在的當日預測被鎖死：不重新預測、不出現在本次 produced
    assert all(r["stock"] != "1111" for r in recs)
    with open(hp, encoding="utf-8") as f:
        saved = json.load(f)
    rec = [r for r in saved if r["stock"] == "1111"][0]
    assert rec["prediction"]["reason"] == "ORIGINAL"   # 原始預測未被竄改


def test_morning_backup_rerun_is_silent(tmp_path, monkeypatch):
    import json
    hp = str(tmp_path / "h.json")
    # 今日大盤與個股都已預測過（主班次已跑）→ 備援重跑應完全靜默、不誤報缺漏
    seed = [
        {"date": "2026-06-30", "stock": "大盤",
         "prediction": {"direction": "漲", "confidence": "中", "drivers": [],
                        "reason": "x"}, "review": None},
        {"date": "2026-06-30", "stock": "1111",
         "prediction": {"signal": "觀望", "direction": "漲", "reason": "x"},
         "review": None},
    ]
    with open(hp, "w", encoding="utf-8") as f:
        json.dump(seed, f, ensure_ascii=False)
    monkeypatch.setattr(morning, "HISTORY_PATH", hp)
    sends = []
    monkeypatch.setattr(morning, "tg", type("T", (), {
        "send": staticmethod(lambda text: sends.append(text) or True)}))
    out = morning.run(
        today=pd.Timestamp("2026-06-30"), llm=_fake_llm,
        fetch=lambda code, today=None: _df_with_ma20(),
        fetch_idx=lambda today=None: _idx_df(),
        stocks={"A (1111)": {"code": "1111"}},
        **_NO_LEAD,
    )
    assert out == []                 # 沒有新預測
    assert sends == []               # 且完全不發訊息（不誤報缺漏）


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
    assert sum("加權指數" in s for s in sends) == 1     # 大盤完整卡片一則
    # 個股不逐檔推卡片，改為一則精簡總表（含兩檔）
    digests = [s for s in sends if "個股開盤預測出爐" in s]
    assert len(digests) == 1
    assert "A (1111)" in digests[0] and "B (2222)" in digests[0]
