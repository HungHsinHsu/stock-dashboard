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


def _market_rec(date="2026-06-30", review=None):
    return {"date": date, "stock": "大盤",
            "prediction": {"direction": "漲", "confidence": "中",
                           "drivers": [], "reason": "x"},
            "review": review}


def _fake_llm(system, user, schema, client=None):
    return {"critique": "量價背離"}


def _patch(monkeypatch, hp):
    monkeypatch.setattr(evening, "HISTORY_PATH", hp)
    sends = []
    monkeypatch.setattr(evening, "tg", type("T", (), {
        "send": staticmethod(lambda t: sends.append(t) or True)}))
    return sends


def test_market_review_pushed(tmp_path, monkeypatch):
    hp = str(tmp_path / "h.json")
    _seed(hp, [_market_rec()])
    sends = _patch(monkeypatch, hp)
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
    sends = _patch(monkeypatch, hp)
    out = evening.run(
        today=pd.Timestamp("2026-06-30"), llm=_fake_llm,
        fetch=lambda code, today=None: _df("2026-06-30"),
        fetch_idx=lambda today=None: _df("2026-06-30"),
        stocks={"華邦電 (2344)": {"code": "2344"}})
    assert len(out) == 1 and out[0]["review"] is not None   # 有復盤、有存
    assert not any("收盤復盤" in s for s in sends)            # 但個股不自動推播


def test_evening_waits_when_today_data_missing(tmp_path, monkeypatch):
    hp = str(tmp_path / "h.json")
    _seed(hp, [_stock_rec()])
    sends = _patch(monkeypatch, hp)
    out = evening.run(
        today=pd.Timestamp("2026-06-30"), llm=_fake_llm,
        fetch=lambda code, today=None: _df("2026-06-29"),     # 當日資料未到
        fetch_idx=lambda today=None: _df("2026-06-29"),
        stocks={"華邦電 (2344)": {"code": "2344"}})
    assert out == []
    assert any("18:00" in s for s in sends)


def test_evening_skips_already_reviewed(tmp_path, monkeypatch):
    hp = str(tmp_path / "h.json")
    _seed(hp, [_market_rec(review={"success": True, "results": {"direction": True}})])
    sends = _patch(monkeypatch, hp)
    out = evening.run(
        today=pd.Timestamp("2026-06-30"), llm=_fake_llm,
        fetch=lambda code, today=None: _df("2026-06-30"),
        fetch_idx=lambda today=None: _df("2026-06-30"),
        stocks={})
    assert out == []                                  # 已復盤→不重複
    assert not any("收盤復盤" in s for s in sends)
