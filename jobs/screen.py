"""收盤後選股：每天收盤資料到齊後，依回檔承接法規則掃『當日成交額前 N 大』，
推薦清單推到 Telegram，並存進 DB 供網頁/機器人直接讀（不用再即時掃、不易被限流）。

排程跑（非互動）→ 放慢節流(workers=1、pause 較長)，對 TWSE 友善。
"""
from core.data import fetch_top_turnover, fetch_daily, fetch_foreign_flow
from core.screener import scan
from core.config import DASHBOARD_URL
import core.telegram as tg
from datetime import datetime

STATE_KEY = "screen:latest"


def _digest(date, cands, names, top):
    lines = [
        f"🔎 今日收盤後選股（回檔承接法・前 {top} 大成交股）— {date}",
        "📏 評選：訊號 進場>觀望>避開 ＞ 回檔到支撐 ＞ 收盤站穩 ＞ 量縮 ＞ 離均線近；禁區/槓桿不列。",
        "",
    ]
    for x in cands:
        nm = names.get(x["code"], x["code"])
        where = x.get("at_batch") or x["kind"]
        trend = x.get("trend", "")
        trend_txt = f"〔{trend}〕" if trend else ""
        lines.append(f"・[{x['signal']}] {nm} ({x['code']}){trend_txt}：{where}｜{x['reason']}")
    lines += ["", "（進場＝四關到位可接；觀望＝趨勢沒破在等；避開＝跌破季線墊底參考）",
              "※ 已逐檔補查外資、資料不齊者已排除，訊號含外資；要追蹤用 /add 代號", f"🔗 {DASHBOARD_URL}"]
    return "\n".join(lines)


def run(today=None, top=150, notify=True, fetch=None, uni_fetch=fetch_top_turnover,
        limit=15, pause=0.05):
    from core import db
    date = str((today or datetime.today()).date())
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
    try:
        db.set_state(STATE_KEY, result)          # 存起來供網頁/機器人直接讀
    except Exception as e:
        print("存選股結果失敗：", e)
    print(f"[screen] date={date} 清單={len(uni)} 讀取成功={got['ok']} 候選={len(cands)}")
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
