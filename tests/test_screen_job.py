import pandas as pd
import core.db as db
from jobs import screen


def _df(n=60):
    idx = pd.date_range(end="2026-07-02", periods=n, freq="D")
    closes = [100 + i for i in range(n)]
    return pd.DataFrame({"Open": closes, "High": [c + 1 for c in closes],
                         "Low": [c - 1 for c in closes], "Close": closes,
                         "Volume": [1000.0] * n}, index=idx)


def test_run_does_not_overwrite_when_universe_empty(monkeypatch):
    # TWSE 沒回應(清單=0) → 不可覆寫 DB，保留上一份好結果
    calls = []
    monkeypatch.setattr(db, "set_state", lambda k, v: calls.append((k, v)))
    # 清單為空仍會走到估值那行（它在 if uni 之外），不 mock 就會真的打網路
    monkeypatch.setattr(screen, "fetch_valuation", lambda *a, **k: {})
    r = screen.run(uni_fetch=lambda n: [], notify=False)
    assert r["cands"] == [] and calls == []


def test_run_overwrites_when_universe_present(monkeypatch):
    # 有抓到清單 → 正常寫入 screen:latest
    calls = []
    monkeypatch.setattr(db, "set_state", lambda k, v: calls.append((k, v)))
    monkeypatch.setattr(db, "get_states_by_prefix", lambda p: {})
    monkeypatch.setattr(screen, "fetch_foreign_flow", lambda c: {"stopped": True})
    # 這兩支不 mock 的話會真的去打外部 API，在沒有外網的環境要等連線逾時與重試
    # （實測單支 50 秒），而且測到的是網路通不通、不是選股邏輯。
    monkeypatch.setattr(screen, "fetch_valuation", lambda *a, **k: {})
    # screen.run 收尾會順手跑追蹤清單掃描，那支自己會抓日線與法人
    monkeypatch.setattr("jobs.watch.run", lambda **k: None)
    r = screen.run(uni_fetch=lambda n: [("8888", "測試")],
                   fetch=lambda c: _df(), notify=False)
    keys = [k for k, _ in calls]
    assert "screen:latest" in keys
    assert r["uni_n"] == 1


def test_run_stores_foreign_snapshot_for_watchlist(monkeypatch):
    # 排程順手把追蹤股(華邦電 2344)的外資抓一份存 DB，供網頁回退
    stored = {}
    monkeypatch.setattr(db, "set_state", lambda k, v: stored.__setitem__(k, v))
    # 真實結構：watchlist 以「代號」為 key，value 只有 name/supports（沒有 code 欄位）
    monkeypatch.setattr(db, "get_states_by_prefix",
                        lambda p: {"wl:admin": {"2344": {"name": "華邦電 (2344)"}}})
    monkeypatch.setattr(screen, "fetch_foreign_flow",
                        lambda c: {"stopped": False, "sold_streak": 2, "net": -100})
    # 這兩支不 mock 的話會真的去打外部 API，在沒有外網的環境要等連線逾時與重試
    # （實測單支 50 秒），而且測到的是網路通不通、不是選股邏輯。
    monkeypatch.setattr(screen, "fetch_valuation", lambda *a, **k: {})
    # screen.run 收尾會順手跑追蹤清單掃描，那支自己會抓日線與法人
    monkeypatch.setattr("jobs.watch.run", lambda **k: None)
    screen.run(uni_fetch=lambda n: [("8888", "測試")],
               fetch=lambda c: _df(), notify=False)
    snap = stored.get("foreign:latest")
    assert snap and "2344" in snap["map"]
    assert snap["map"]["2344"]["stopped"] is False


def test_run_skips_stocks_whose_price_was_reset(monkeypatch):
    """除權息/分割/減資會把價格重設，而日線是未還原價 → 均線混到事件前後兩種價格。

    寶雅 5904 於 2026-08-10 一拆十（7/29 收 720、8/10 收 79.2），系統照算出
    MA60 586、位階 0.4%、「空頭排列」。那次剛好被判避開，但減資是往上跳——
    會算出假的高位階甚至假的「站上均線」而放行進場。寧可漏一檔，不要放行算錯的訊號。
    """
    calls = []
    monkeypatch.setattr(db, "set_state", lambda k, v: calls.append(k))
    monkeypatch.setattr(db, "get_states_by_prefix", lambda p: {})
    monkeypatch.setattr(screen, "fetch_foreign_flow", lambda c: {"stopped": True})
    monkeypatch.setattr(screen, "fetch_valuation", lambda *a, **k: {})
    monkeypatch.setattr("jobs.watch.run", lambda **k: None)

    split = _df()
    split.iloc[:30, split.columns.get_loc("Close")] *= 10   # 前 30 根是分割前的價格

    r = screen.run(uni_fetch=lambda n: [("5904", "寶雅")],
                   fetch=lambda c: split, notify=False)
    assert r["cands"] == []          # 被排除，不進候選
    assert r["fetched_n"] == 0       # 也不算「讀取成功」


def test_run_keeps_stocks_with_a_clean_price_series(monkeypatch):
    """沒有價格重設的正常股票不能被誤殺——漲停/跌停是合法交易。"""
    calls = []
    monkeypatch.setattr(db, "set_state", lambda k, v: calls.append(k))
    monkeypatch.setattr(db, "get_states_by_prefix", lambda p: {})
    monkeypatch.setattr(screen, "fetch_foreign_flow", lambda c: {"stopped": True})
    monkeypatch.setattr(screen, "fetch_valuation", lambda *a, **k: {})
    monkeypatch.setattr("jobs.watch.run", lambda **k: None)

    r = screen.run(uni_fetch=lambda n: [("8888", "測試")],
                   fetch=lambda c: _df(), notify=False)
    assert r["fetched_n"] == 1
