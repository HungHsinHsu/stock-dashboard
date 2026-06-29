from core.data import fetch_daily, STOCKS
from core.indicators import compute_indicators
from core.review import judge, make_review, format_review, hit_rate
from core.llm import generate_json
from core.store import load_history, save_history, upsert_record, get_record, HISTORY_PATH
import core.telegram as tg


def run(today=None, llm=generate_json, fetch=fetch_daily):
    name, cfg = next(iter(STOCKS.items()))
    df = fetch(cfg["code"], today=today)
    if df.empty:
        tg.send("⚠️ 今日資料缺漏，無法復盤。")
        return None

    date = str(df.index[-1].date()) if today is None else str(today.date())
    records = load_history(HISTORY_PATH)
    rec = get_record(records, date)
    if rec is None or not rec.get("prediction"):
        tg.send(f"⚠️ 找不到 {date} 的開盤預測，略過復盤。")
        return None

    indicators = compute_indicators(df, cfg["supports"])
    s1 = cfg["supports"]["支撐1 (短期)"]
    judged = judge(
        rec["prediction"],
        today_close=indicators["close"],
        prev_close=indicators["prev_close"],
        today_ma20=indicators["ma20"],
        support1=s1,
    )
    review = make_review(rec["prediction"], judged, indicators, name, llm=llm)
    rec["review"] = review
    records = upsert_record(records, rec)
    save_history(records, HISTORY_PATH)

    tg.send(format_review(name, date, review, hit_rate(records)))
    return rec


if __name__ == "__main__":
    run()
