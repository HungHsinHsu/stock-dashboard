"""選股掃描器：把「回檔承接法」規則(core.rules)套到一批股票，
依『接近承接點的程度』評分排序，一律列出前 N 名相對最好的候選，
每檔標上訊號（進場／觀望；ETF 標順勢偏多／趨勢轉弱觀望）。

只有真正『趨勢已破』的（個股跌破季線＝停損、禁區；ETF 明顯轉空避開）才不列，
因為那些不是值得觀察承接的標的。篩選是純規則、不花 AI。
"""
import time
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


def _group_first(items, limit, etf_limit):
    """個股優先、ETF 分開放：個股排前面（各自依分數，就算全觀望也在最上面），
    ETF 收在後面當『趨勢參考』，不跟個股搶排名、不洗版。"""
    def _key(x):
        # 先照訊號分級(進場>觀望>避開)，同級再照『接近進場的程度』(score：到支撐/站穩/
        # 量縮/離均線近越高)，最後量縮越明顯越前。這樣同是觀望，越接近進場的排越前。
        rank = x.get("_rank", x.get("score", 0))
        score = x.get("score", 0)
        vr = x["vol_ratio"] if x.get("vol_ratio") is not None else 9.9
        return (-rank, -score, vr)
    stocks = sorted([x for x in items if x.get("kind") != "ETF"], key=_key)
    etfs = sorted([x for x in items if x.get("kind") == "ETF"], key=_key)
    out = stocks[:limit] + etfs[:etf_limit]
    for it in out:
        it.pop("_rank", None)
    return out


def _trend_label(ind):
    """波段體質：均線排列(多頭/空頭/糾結) ＋ 是否站上季線(MA60)。
    多頭排列·站上季線＝體質好的回檔；空頭排列·季線下＝多半是反彈，別當波段。"""
    align = ind.get("ma_align") or "均線糾結"
    close, ma60 = ind.get("close"), ind.get("ma60")
    if close and ma60:
        return f"{align}·{'站上季線' if close >= ma60 else '季線下'}"
    return align


def scan(codes, fetch, foreign_lookup=None, min_rows=60, limit=10, pause=0.0,
         etf_limit=8, drop_incomplete=True):
    """對 codes 逐檔套規則、評分排序，回前 limit 名候選(list[dict]，含 signal 標籤)。

    fetch(code) -> DataFrame（需足夠歷史算 MA60；不足或抓不到則略過）。
    foreign_lookup(code) -> 外資 dict（含 'stopped'）或 None。為省呼叫，掃描階段先把外資當
      未知；排序取前 limit 名後，『只對這些入選檔補查外資、重新定訊號』（便宜、且讓標籤反映真實外資）。
    排序：訊號(進場>觀望>避開) → 是否到支撐、站穩、量縮 → 離均線越近；同分時量縮越明顯越前。
    """
    prelim = []
    for code in codes:
        if is_denied(code) or is_leveraged_etf(code):
            continue                          # 禁區/槓桿股：本來就不玩，不列
        if pause:
            time.sleep(pause)                 # 節流，降低 TWSE 限流風險
        try:
            df = fetch(code)
        except Exception:
            continue
        if df is None or getattr(df, "empty", True) or len(df) < min_rows:
            continue
        ind = compute_indicators(df, {})
        etf = is_etf(code)
        setup = etf_setup(ind, code) if etf else entry_setup(ind, code, None)
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
        item = {
            "code": str(code), "kind": "ETF" if etf else "個股",
            "signal": _label(ceil, etf), "at_batch": setup.get("at_batch"),
            "reason": setup.get("reason"), "close": ind.get("close"),
            "prev_close": ind.get("prev_close"),   # 供網頁過期時用快照算漲跌幅
            "vol_ratio": ind.get("vol_ratio"), "score": round(score, 1),
            "trend": _trend_label(ind),
            # 三段支撐價（＝MA5/20/60），供「每日策略頁」算掛單價/停損線，免再抓一次
            "ma5": ind.get("ma5"), "ma20": ind.get("ma20"), "ma60": ind.get("ma60"),
            "ma20_slope5": ind.get("ma20_slope5"),
            # 技術面四關到位、只差外資＝激進版(左側)可當天接；保守版(右側)要外資也停手才接
            "tech_ready": setup.get("tech_ready"),
        }
        prelim.append((item, ind, code, etf, ceil))
    prelim.sort(key=lambda t: (-t[0]["score"],
                               t[0]["vol_ratio"] if t[0]["vol_ratio"] is not None else 9.9))

    if foreign_lookup is None:
        return _group_first([item for item, *_ in prelim], limit, etf_limit)

    # 外資確認：對排名靠前的候選(多取緩衝)逐檔補查外資。
    # 原則：資料要齊——個股若『查不到外資』就整檔剔除（不推薦資料不全的標的），
    # 查得到才用真實外資定訊號(進場/觀望)；ETF 不看外資，直接保留。
    kept = []
    for item, ind, code, etf, ceil in prelim[:max(limit * 2, 30)]:
        if etf:
            item["_rank"] = _SIGNAL_BASE.get(ceil, 100)
            kept.append(item)
            continue
        try:
            fo = foreign_lookup(code)
        except Exception:
            fo = None
        stopped = (fo or {}).get("stopped")
        if stopped is None and drop_incomplete:
            continue                          # 選股模式：外資不齊就剔除（不推薦資料不全的標的）
        # 追蹤清單模式(drop_incomplete=False)：外資不齊也保留，用 None→entry_setup 給觀望
        s2 = entry_setup(ind, code, stopped)
        item["signal"] = _label(s2["ceiling"], etf)
        item["reason"] = s2["reason"]
        item["tech_ready"] = s2.get("tech_ready")   # 用真實外資重判後的技術到位旗標
        item["_rank"] = _SIGNAL_BASE.get(s2["ceiling"], 100)
        kept.append(item)
    return _group_first(kept, limit, etf_limit)
