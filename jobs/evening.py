from core.data import fetch_daily, fetch_index
from core.indicators import compute_indicators
from core.market import market_summary
from core.review import (
    judge, make_review, format_review, hit_rate,
    judge_market, make_market_review, format_market_review,
)
from core.lessons import add_lesson
from core.llm import generate_json
from core.store import load_history, save_history, upsert_record, get_record, HISTORY_PATH
from core.watchlist import all_tracked_stocks
from core.config import DASHBOARD_URL
import core.telegram as tg
from datetime import datetime
from core.tz import today_tw


def _stock_review_digest(items, date):
    """把個股復盤壓成一則精簡總表（避免逐檔卡片洗版）。items=[(name, pred_dir, review)]。
    預測方向與實際方向都寫出來，不必自己回推。"""
    lines = [f"📋 個股收盤復盤出爐（{date}）", ""]
    for name, pred_dir, rv in items:
        hit = "命中 ✅" if rv.get("success") else "未中 ❌"
        lines.append(
            f"🔍 {name}：預測{pred_dir or '—'} → 實際{rv.get('direction_actual', '—')}　{hit}")
    lines += ["", "詳細檢討看網頁，或用 /復盤 代號", f"🔗 {DASHBOARD_URL}"]
    return "\n".join(lines)


def _today_bar(df):
    """取當日 K 棒(開高低收量)供檢討幅度/震盪用；無資料回 None。"""
    if df is None or df.empty:
        return None
    last = df.iloc[-1]
    bar = {}
    for col, key in (("Open", "open"), ("High", "high"), ("Low", "low"),
                     ("Close", "close"), ("Volume", "volume")):
        if col in df.columns:
            try:
                bar[key] = round(float(last[col]), 2)
            except (TypeError, ValueError):
                pass
    return bar


def run(today=None, llm=generate_json, fetch=fetch_daily, fetch_idx=fetch_index, stocks=None):
    from core import db
    db.migrate_from_json()     # DB 首次啟用時匯入舊 JSON（無 DB 則 no-op）
    db.migrate_owner_data()
    stocks = all_tracked_stocks() if stocks is None else stocks
    # 復盤對「執行當日」這天的開盤預測；需確認當日收盤資料已發布才結算。
    date = str(today.date()) if today is not None else str(today_tw())
    idx_df = fetch_idx(today=today)
    market = market_summary(idx_df)
    records = load_history(HISTORY_PATH)
    produced, waiting, stock_summ = [], [], []
    idx_last = str(idx_df.index[-1].date()) if not idx_df.empty else "EMPTY"
    print(f"[evening] db={db.db_enabled()} date={date} 指數最後交易日={idx_last} "
          f"載入紀錄={len(records)} 筆")

    # 大盤復盤（驗方向＋無論對錯都檢討）；唯一自動推播的一則。
    # waiting 只收「有今日預測、還沒復盤、但當日資料未到」的項目——
    # 沒預測(不用結算)或已復盤(已完成)一律不列入，避免誤報。
    mrec = get_record(records, date, "大盤")
    m_pred = bool(mrec and mrec.get("prediction"))
    m_reviewed = bool(mrec and (mrec.get("review") or {}).get("critique"))
    data_ready = (not idx_df.empty) and str(idx_df.index[-1].date()) == date
    print(f"[evening] 大盤: 有預測={m_pred} 已檢討={m_reviewed} 資料到齊={data_ready}")
    if m_pred and not m_reviewed:
        closes = idx_df["Close"] if not idx_df.empty else []
        if data_ready and len(closes) >= 2:
            judged = judge_market(mrec["prediction"], closes.iloc[-1], closes.iloc[-2])
            jm = make_market_review(mrec["prediction"], judged,
                                    today_bar=_today_bar(idx_df), llm=llm)
            mrec["review"] = jm
            records = upsert_record(records, mrec)
            produced.append(mrec)
            if not jm.get("success"):     # 教訓只收『預測錯』的（避免重蹈）
                add_lesson("大盤", date, jm.get("critique"))
            save_history(records, HISTORY_PATH)   # 先存 DB 再推播，網頁才不會落後於推播
            mkt_recs = [r for r in records if r.get("stock") == "大盤"]
            tg.send(format_market_review(date, jm, hit_rate(mkt_recs)))
        else:
            waiting.append("大盤")        # 有預測待結算、但當日資料未到

    for name, cfg in stocks.items():
        rec = get_record(records, date, cfg["code"])
        if rec is None or not rec.get("prediction"):
            continue                      # 沒有今日預測 → 不用結算、不列待辦
        if (rec.get("review") or {}).get("critique"):   # 已有檢討 → 已完成，略過
            print(f"[evening] {name}: 已有檢討，略過")
            continue
        df = fetch(cfg["code"], today=today)
        if df.empty or str(df.index[-1].date()) != date:
            waiting.append(name)          # 有預測待結算、但當日資料未到
            continue

        indicators = compute_indicators(df, cfg.get("supports", {}))
        s1 = cfg.get("supports", {}).get("支撐1 (短期)")
        judged = judge(
            rec["prediction"],
            today_close=indicators["close"],
            prev_close=indicators["prev_close"],
            today_ma20=indicators["ma20"],
            support1=s1,
        )
        review = make_review(rec["prediction"], judged, indicators, name,
                             market=market, today_bar=_today_bar(df), llm=llm)
        rec["review"] = review
        records = upsert_record(records, rec)
        if not review.get("success"):     # 教訓只收『預測錯』的
            add_lesson(cfg["code"], date, review.get("critique"))
        print(f"[evening] {name}: 復盤完成 命中={review['results'].get('direction')}")
        # 個股復盤不逐檔推卡片，改為結束後發一則精簡總表
        produced.append(rec)
        stock_summ.append((name, rec["prediction"].get("direction"), review))

    print(f"[evening] produced={len(produced)} waiting={waiting}")
    if produced:
        save_history(records, HISTORY_PATH)
    if stock_summ:                       # 個股復盤出爐 → 一則總表通知
        tg.send(_stock_review_digest(stock_summ, date))
    if waiting:
        tg.send(
            "⏳ 今日收盤資料尚未到齊：" + "、".join(waiting) +
            "。資料到齊後會自動補做（今天 15:20、18:00 各一班）；"
            "若兩班都過了仍未到，多半是當下抓不到資料，可稍後用 /復盤 手動補。")
    return produced


if __name__ == "__main__":
    import sys
    if "--dry-run" in sys.argv:
        market = market_summary(fetch_index())
        records = load_history(HISTORY_PATH)
        for name, cfg in all_tracked_stocks().items():
            df = fetch_daily(cfg["code"])
            if df.empty:
                print(f"{name}：資料缺漏")
                continue
            date = str(df.index[-1].date())
            rec = get_record(records, date, cfg["code"])
            if not rec:
                print(f"{name}：找不到 {date} 的預測，無法 dry-run 復盤")
                continue
            ind = compute_indicators(df, cfg.get("supports", {}))
            s1 = cfg.get("supports", {}).get("支撐1 (短期)")
            judged = judge(rec["prediction"], ind["close"],
                           ind["prev_close"], ind["ma20"], s1)
            review = make_review(rec["prediction"], judged, ind, name,
                                 market=market)
            same = [r for r in records if r.get("stock") == cfg["code"]]
            print(format_review(name, date, review, hit_rate(same)))
            print()
    else:
        run()
