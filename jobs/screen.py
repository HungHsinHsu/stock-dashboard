"""收盤後選股：每天收盤資料到齊後，依回檔承接法規則掃『當日成交額前 N 大』，
推薦清單推到 Telegram，並存進 DB 供網頁/機器人直接讀（不用再即時掃、不易被限流）。

排程跑（非互動）→ 放慢節流(workers=1、pause 較長)，對 TWSE 友善。
"""
from core.data import fetch_top_turnover, fetch_daily, fetch_foreign_flow
from core.screener import scan
from core.rules import NEAR_PCT
from core.fundamentals import fetch_valuation, valuation_notes
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


# 「支撐N」對應的均線欄位。訊號說「回檔到支撐2」，掛單價就要用該支撐的均線去算。
_BATCH_MA = (("支撐1", "ma5"), ("支撐2", "ma20"), ("支撐3", "ma60"))


def _order_hint(x):
    """把『進場訊號』翻成『明天掛多少』。回一行字串；算不出來回 None。

    存在的理由（實際踩過的坑）：這份清單是收盤後算的，隔天開盤才能下單。原本推播
    只有訊號與理由、一個價格都沒有，使用者早上根本不知道要掛什麼價——等到盤中問完
    再掛，好價位早就過去了。訊號沒有價格，就不是可執行的動作。

    掛單上限＝支撐 ×(1+NEAR_PCT%)，也就是『到價區』的上緣：買在這個價之上，就已經
    不符合「回檔到支撐」的定義，那是追高不是承接。"""
    at = str(x.get("at_batch") or "")
    key = next((k for tag, k in _BATCH_MA if at.startswith(tag)), None)
    sup = x.get(key) if key else None
    if not isinstance(sup, (int, float)):
        return None
    hi = sup * (1 + NEAR_PCT / 100)
    parts = [f"💰 明日掛單 ≤{hi:.2f}（{at.split('(')[0]} {sup:.2f} 的 +{NEAR_PCT:.0f}% 內）"]
    stop = x.get("ma60")
    if isinstance(stop, (int, float)) and hi > stop:
        parts.append(f"🛑停損 {stop:.2f}（季線）")
        parts.append(f"風險 −{(hi - stop) / hi * 100:.1f}%")
    return "　" + "｜".join(parts)


def _day_chg_txt(x):
    """當日漲跌%。沒有這個，一行『空頭排列·季線下』完全看不出它今天是漲停還是續跌——
    實例：華邦電 2026-08-04 漲停 +9.8%，清單上跟一路下跌的股票長得一模一樣。
    『跌破季線』講的是位置，『今天漲跌』講的是方向，兩個都要有才讀得懂。"""
    c, p = x.get("close"), x.get("prev_close")
    if not isinstance(c, (int, float)) or not isinstance(p, (int, float)) or not p:
        return ""
    return f" {(c - p) / p * 100:+.1f}%"


def _line(x, names):
    nm = names.get(x["code"], x["code"])
    where = x.get("at_batch") or x["kind"]
    trend = x.get("trend", "")
    trend_txt = f"〔{trend}〕" if trend else ""
    close_txt = (f"　收{x['close']:g}{_day_chg_txt(x)}"
                 if isinstance(x.get("close"), (int, float)) else "")
    out = (f"・[{x['signal']}] {nm} ({x['code']}){trend_txt}{close_txt}"
           f"：{where}｜{x['reason']}")
    # 只有「進場」才給掛單價：觀望/避開給價格等於變相鼓勵進場，方向就反了。
    if x.get("signal") == "進場":
        hint = _order_hint(x)
        if hint:
            out += "\n" + hint
    return out


MAX_STOP_PCT = 8.0     # 掛單價到季線停損的距離上限；超過代表這筆的風險報酬已經不划算
MAX_POS_PCT = 70.0     # 位階上限（與 core.holdings.HIGH_POS_PCT 同義：中上緣就不追）


def _limit_price(x):
    """該候選的掛單上限＝所在支撐 ×(1+NEAR_PCT%)。算不出來回 (None, None, None)。
    回 (掛單價, 支撐值, 支撐名)。"""
    at = str(x.get("at_batch") or "")
    key = next((k for tag, k in _BATCH_MA if at.startswith(tag)), None)
    sup = x.get(key) if key else None
    if not isinstance(sup, (int, float)):
        return None, None, None
    return sup * (1 + NEAR_PCT / 100), sup, at.split("(")[0]


def _stop_pct(limit_price, ma60):
    """掛單價到季線停損要承受的跌幅%；算不出或掛單價已在季線之下回 None。"""
    if not isinstance(ma60, (int, float)) or not limit_price or limit_price <= ma60:
        return None
    return (limit_price - ma60) / limit_price * 100


def _top_pick(cands, names, valuation=None):
    """從『進場』候選裡挑一檔今日首選，直接給開盤掛單價。回 list[str]。

    這一段的存在理由：使用者要的是「明天掛多少」，不是一份要自己再篩一次的清單。
    但『最優秀』不能是黑箱，所以用可解釋的硬門檻先濾、再用風險報酬排序，並把
    估值數字原樣附上——不打分，因為本益比在不同產業/循環位置的意義完全相反。

    硬門檻（缺一不取）：
      ・訊號＝進場、且是個股（ETF 走另一套框架）
      ・月線 MA20 斜率 > 0：承接法只接「上升趨勢中的回檔」
      ・位階 < MAX_POS_PCT：中上緣不追
      ・掛單價到季線停損 < MAX_STOP_PCT：停損太遠的單，期望值會被吃掉
    排序：停損距離小 → 位階低 → 量比小（風險報酬優先，不是漲得快優先）
    """
    valuation = valuation or {}
    ok, rejected = [], []
    for x in cands:
        if x.get("signal") != "進場" or x.get("kind") == "ETF":
            continue
        lp, sup, sup_name = _limit_price(x)
        slope = x.get("ma20_slope5")
        pos = x.get("pos_pct")
        stop_pct = _stop_pct(lp, x.get("ma60"))
        nm = names.get(x["code"], x["code"])
        if lp is None or stop_pct is None:
            rejected.append(f"{nm}（算不出掛單價/停損）")
        elif not isinstance(pos, (int, float)):
            # 算不出位階就不能放行：硬門檻缺一不取，「沒資料」不等於「位階低」。
            rejected.append(f"{nm}（位階算不出來，資料不足 120 日）")
        elif not (isinstance(slope, (int, float)) and slope > 0):
            rejected.append(f"{nm}（月線走平/下彎，非上升趨勢中的回檔）")
        elif isinstance(pos, (int, float)) and pos >= MAX_POS_PCT:
            rejected.append(f"{nm}（位階 {pos:.0f}% 偏高）")
        elif stop_pct >= MAX_STOP_PCT:
            rejected.append(f"{nm}（停損要 −{stop_pct:.1f}%，太遠）")
        else:
            ok.append((stop_pct, pos if isinstance(pos, (int, float)) else 50.0,
                       x.get("vol_ratio") or 9.9, x, lp, sup, sup_name))
    lines = ["", "──── ⭐ 今日首選（明日開盤前掛單）────"]
    if not ok:
        lines.append("・今天沒有同時通過『月線上揚＋位階不高＋停損夠近』的進場標的 → 不出手。")
        if rejected:
            lines.append("　被刷掉的：" + "、".join(rejected[:6]))
        return lines
    ok.sort(key=lambda t: (t[0], t[1], t[2]))
    stop_pct, pos, vr, x, lp, sup, sup_name = ok[0]
    code = x["code"]
    nm = names.get(code, code)
    lines.append(f"🥇 {nm} ({code})　{x.get('trend', '')}")
    lines.append(f"　💰 掛單 ≤{lp:.2f}（{sup_name} {sup:.2f} 的 +{NEAR_PCT:.0f}% 內）")
    lines.append(f"　🛑 停損 {x['ma60']:.2f}（季線）｜風險 −{stop_pct:.1f}%")
    meta = [f"位階 {pos:.0f}%", f"量比 {vr}", f"月線斜率 +{x['ma20_slope5']:.2f}"]
    lines.append("　📊 " + "｜".join(meta))
    notes = valuation_notes(valuation.get(code))
    lines.append("　💵 " + "｜".join(notes) if notes
                 else "　💵 （今日無估值資料，本益比/殖利率請自行確認）")
    lines.append(f"　理由：{x.get('reason')}")
    if len(ok) > 1:
        alts = "、".join(f"{names.get(t[3]['code'], t[3]['code'])}(−{t[0]:.1f}%)"
                         for t in ok[1:4])
        lines.append(f"　次選：{alts}")
    lines.append("　⚠️ 估值只有本益比/殖利率/淨值比（TWSE 每日公布）；營收年增、毛利率、"
                 "訂單能見度需人工查證，未納入。")
    return lines


def _digest(date, cands, names, top, valuation=None):
    stocks = [x for x in cands if x.get("kind") != "ETF"]
    etfs = [x for x in cands if x.get("kind") == "ETF"]
    lines = [
        f"🔎 今日收盤後選股（回檔承接法・前 {top} 大成交股）— {date}",
        "📏 評選：訊號 進場>觀望>避開 ＞ 回檔到支撐 ＞ 收盤站穩 ＞ 量縮 ＞ 離均線近；禁區/槓桿不列。",
        "",
    ]
    lines += _top_pick(cands, names, valuation)     # 首選擺最前面：這是唯一要立刻動作的一行
    lines += ["", "📈 個股（主）："]
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
        # 7 個月(~145 個交易日)：位階要滿 120 根才算得出來，5 個月(~100 根)不夠，
        # 會讓 _top_pick 的位階門檻整條失效（見 position_pct 的註解）。
        df = (fetch or (lambda x: fetch_daily(x, months=7, workers=2)))(c)
        if df is not None and not getattr(df, "empty", True):
            got["ok"] += 1
        return df

    cands = scan([c for c, _ in uni], fetch=_f, foreign_lookup=fetch_foreign_flow,
                 limit=limit, pause=pause) if uni else []
    # 估值（本益比/殖利率/淨值比）：一次呼叫拿全市場，抓不到就降級成「無估值資料」，
    # 不因此擋掉整份推播——技術面本來就能獨立成立。
    try:
        valuation = fetch_valuation((today or now_tw()).strftime("%Y%m%d"))
    except Exception as e:
        print("[screen] 估值抓取失敗：", type(e).__name__, e)
        valuation = {}
    result = {"date": date, "top": top, "uni_n": len(uni),
              "fetched_n": got["ok"], "names": names, "cands": cands,
              "valuation": {c: valuation[c] for c in
                            (x["code"] for x in cands) if c in valuation}}
    # 只有真的抓到市場清單才覆寫；TWSE 沒回應(清單=0)時保留上一份好結果，不要洗成空的。
    if uni:
        try:
            db.set_state(STATE_KEY, result)      # 存起來供網頁/機器人直接讀
        except Exception as e:
            print("存選股結果失敗：", e)
        _store_foreign_snapshot(db, date, cands)  # 順手補抓追蹤股(含華邦電)的外資存 DB
        try:                                       # 順手把『追蹤清單體質掃描』也存一份(不重複推播)
            from jobs import watch
            watch.run(notify=False)
        except Exception as e:
            print("追蹤清單掃描失敗：", e)
    else:
        print("[screen] 清單抓不到(TWSE 沒回應)，保留上一份選股結果，不覆寫。")
    print(f"[screen] date={date} 清單={len(uni)} 讀取成功={got['ok']} 候選={len(cands)}")
    for x in cands:
        print(f"[screen] {names.get(x['code'], x['code'])} ({x['code']}) "
              f"[{x['signal']}] {x.get('trend', '')} | "
              f"{x.get('at_batch') or x['kind']} | 量比{x.get('vol_ratio')}")
    if notify:
        if cands:
            tg.send(_digest(date, cands, names, top, valuation))
        elif not uni:
            tg.send("🔎 收盤後選股：抓不到市場清單（TWSE 沒回應），稍後系統會再試。")
        else:
            tg.send(f"🔎 收盤後選股：清單 {len(uni)} 檔、成功讀取 {got['ok']} 檔，"
                    "這次沒有合適候選。")
    return result


if __name__ == "__main__":
    run()
