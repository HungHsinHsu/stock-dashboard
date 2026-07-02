"""選股掃描器：把「回檔承接法」規則(core.rules)套到一批股票，
挑出『目前訊號＝進場』的候選，讓使用者不必只盯舊清單。

篩選是純規則(不花 AI)：個股走 entry_setup、ETF 走 etf_setup。
資料抓取交給呼叫端傳入的 fetch(code)，方便測試與快取。
"""
from core.indicators import compute_indicators
from core.rules import entry_setup, etf_setup, is_etf


def scan(codes, fetch, foreign_lookup=None, min_rows=60, limit=20):
    """對 codes 逐檔套規則，回符合『進場』的候選(list[dict])，依量縮程度(量比小)排序。

    fetch(code) -> DataFrame（需含足夠歷史算 MA60；不足或抓不到則略過該檔）。
    foreign_lookup(code) -> 外資 dict 或 None（選填；省略時外資當『未知』，
      規則會放行但標示需自行確認——掃描階段不逐檔打外資 API 以免太慢）。
    """
    cands = []
    for code in codes:
        try:
            df = fetch(code)
        except Exception:
            continue
        if df is None or getattr(df, "empty", True) or len(df) < min_rows:
            continue
        ind = compute_indicators(df, {})
        etf = is_etf(code)
        if etf:
            setup = etf_setup(ind, code)
        else:
            fstopped = None
            if foreign_lookup is not None:
                try:
                    fo = foreign_lookup(code)
                    fstopped = fo.get("stopped") if fo else None
                except Exception:
                    fstopped = None
            setup = entry_setup(ind, code, fstopped)
        if setup.get("ceiling") != "進場":
            continue
        cands.append({
            "code": str(code),
            "kind": "ETF" if etf else "個股",
            "at_batch": setup.get("at_batch"),
            "reason": setup.get("reason"),
            "close": ind.get("close"),
            "vol_ratio": ind.get("vol_ratio"),
        })
    # 量縮越明顯(量比越小)越優先；量比缺的排後面
    cands.sort(key=lambda x: (x["vol_ratio"] is None, x["vol_ratio"]
                              if x["vol_ratio"] is not None else 9.9))
    return cands[:limit]
