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
from core.watchlist import effective_stocks
from core.config import DASHBOARD_URL
import core.telegram as tg
from datetime import datetime


def _stock_review_digest(items, date):
    """把個股復盤壓成一則精簡總表（避免逐檔卡片洗版）。items=[(name, review)]。"""
    lines = [f"📋 個股收盤復盤出爐（{date}）", ""]
    for name, rv in items:
        hit = "命中 ✅" if rv.get("success") else "未中 ❌"
        lines.append(f"🔍 {name}：{hit}（實際{rv.get('direction_actual', '—')}）")
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
    stocks = effective_stocks() if stocks is None else stocks
    # 復盤對「執行當日」這天的開盤預測；需確認當日收盤資料已發布才結算。
    date = str(today.date()) if today is not None else str(datetime.today().date())
    idx_df = fetch_idx(today=today)
    market = market_summary(idx_df)
    records = load_history(HISTORY_PATH)
    produced, waiting, stock_summ = [], [], []
    idx_last = str(idx_df.index[-1].date()) if not idx_df.empty else "EMPTY"
    print(f"[evening] db={db.db_enabled()} date={date} 指數最後交易日={idx_last} "
          f"載入紀錄={len(records)} 筆")

    # 大盤復盤（驗方向＋無論對錯都檢討）；唯一自動推播的一則。
    if not idx_df.empty and str(idx_df.index[-1].date()) == date:
        mrec = get_record(records, date, "大盤")
        closes = idx_df["Close"]
        _m_reviewed = bool(mrec and (mrec.get("review") or {}).get("critique"))
        print(f"[evening] 大盤: 有預測={bool(mrec and mrec.get('prediction'))} "
              f"已檢討={_m_reviewed}")
        if (mrec and mrec.get("prediction") and not _m_reviewed
                and len(closes) >= 2):
            judged = judge_market(mrec["prediction"], closes.iloc[-1], closes.iloc[-2])
            jm = make_market_review(mrec["prediction"], judged,
                                    today_bar=_today_bar(idx_df), llm=llm)
            mrec["review"] = jm
            records = upsert_record(records, mrec)
            produced.append(mrec)
            if not jm.get("success"):     # 教訓只收『預測錯』的（避免重蹈）
                add_lesson("大盤", date, jm.get("critique"))
            mkt_recs = [r for r in records if r.get("stock") == "大盤"]
            tg.send(format_market_review(date, jm, hit_rate(mkt_recs)))
    elif not idx_df.empty:
        waiting.append("大盤")
    else:                                  # 連指數資料都抓不到 → 也要告知，不可靜默
        waiting.append("大盤（收盤資料尚未取得）")

    for name, cfg in stocks.items():
        df = fetch(cfg["code"], today=today)
        if df.empty:
            waiting.append(name)           # 抓不到當日資料 → 也列入待結算，不靜默
            continue
        # 當日收盤(日 K)是否已發布？沒有就先不結算，留待 18:00 再跑。
        if str(df.index[-1].date()) != date:
            waiting.append(name)
            continue
        rec = get_record(records, date, cfg["code"])
        if rec is None or not rec.get("prediction"):
            print(f"[evening] {name}: 找不到 {date} 預測，略過")
            continue                      # 沒有對應預測就略過，不推播
        if (rec.get("review") or {}).get("critique"):   # 已有檢討才略過
            print(f"[evening] {name}: 已有檢討，略過")
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
        stock_summ.append((name, review))

    print(f"[evening] produced={len(produced)} waiting={waiting}")
    if produced:
        save_history(records, HISTORY_PATH)
    if stock_summ:                       # 個股復盤出爐 → 一則總表通知
        tg.send(_stock_review_digest(stock_summ, date))
    if waiting:
        tg.send(
            "⏳ 今日收盤資料尚未到齊（" + "、".join(waiting) +
            "），暫時無法結算；等資料發布後會自動再復盤（今天 18:00 前那班）。")
    return produced


if __name__ == "__main__":
    import sys
    if "--dry-run" in sys.argv:
        market = market_summary(fetch_index())
        records = load_history(HISTORY_PATH)
        for name, cfg in effective_stocks().items():
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
