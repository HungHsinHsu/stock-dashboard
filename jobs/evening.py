from core.data import fetch_daily, fetch_index
from core.indicators import compute_indicators
from core.market import market_summary
from core.review import (
    judge, make_review, format_review, hit_rate,
    judge_market, format_market_review,
)
from core.llm import generate_json
from core.store import load_history, save_history, upsert_record, get_record, HISTORY_PATH
from core.watchlist import effective_stocks
import core.telegram as tg
from datetime import datetime


def run(today=None, llm=generate_json, fetch=fetch_daily, fetch_idx=fetch_index, stocks=None):
    stocks = effective_stocks() if stocks is None else stocks
    # 復盤對「執行當日」這天的開盤預測；需確認當日收盤資料已發布才結算。
    date = str(today.date()) if today is not None else str(datetime.today().date())
    idx_df = fetch_idx(today=today)
    market = market_summary(idx_df)
    records = load_history(HISTORY_PATH)
    produced, waiting = [], []

    # 大盤復盤（只驗方向）；唯一自動推播的一則。當日指數收盤已發布才結算。
    if not idx_df.empty and str(idx_df.index[-1].date()) == date:
        mrec = get_record(records, date, "大盤")
        closes = idx_df["Close"]
        if (mrec and mrec.get("prediction") and not mrec.get("review")
                and len(closes) >= 2):
            jm = judge_market(mrec["prediction"], closes.iloc[-1], closes.iloc[-2])
            mrec["review"] = jm
            records = upsert_record(records, mrec)
            produced.append(mrec)
            mkt_recs = [r for r in records if r.get("stock") == "大盤"]
            tg.send(format_market_review(date, jm, hit_rate(mkt_recs)))
    elif not idx_df.empty:
        waiting.append("大盤")

    for name, cfg in stocks.items():
        df = fetch(cfg["code"], today=today)
        if df.empty:
            continue
        # 當日收盤(日 K)是否已發布？沒有就先不結算，留待 18:00 再跑。
        if str(df.index[-1].date()) != date:
            waiting.append(name)
            continue
        rec = get_record(records, date, cfg["code"])
        if rec is None or not rec.get("prediction"):
            continue                      # 沒有對應預測就略過，不推播
        if rec.get("review"):             # 已復盤過，避免 18:00 重複
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
                             market=market, llm=llm)
        rec["review"] = review
        records = upsert_record(records, rec)
        # 個股復盤不自動推播（存檔即可，/p 查得到、儀表板看得到）
        produced.append(rec)

    if produced:
        save_history(records, HISTORY_PATH)
    if waiting:
        tg.send(
            "⏳ 今日收盤資料尚未發布（" + "、".join(waiting) +
            "），暫時無法結算。18:00 會自動再復盤一次。")
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
