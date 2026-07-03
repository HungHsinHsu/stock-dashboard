import pandas as pd
from core.review import judge, hit_rate, make_review, format_review, CRITIQUE_SCHEMA
import jobs.evening as evening
from core.store import save_history


def test_judge_all_hit():
    pred = {"direction": "跌", "hold_ma20": False, "hold_support1": False,
            "signal": "觀望"}
    j = judge(pred, today_close=201.0, prev_close=203.0,
              today_ma20=205.0, support1=222)
    assert j["direction_actual"] == "跌"
    assert j["results"]["direction"] is True
    # 201 < 205 -> 實際沒站穩；預測也是否(False) -> 兩者相同 -> 命中(True)
    assert j["results"]["hold_ma20"] is True  # 命中判定：實際是否站穩 == 預測
    assert j["success"] is True


def test_judge_direction_hit_but_hold_miss_still_counts_as_hit():
    # 方向對(預測跌、實際跌)但『站穩 MA20』猜錯 → 仍算命中(success=方向)，
    # 不再出現「預測跌→實際跌 未中」這種自相矛盾。
    pred = {"direction": "跌", "hold_ma20": True, "hold_support1": True}
    j = judge(pred, today_close=201.0, prev_close=203.0,
              today_ma20=190.0, support1=222)          # 實際站穩(201>190)，但預測也說站穩→其實對
    # 用一個真的站穩猜錯的情境：實際沒站穩、預測說站穩
    j2 = judge({"direction": "跌", "hold_ma20": True},
               today_close=201.0, prev_close=203.0, today_ma20=205.0)
    assert j2["results"]["direction"] is True          # 方向命中
    assert j2["results"]["hold_ma20"] is False          # 站穩猜錯
    assert j2["success"] is True                        # 但整體仍算命中(以方向為準)


def test_judge_direction_miss():
    pred = {"direction": "漲", "hold_ma20": True, "hold_support1": True}
    j = judge(pred, today_close=201.0, prev_close=203.0,
              today_ma20=190.0, support1=222)
    assert j["results"]["direction"] is False
    assert j["success"] is False


def test_hit_rate():
    recs = [
        {"review": {"results": {"direction": True}}},
        {"review": {"results": {"direction": False}}},
        {"review": None},
    ]
    assert hit_rate(recs) == 0.5
    assert hit_rate([{"review": None}]) is None


def test_make_review_success_still_critiques():
    # 猜對也要檢討（幅度/震盪/是否運氣）
    judged = {"success": True, "results": {}}
    out = make_review({}, judged, {}, "華邦電 (2344)",
                      llm=lambda s, u, sc: {"critique": "方向對但漲幅不如預期"})
    assert out["critique"] == "方向對但漲幅不如預期"


def test_make_review_failure_calls_llm():
    judged = {"success": False, "results": {}}
    out = make_review({"reason": "r"}, judged, {"rsi14": 42}, "華邦電 (2344)",
                      llm=lambda s, u, sc: {"critique": "量背離"})
    assert out["critique"] == "量背離"


def test_make_review_failure_calls_llm_with_market():
    judged = {"success": False, "results": {}}
    out = make_review({"reason": "r"}, judged, {"rsi14": 42}, "華邦電 (2344)",
                      market={"direction": "跌"},
                      llm=lambda s, u, sc: {"critique": "大盤拖累"})
    assert out["critique"] == "大盤拖累"
    assert out["market"]["direction"] == "跌"


def test_make_review_success_keeps_market():
    judged = {"success": True, "results": {}}
    out = make_review({}, judged, {}, "華邦電 (2344)",
                      market={"direction": "漲"},
                      llm=lambda s, u, sc: {"critique": "運氣成分高"})
    assert out["critique"] == "運氣成分高"
    assert out["market"]["direction"] == "漲"


def test_make_market_review_hit_still_critiques():
    from core.review import make_market_review
    out = make_market_review({"direction": "漲"}, {"success": True},
                             llm=lambda s, u, sc: {"critique": "漲幅超乎預期"})
    assert out["critique"] == "漲幅超乎預期"


def test_make_market_review_miss_critique():
    from core.review import make_market_review
    out = make_market_review(
        {"direction": "漲"}, {"success": False, "direction_actual": "跌"},
        llm=lambda s, u, sc: {"critique": "夜盤領先指標失靈、開高走低"})
    assert out["critique"] == "夜盤領先指標失靈、開高走低"


def test_format_review_shows_market():
    review = {
        "actual_close": 201.0, "prev_close": 203.0, "direction_actual": "跌",
        "results": {"direction": True, "hold_ma20": True, "hold_support1": False},
        "success": False, "critique": "大盤拖累",
        "market": {"direction": "跌", "pct": -0.7, "above_ma20": False},
    }
    s = format_review("華邦電 (2344)", "2026-06-30", review, 0.6)
    assert "大盤" in s and "復盤" in s
    assert "streamlit.app" in s  # 附上圖表連結


def _idx_df(n=30):
    import pandas as pd
    closes = [float(45000 + i) for i in range(n)]
    idx = pd.date_range("2026-05-01", periods=n, freq="D")
    df = pd.DataFrame({"Open": closes, "High": closes, "Low": closes,
                       "Close": closes}, index=idx)
    df["MA20"] = df["Close"].rolling(20).mean()
    return df


def test_evening_run_updates_record(tmp_path, monkeypatch):
    hp = str(tmp_path / "h.json")
    save_history([{
        "date": "2026-06-30", "stock": "2344",
        "prediction": {"direction": "跌", "hold_ma20": False,
                       "hold_support1": False, "signal": "觀望",
                       "indicators": {}},
        "review": None,
    }], hp)
    monkeypatch.setattr(evening, "HISTORY_PATH", hp)
    sent = {}
    monkeypatch.setattr(evening, "tg", type("T", (), {
        "send": staticmethod(lambda text: sent.setdefault("t", text) or True)}))

    idx = pd.date_range("2026-05-01", periods=30, freq="D")
    closes = [203.0] * 29 + [201.0]
    df = pd.DataFrame({"Open": closes, "High": closes, "Low": closes,
                       "Close": closes, "Volume": [1000.0] * 30}, index=idx)
    df.index = list(idx[:-1]) + [pd.Timestamp("2026-06-30")]
    df["MA20"] = df["Close"].rolling(20).mean()

    recs = evening.run(today=pd.Timestamp("2026-06-30"),
                       llm=lambda s, u, sc: {"critique": "x"},
                       fetch=lambda code, today=None: df,
                       fetch_idx=lambda today=None: _idx_df(),
                       stocks={"華邦電 (2344)": {"code": "2344",
                               "supports": {"支撐1 (短期)": 222}}})
    assert recs
    rec = recs[0]
    assert rec["review"]["actual_close"] == 201.0
    assert rec["review"]["market"]["direction"] == "漲"
    assert "復盤" in sent["t"]
