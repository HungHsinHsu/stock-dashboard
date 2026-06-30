import json
import pandas as pd
import jobs.evening as evening


def _df(end="2026-06-30", n=30):
    closes = [float(100 + i) for i in range(n)]      # 遞增 → 最後一日相對前一日為漲
    idx = pd.date_range(end=end, periods=n, freq="D")
    df = pd.DataFrame({"Open": closes, "High": closes, "Low": closes,
                       "Close": closes, "Volume": [1000.0] * n}, index=idx)
    df["MA20"] = df["Close"].rolling(20).mean()
    return df


def _seed(path, recs):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(recs, f, ensure_ascii=False)


def _stock_rec(date="2026-06-30", review=None):
    return {"date": date, "stock": "2344",
            "prediction": {"signal": "觀望", "direction": "漲", "confidence": "中",
                           "hold_ma20": True, "hold_support1": True, "reason": "x"},
            "review": review}


def _market_rec(date="2026-06-30", direction="漲", review=None):
    return {"date": date, "stock": "大盤",
            "prediction": {"direction": direction, "confidence": "中",
                           "drivers": [], "reason": "x"},
            "review": review}


def _fake_llm(system, user, schema, client=None):
    return {"critique": "量價背離"}


def _patch(monkeypatch, hp):
    monkeypatch.setattr(evening, "HISTORY_PATH", hp)
    sends, lessons = [], []
    monkeypatch.setattr(evening, "tg", type("T", (), {
        "send": staticmethod(lambda t: sends.append(t) or True)}))
    monkeypatch.setattr(evening, "add_lesson",
                        lambda *a, **k: lessons.append(a))
    return sends, lessons


def test_market_review_pushed(tmp_path, monkeypatch):
    hp = str(tmp_path / "h.json")
    _seed(hp, [_market_rec()])
    sends, lessons = _patch(monkeypatch, hp)
    out = evening.run(
        today=pd.Timestamp("2026-06-30"), llm=_fake_llm,
        fetch=lambda code, today=None: _df("2026-06-30"),
        fetch_idx=lambda today=None: _df("2026-06-30"),
        stocks={})
    assert len(out) == 1
    assert any("加權指數" in s and "收盤復盤" in s for s in sends)   # 大盤復盤有推
    assert out[0]["review"]["results"]["direction"] is True         # 預測漲、實際漲→命中


def test_stock_review_saved_but_not_pushed(tmp_path, monkeypatch):
    hp = str(tmp_path / "h.json")
    _seed(hp, [_stock_rec()])
    sends, lessons = _patch(monkeypatch, hp)
    out = evening.run(
        today=pd.Timestamp("2026-06-30"), llm=_fake_llm,
        fetch=lambda code, today=None: _df("2026-06-30"),
        fetch_idx=lambda today=None: _df("2026-06-30"),
        stocks={"華邦電 (2344)": {"code": "2344"}})
    assert len(out) == 1 and out[0]["review"] is not None   # 有復盤、有存
    assert not any("收盤復盤" in s for s in sends)            # 但個股不自動推播


def test_market_miss_pushes_critique_and_records_lesson(tmp_path, monkeypatch):
    hp = str(tmp_path / "h.json")
    _seed(hp, [_market_rec(direction="跌")])      # 預測跌、實際漲(遞增)→未中
    sends, lessons = _patch(monkeypatch, hp)
    out = evening.run(
        today=pd.Timestamp("2026-06-30"), llm=_fake_llm,
        fetch=lambda code, today=None: _df("2026-06-30"),
        fetch_idx=lambda today=None: _df("2026-06-30"),
        stocks={})
    assert out and out[0]["review"]["success"] is False
    assert any("檢討" in s and "量價背離" in s for s in sends)   # 大盤檢討有推
    assert lessons and lessons[0][0] == "大盤"                  # 教訓已累加


def test_evening_waits_when_today_data_missing(tmp_path, monkeypatch):
    hp = str(tmp_path / "h.json")
    _seed(hp, [_stock_rec()])
    sends, lessons = _patch(monkeypatch, hp)
    out = evening.run(
        today=pd.Timestamp("2026-06-30"), llm=_fake_llm,
        fetch=lambda code, today=None: _df("2026-06-29"),     # 當日資料未到
        fetch_idx=lambda today=None: _df("2026-06-29"),
        stocks={"華邦電 (2344)": {"code": "2344"}})
    assert out == []
    assert any("18:00" in s for s in sends)


def test_evening_skips_when_critique_exists(tmp_path, monkeypatch):
    hp = str(tmp_path / "h.json")
    # 已有檢討的紀錄才會被略過（避免重複/覆蓋既有檢討）
    _seed(hp, [_market_rec(review={"success": True, "results": {"direction": True},
                                   "critique": "已檢討過"})])
    sends, lessons = _patch(monkeypatch, hp)
    out = evening.run(
        today=pd.Timestamp("2026-06-30"), llm=_fake_llm,
        fetch=lambda code, today=None: _df("2026-06-30"),
        fetch_idx=lambda today=None: _df("2026-06-30"),
        stocks={})
    assert out == []                                  # 已有檢討→不重複
    assert not any("收盤復盤" in s for s in sends)


def test_evening_backfills_missing_critique(tmp_path, monkeypatch):
    hp = str(tmp_path / "h.json")
    # 有 review 但缺 critique（舊版猜中沒檢討）→ 應補上檢討
    _seed(hp, [_market_rec(review={"success": True, "results": {"direction": True},
                                   "critique": None})])
    sends, lessons = _patch(monkeypatch, hp)
    out = evening.run(
        today=pd.Timestamp("2026-06-30"), llm=_fake_llm,
        fetch=lambda code, today=None: _df("2026-06-30"),
        fetch_idx=lambda today=None: _df("2026-06-30"),
        stocks={})
    assert out and out[0]["review"].get("critique")   # 檢討被補上
