import pandas as pd
import core.db as db
from jobs import watch


def _df(n=60):
    closes = [100 + i * 0.4 for i in range(n)]
    closes[-1] = closes[-2] + 0.2            # 小紅站穩
    idx = pd.date_range(end="2026-07-02", periods=n, freq="D")
    vols = [1000.0] * n
    vols[-1] = 500.0
    return pd.DataFrame({"Open": closes, "High": [c + 1 for c in closes],
                         "Low": [c - 1 for c in closes], "Close": closes,
                         "Volume": vols}, index=idx)


def test_watch_lists_stock_even_when_foreign_missing(monkeypatch):
    # 追蹤清單掃描：外資查不到也要保留(標觀望)，不像選股那樣剔除
    calls = {}
    monkeypatch.setattr(db, "set_state", lambda k, v: calls.__setitem__(k, v))
    monkeypatch.setattr(db, "migrate_owner_data", lambda: None)
    monkeypatch.setattr(watch, "fetch_foreign_flow", lambda c: {"stopped": None})
    stocks = {"華邦電 (2344)": {"code": "2344"}}
    res = watch.run(notify=False, stocks=stocks, fetch=lambda c: _df())
    codes = [x["code"] for x in res["cands"]]
    assert "2344" in codes                       # 外資缺也保留
    assert calls.get("watch:latest")             # 有存快照
    assert calls.get("bars:2344")                # 有把日線存進 DB 給網頁讀
    assert res["n"] == 1


def test_watch_empty_watchlist(monkeypatch):
    monkeypatch.setattr(db, "migrate_owner_data", lambda: None)
    res = watch.run(notify=False, stocks={})
    assert res["cands"] == [] and res["n"] == 0
