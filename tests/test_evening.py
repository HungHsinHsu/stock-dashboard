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
    assert not any("對錯一覽" in s for s in sends)            # 不逐檔推完整卡片
    assert any("個股收盤復盤出爐" in s for s in sends)         # 但會發一則精簡總表
    assert any("2344" in s for s in sends)                   # 總表含該股
    # 總表同時寫出「預測方向 → 實際方向」，不必自己回推
    assert any("預測漲" in s and "實際漲" in s for s in sends)


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


def test_evening_notifies_when_data_unavailable(tmp_path, monkeypatch):
    hp = str(tmp_path / "h.json")
    _seed(hp, [_stock_rec()])
    sends, lessons = _patch(monkeypatch, hp)
    empty = pd.DataFrame()
    out = evening.run(
        today=pd.Timestamp("2026-06-30"), llm=_fake_llm,
        fetch=lambda code, today=None: empty,        # 個股資料抓不到
        fetch_idx=lambda today=None: empty,          # 連指數也抓不到
        stocks={"華邦電 (2344)": {"code": "2344"}})
    assert out == []
    assert sends and any("尚未" in s for s in sends)   # 資料抓不到也一定通知、不靜默


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


def test_evening_backfills_past_unreviewed_stock(tmp_path, monkeypatch):
    hp = str(tmp_path / "h.json")
    # 6/29 有預測但沒復盤（當天收盤抓不到被跳過）；今天 6/30 復盤應回頭補做 6/29，
    # 不然它會永遠卡在「尚未復盤」。
    _seed(hp, [_stock_rec(date="2026-06-29", review=None)])
    sends, lessons = _patch(monkeypatch, hp)
    evening.run(
        today=pd.Timestamp("2026-06-30"), llm=_fake_llm,
        fetch=lambda code, today=None: _df("2026-06-30"),   # 含 6/29 收盤
        fetch_idx=lambda today=None: _df("2026-06-30"),
        stocks={"華邦電 (2344)": {"code": "2344"}})
    recs = json.load(open(hp, encoding="utf-8"))
    r = next(x for x in recs if x["date"] == "2026-06-29" and x["stock"] == "2344")
    assert (r.get("review") or {}).get("results") is not None   # 6/29 被補結算


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
