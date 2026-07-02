"""選股掃描器：把「回檔承接法」規則(core.rules)套到一批股票，
依『接近承接點的程度』評分排序，一律列出前 N 名相對最好的候選，
每檔標上訊號（進場／觀望；ETF 標順勢偏多／趨勢轉弱觀望）。

只有真正『趨勢已破』的（個股跌破季線＝停損、禁區；ETF 明顯轉空避開）才不列，
因為那些不是值得觀察承接的標的。篩選是純規則、不花 AI。
"""
from core.indicators import compute_indicators
from core.rules import (
    entry_setup, etf_setup, is_etf, is_denied, is_leveraged_etf, ETF_SIGNAL_LABEL,
)

# 一律排名列出：進場最優、觀望次之、避開(跌破季線/趨勢偏弱)墊底但仍列出。
# 只有禁區/槓桿股永遠不列（本來就不玩）。
_SIGNAL_BASE = {"進場": 1000, "觀望": 500, "避開": 100}


def _min_dist_pct(ind):
    """現價距最近一條均線(MA5/20/60)的百分比；越小＝越貼近支撐、越可觀察。"""
    close = ind.get("close")
    ds = [abs(close - v) / v * 100
          for v in (ind.get("ma5"), ind.get("ma20"), ind.get("ma60"))
          if close and v]
    return min(ds) if ds else None


def _label(ceiling, etf):
    return ETF_SIGNAL_LABEL.get(ceiling, ceiling) if etf else ceiling


def scan(codes, fetch, foreign_lookup=None, min_rows=60, limit=10):
    """對 codes 逐檔套規則、評分排序，回前 limit 名候選(list[dict]，含 signal 標籤)。

    fetch(code) -> DataFrame（需足夠歷史算 MA60；不足或抓不到則略過）。
    foreign_lookup(code) -> 外資 dict 或 None（選填；省略時外資當未知，掃描階段不逐檔查）。
    排序：訊號(進場>觀望) → 是否到支撐、站穩、量縮 → 離均線越近；同分時量縮越明顯越前。
    """
    scored = []
    for code in codes:
        if is_denied(code) or is_leveraged_etf(code):
            continue                          # 禁區/槓桿股：本來就不玩，不列
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
        ceil = setup.get("ceiling")
        score = _SIGNAL_BASE.get(ceil, 100)
        if setup.get("at_batch"):
            score += 120                      # 已到某支撐附近（接近買點）
        if setup.get("hold_ok"):
            score += 40                       # 收盤站穩
        if setup.get("vol_ok"):
            score += 40                       # 量縮
        md = _min_dist_pct(ind)
        if md is not None:
            score += max(0.0, 20.0 - md)      # 離均線越近加越多
        scored.append({
            "code": str(code),
            "kind": "ETF" if etf else "個股",
            "signal": _label(ceil, etf),
            "at_batch": setup.get("at_batch"),
            "reason": setup.get("reason"),
            "close": ind.get("close"),
            "vol_ratio": ind.get("vol_ratio"),
            "score": round(score, 1),
        })
    scored.sort(key=lambda x: (-x["score"],
                               x["vol_ratio"] if x["vol_ratio"] is not None else 9.9))
    return scored[:limit]
