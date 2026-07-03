"""收盤後選股：每天收盤資料到齊後，依回檔承接法規則掃『當日成交額前 N 大』，
推薦清單推到 Telegram，並存進 DB 供網頁/機器人直接讀（不用再即時掃、不易被限流）。

排程跑（非互動）→ 放慢節流(workers=1、pause 較長)，對 TWSE 友善。
"""
from core.data import fetch_top_turnover, fetch_daily, fetch_foreign_flow
from core.screener import scan
from core.config import DASHBOARD_URL
from core.tz import now_tw
import core.telegram as tg
from datetime import datetime

STATE_KEY = "screen:latest"
FOREIGN_KEY = "foreign:latest"   # 每天排程(Actions)抓好的外資快照，供網頁在即時抓不到時回退


def _all_watchlist_codes(db):
    """列舉各帳號追蹤清單裡的所有股票代號（給排程順手補抓外資用）。
    watchlist 結構是 {code: {"name":.., "supports"?}}——代號是 key，不是 value 裡的欄位。"""
    codes = set()
    try:
        for _, wl in (db.get_states_by_prefix("wl:") or {}).items():
            if isinstance(wl, dict):
                codes |= {str(c) for c in wl.keys()}
    except Exception as e:
        print("列舉追蹤清單失敗：", e)
    return codes


def _store_foreign_snapshot(db, date, cands, lookup=None):
    """把『追蹤股＋今日候選股』的外資買賣超抓一份存進 DB。
    網頁個股頁即時抓 TWSE 常被限流，抓不到時就回退讀這份（來源是 Actions，較穩）。"""
    lookup = lookup or fetch_foreign_flow    # 在呼叫時解析，測試可 monkeypatch 模組屬性
    codes = _all_watchlist_codes(db)
    codes |= {x["code"] for x in cands if x.get("kind") != "ETF"}
    fmap = {}
    for c in sorted(codes):
        try:
            fo = lookup(c)
        except Exception:
            fo = None
        if fo and fo.get("stopped") is not None:     # 只存真的抓到的（資料不齊不存）
            fmap[c] = fo
    if fmap:
        try:
            db.set_state(FOREIGN_KEY, {"date": date, "map": fmap})
            print(f"[screen] 外資快照已存 {len(fmap)} 檔（含追蹤股）")
        except Exception as e:
            print("存外資快照失敗：", e)
    else:
        print("[screen] 外資快照：這次一檔都沒抓到，保留上一份。")


def _line(x, names):
    nm = names.get(x["code"], x["code"])
    where = x.get("at_batch") or x["kind"]
    trend = x.get("trend", "")
    trend_txt = f"〔{trend}〕" if trend else ""
    return f"・[{x['signal']}] {nm} ({x['code']}){trend_txt}：{where}｜{x['reason']}"


def _digest(date, cands, names, top):
    stocks = [x for x in cands if x.get("kind") != "ETF"]
    etfs = [x for x in cands if x.get("kind") == "ETF"]
    lines = [
        f"🔎 今日收盤後選股（回檔承接法・前 {top} 大成交股）— {date}",
        "📏 評選：訊號 進場>觀望>避開 ＞ 回檔到支撐 ＞ 收盤站穩 ＞ 量縮 ＞ 離均線近；禁區/槓桿不列。",
        "",
        "📈 個股（主）：",
    ]
    lines += [_line(x, names) for x in stocks] or ["・（今日沒有合適個股）"]
    if etfs:
        lines += ["", "📦 ETF（趨勢參考，走另一套框架，非個股承接法）："]
        lines += [_line(x, names) for x in etfs]
    lines += ["", "（進場＝四關到位可接；觀望＝趨勢沒破在等；避開＝跌破季線墊底參考）",
              "🕒 此清單為當天收盤後一次性快照、盤中不更新；「等站穩」＝隔日承接（隔日回到支撐、"
              "收盤站穩再分批接），非當天再等收盤。",
              "※ 已逐檔補查外資、資料不齊者已排除，訊號含外資；要追蹤用 /add 代號", f"🔗 {DASHBOARD_URL}"]
    return "\n".join(lines)


def run(today=None, top=150, notify=True, fetch=None, uni_fetch=fetch_top_turnover,
        limit=15, pause=0.05):
    from core import db
    date = str((today or now_tw()).date())
    uni = uni_fetch(top) or []
    names = {c: nm for c, nm in uni}
    got = {"ok": 0}

    def _f(c):
        df = (fetch or (lambda x: fetch_daily(x, months=5, workers=2)))(c)
        if df is not None and not getattr(df, "empty", True):
            got["ok"] += 1
        return df

    cands = scan([c for c, _ in uni], fetch=_f, foreign_lookup=fetch_foreign_flow,
                 limit=limit, pause=pause) if uni else []
    result = {"date": date, "top": top, "uni_n": len(uni),
              "fetched_n": got["ok"], "names": names, "cands": cands}
    # 只有真的抓到市場清單才覆寫；TWSE 沒回應(清單=0)時保留上一份好結果，不要洗成空的。
    if uni:
        try:
            db.set_state(STATE_KEY, result)      # 存起來供網頁/機器人直接讀
        except Exception as e:
            print("存選股結果失敗：", e)
        _store_foreign_snapshot(db, date, cands)  # 順手補抓追蹤股(含華邦電)的外資存 DB
    else:
        print("[screen] 清單抓不到(TWSE 沒回應)，保留上一份選股結果，不覆寫。")
    print(f"[screen] date={date} 清單={len(uni)} 讀取成功={got['ok']} 候選={len(cands)}")
    for x in cands:
        print(f"[screen] {names.get(x['code'], x['code'])} ({x['code']}) "
              f"[{x['signal']}] {x.get('trend', '')} | "
              f"{x.get('at_batch') or x['kind']} | 量比{x.get('vol_ratio')}")
    if notify:
        if cands:
            tg.send(_digest(date, cands, names, top))
        elif not uni:
            tg.send("🔎 收盤後選股：抓不到市場清單（TWSE 沒回應），稍後系統會再試。")
        else:
            tg.send(f"🔎 收盤後選股：清單 {len(uni)} 檔、成功讀取 {got['ok']} 檔，"
                    "這次沒有合適候選。")
    return result


if __name__ == "__main__":
    run()
