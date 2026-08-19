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


def test_insti_warning_only_when_foreign_and_total_disagree():
    """第四關只問外資。外資買、投信賣更多時，「外資已停止倒貨」是真的，
    「法人在買」卻是假的——貿聯-KY 2026-08-12 就是這樣（外資+497張／投信−1,318張
    ／三大法人−821張），清單只顯示外資那一面，讀起來像籌碼很好。"""
    poya = {"foreign_net": 497443, "trust_net": -1318430, "total_net": -820962}
    txt = screen._insti_txt(poya)
    assert "法人不同調" in txt
    assert "外資 +497張" in txt and "投信 -1,318張" in txt and "三大法人 -821張" in txt

    # 同向就不印，否則每行都是數字、警示會失效
    assert screen._insti_txt(
        {"foreign_net": 1000, "trust_net": 500, "total_net": 1500}) == ""
    assert screen._insti_txt(
        {"foreign_net": -1000, "trust_net": -500, "total_net": -1500}) == ""
    # 資料不齊不要亂猜
    assert screen._insti_txt({"foreign_net": None, "total_net": 5}) == ""
    assert screen._insti_txt({}) == ""


def test_run_is_idempotent_per_day(monkeypatch):
    """同一天第二次呼叫要直接退場：不重掃、不重推（8/19 雙推播事故的鎖）。"""
    import jobs.screen as screen
    from core import db as _db
    calls = {"fetch": 0, "send": 0}
    monkeypatch.setattr(_db, "db_enabled", lambda: False)
    stored = {}
    monkeypatch.setattr(_db, "get_state", lambda k, default=None: stored.get(k, default))
    monkeypatch.setattr(_db, "set_state", lambda k, v: stored.__setitem__(k, v))
    monkeypatch.setattr(screen.tg, "send", lambda *a, **k: calls.__setitem__("send", calls["send"] + 1))
    monkeypatch.setattr(screen, "_store_foreign_snapshot", lambda *a, **k: None)
    import jobs.watch as watch_mod            # run() 內部會順手跑追蹤掃描——測試不上網
    monkeypatch.setattr(watch_mod, "run", lambda **k: None)

    def uni(top):
        calls["fetch"] += 1
        return [("2330", "台積電")]

    monkeypatch.setattr(screen, "scan", lambda *a, **k: [])
    monkeypatch.setattr(screen, "fetch_valuation", lambda *_: {})
    r1 = screen.run(notify=True, uni_fetch=uni, fetch=lambda c: None)
    assert calls["fetch"] == 1 and r1["date"]
    r2 = screen.run(notify=True, uni_fetch=uni, fetch=lambda c: None)
    assert calls["fetch"] == 1, "第二次不該再掃"
    assert r2 == r1, "第二次要回第一次的結果"
    r3 = screen.run(notify=True, uni_fetch=uni, fetch=lambda c: None, force=True)
    assert calls["fetch"] == 2, "force=True 才能重跑"


def test_digest_flags_missing_otc():
    """TPEx 缺席時，推播要標明只掃了上市——殘缺池不能偽裝成全市場。"""
    import jobs.screen as screen
    out = screen._digest("2026-08-19", [], {}, 150, otc_ok=False)
    assert "上櫃" in out and "抓不到" in out
    out_ok = screen._digest("2026-08-19", [], {}, 150, otc_ok=True)
    assert "抓不到" not in out_ok


def test_top_turnover_meta_reports_otc_status(monkeypatch):
    """fetch_top_turnover 的 meta 要誠實回報上櫃有沒有併進來（空清單＝失敗）。"""
    import core.data as data

    class _R:
        def json(self):
            return [{"Code": "2330", "Name": "台積電", "TradeValue": "1000"}]

    monkeypatch.setattr(data.requests, "get", lambda *a, **k: _R())
    import core.tpex as tpex
    monkeypatch.setattr(tpex, "fetch_tpex_top_turnover", lambda n: [])
    meta = {}
    data.fetch_top_turnover(5, meta=meta)
    assert meta["otc_ok"] is False
    monkeypatch.setattr(tpex, "fetch_tpex_top_turnover",
                        lambda n: [("6588", "東典光電", 999.0)])
    meta = {}
    rows = data.fetch_top_turnover(5, meta=meta)
    assert meta["otc_ok"] is True and ("6588", "東典光電") in rows
    meta = {}
    data.fetch_top_turnover(5, include_otc=False, meta=meta)
    assert meta["otc_ok"] is None
