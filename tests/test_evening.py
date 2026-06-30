import json
import pandas as pd
import jobs.evening as evening


def _df(end="2026-06-30", n=30):
    closes = [float(100 + i) for i in range(n)]
    idx = pd.date_range(end=end, periods=n, freq="D")
    df = pd.DataFrame({"Open": closes, "High": closes, "Low": closes,
                       "Close": closes, "Volume": [1000.0] * n}, index=idx)
    df["MA20"] = df["Close"].rolling(20).mean()
    return df


def _seed(path, date="2026-06-30", review=None):
    recs = [{"date": date, "stock": "2344",
             "prediction": {"signal": "觀望", "direction": "漲", "confidence": "中",
                            "hold_ma20": True, "hold_support1": True, "reason": "x"},
             "review": review}]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(recs, f, ensure_ascii=False)


def _fake_llm(system, user, schema, client=None):
    return {"critique": "量價背離"}


def _patch_tg(monkeypatch):
    sends = []
    monkeypatch.setattr(evening, "tg", type("T", (), {
        "send": staticmethod(lambda t: sends.append(t) or True)}))
    return sends


def test_evening_reviews_when_today_data_present(tmp_path, monkeypatch):
    hp = str(tmp_path / "h.json")
    _seed(hp)
    monkeypatch.setattr(evening, "HISTORY_PATH", hp)
    sends = _patch_tg(monkeypatch)
    out = evening.run(
        today=pd.Timestamp("2026-06-30"), llm=_fake_llm,
        fetch=lambda code, today=None: _df("2026-06-30"),
        fetch_idx=lambda today=None: _df("2026-06-30"),
        stocks={"華邦電 (2344)": {"code": "2344"}})
    assert len(out) == 1
    assert any("收盤復盤" in s for s in sends)


def test_evening_waits_when_today_data_missing(tmp_path, monkeypatch):
    hp = str(tmp_path / "h.json")
    _seed(hp)
    monkeypatch.setattr(evening, "HISTORY_PATH", hp)
    sends = _patch_tg(monkeypatch)
    out = evening.run(
        today=pd.Timestamp("2026-06-30"), llm=_fake_llm,
        fetch=lambda code, today=None: _df("2026-06-29"),   # 當日資料還沒到
        fetch_idx=lambda today=None: _df("2026-06-29"),
        stocks={"華邦電 (2344)": {"code": "2344"}})
    assert out == []
    assert any("18:00" in s for s in sends)            # 通知 18:00 再補跑


def test_evening_skips_already_reviewed(tmp_path, monkeypatch):
    hp = str(tmp_path / "h.json")
    _seed(hp, review={"success": True, "results": {"direction": True}})
    monkeypatch.setattr(evening, "HISTORY_PATH", hp)
    sends = _patch_tg(monkeypatch)
    out = evening.run(
        today=pd.Timestamp("2026-06-30"), llm=_fake_llm,
        fetch=lambda code, today=None: _df("2026-06-30"),
        fetch_idx=lambda today=None: _df("2026-06-30"),
        stocks={"華邦電 (2344)": {"code": "2344"}})
    assert out == []                                   # 已復盤→不重複
    assert not any("收盤復盤" in s for s in sends)
