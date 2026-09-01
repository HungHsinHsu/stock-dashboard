"""維護工具：檢視／刪除某個「非交易日」被排程誤寫進去的預測與復盤紀錄。

颱風假、國定假日若落在平日，morning/evening 排程仍會照跑，可能寫進一筆
沒有行情的髒資料（假預測＋假復盤）。用這個工具把那天整批清乾淨。

安全設計：預設只『列出』，要真的刪除必須明確帶 ADMIN_ACTION=delete。

跑在 GitHub Actions（有 DATABASE_URL secret；本機/雲端 sandbox 連不到 DB）。

用法（workflow_dispatch，job=dbadmin）：
  admin_date=2026-07-10                      # 只列出那天有哪些紀錄
  admin_date=2026-07-10, admin_action=delete # 確認後刪除
  admin_action=stats                         # 方向命中率統計（含盲猜基準，唯讀）
  admin_action=screen                        # 傾印目前選股快照（唯讀）
"""
import os
from collections import defaultdict

from core import db, store


def hit_stats(records):
    """方向命中率統計（唯讀）。回 dict：整體／大盤／個股各一組
    {n, hits, rate, baseline_up}，外加各標的與各月份的分組。

    baseline_up＝同一批日子裡「永遠盲猜漲」的命中率（＝實際收漲的天數比例；
    盲猜跌＝1−它）。預測要贏過盲猜才有存在價值——「我自己猜都比較準」
    這個質疑，要有這個對照組才能被驗證或推翻，光看命中率絕對值會誤判：
    在連跌的月份 45% 可能贏盲猜，在連漲的月份 60% 可能輸盲猜。"""
    rows = []
    for r in records:
        rev = r.get("review") or {}
        if "results" not in rev or "direction" not in rev["results"]:
            continue
        rows.append({
            "stock": str(r.get("stock") or "?"),
            "month": str(r.get("date") or "")[:7],
            "hit": bool(rev["results"]["direction"]),
            "up": rev.get("direction_actual") == "漲",
        })

    def agg(subset):
        n = len(subset)
        if not n:
            return {"n": 0, "hits": 0, "rate": None, "baseline_up": None}
        hits = sum(1 for x in subset if x["hit"])
        ups = sum(1 for x in subset if x["up"])
        return {"n": n, "hits": hits,
                "rate": round(hits / n, 2), "baseline_up": round(ups / n, 2)}

    by_stock, by_month = defaultdict(list), defaultdict(list)
    for x in rows:
        by_stock[x["stock"]].append(x)
        by_month[x["month"]].append(x)
    return {
        "overall": agg(rows),
        "market": agg([x for x in rows if x["stock"] == "大盤"]),
        "stocks": agg([x for x in rows if x["stock"] != "大盤"]),
        "by_stock": {k: agg(v) for k, v in sorted(by_stock.items())},
        "by_month": {k: agg(v) for k, v in sorted(by_month.items())},
    }


def _fmt(label, s):
    if not s["n"]:
        return f"  {label}: 無資料"
    return (f"  {label}: 命中 {s['hits']}/{s['n']}＝{s['rate'] * 100:.0f}%"
            f"｜盲猜漲基準 {s['baseline_up'] * 100:.0f}%"
            f"（盲猜跌 {(1 - s['baseline_up']) * 100:.0f}%）")


def run():
    date = os.environ.get("ADMIN_DATE", "").strip()
    action = os.environ.get("ADMIN_ACTION", "list").strip().lower()
    if not db.db_enabled():
        print("未設定 DATABASE_URL，無法連 DB。中止。")
        return

    if action == "stats":
        # 方向命中率總覽：整體/大盤/個股＋各標的＋各月份，每組都附盲猜基準。
        st = hit_stats(store.load_history())
        print("===== 方向命中率統計 =====")
        print(_fmt("整體", st["overall"]))
        print(_fmt("大盤", st["market"]))
        print(_fmt("個股", st["stocks"]))
        print("----- 各標的 -----")
        for code, s in st["by_stock"].items():
            print(_fmt(code, s))
        print("----- 各月份 -----")
        for month, s in st["by_month"].items():
            print(_fmt(month, s))
        return

    if action == "screen":
        # 傾印目前選股快照 screen:latest：看每檔候選的訊號＋理由＋收盤/均線/量比，
        # 用來查核「某檔為何被標進場/觀望」是否合理（例：漲停股不該標進場）。
        snap = db.get_state("screen:latest") or {}
        cands = snap.get("cands") or []
        print(f"screen:latest date={snap.get('date')} 候選={len(cands)} 檔")
        for x in cands:
            print(f"  [{x.get('signal')}] {x.get('code')} 收{x.get('close')} "
                  f"前收{x.get('prev_close')} MA5={x.get('ma5')} MA20={x.get('ma20')} "
                  f"MA60={x.get('ma60')} 量比={x.get('vol_ratio')} 排列={x.get('trend')}"
                  f"｜{x.get('at_batch') or '-'}｜{x.get('reason')}")
        return

    if not date:
        print("ADMIN_DATE 未設定；請提供要處理的日期（YYYY-MM-DD）。中止。")
        return

    records = store.load_history()
    hit = [r for r in records if r.get("date") == date]
    lessons = db.load_lessons()
    lhit = [x for x in lessons if x.get("date") == date]

    print(f"===== {date} 現有紀錄 =====")
    print(f"predictions（預測/復盤）：{len(hit)} 筆")
    for r in sorted(hit, key=lambda r: r.get("stock") or ""):
        pred = "有" if r.get("prediction") else "無"
        rev = "有" if r.get("review") else "無"
        print(f"  - {r.get('stock')}  預測={pred} 復盤={rev}")
    print(f"lessons（教訓）：{len(lhit)} 筆")
    for x in sorted(lhit, key=lambda x: x.get("stock") or ""):
        print(f"  - {x.get('stock')}: {str(x.get('lesson'))[:60]}")

    if action != "delete":
        print("（僅檢視。確認要清除請重跑並帶 admin_action=delete）")
        return

    n1 = db.delete_predictions(date)
    n2 = db.delete_lessons(date)
    print(f"已刪除：predictions {n1} 筆、lessons {n2} 筆。")


if __name__ == "__main__":
    run()
