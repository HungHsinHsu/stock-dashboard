from core.data import fetch_daily, fetch_index
from core.indicators import compute_indicators
from core.predict import make_prediction, format_prediction, PREDICTION_SCHEMA  # noqa: F401
from core.market import market_summary
from core.llm import generate_json
from core.store import load_history, save_history, upsert_record, HISTORY_PATH
from core.watchlist import effective_stocks
import core.telegram as tg


def run(today=None, llm=generate_json, fetch=fetch_daily,
        fetch_idx=fetch_index, notify=None, stocks=None):
    stocks = effective_stocks() if stocks is None else stocks
    market = market_summary(fetch_idx(today=today))
    records = load_history(HISTORY_PATH)
    produced, skipped = [], []

    for name, cfg in stocks.items():
        df = fetch(cfg["code"], today=today)
        if df.empty:
            skipped.append(name)
            continue
        date = str(df.index[-1].date()) if today is None else str(today.date())
        indicators = compute_indicators(df, cfg.get("supports", {}))
        prediction = make_prediction(indicators, name, market=market, llm=llm)
        record = {
            "date": date,
            "stock": cfg["code"],
            "prediction": prediction,
            "review": None,
        }
        records = upsert_record(records, record)
        tg.send(format_prediction(name, date, prediction))
        produced.append(record)

    if produced:
        save_history(records, HISTORY_PATH)
    if not produced:
        tg.send("⚠️ 今日資料缺漏，已跳過開盤預測。")
    elif skipped:
        tg.send("⚠️ 今日資料缺漏，已略過：" + "、".join(skipped))
    return produced


if __name__ == "__main__":
    import sys
    if "--dry-run" in sys.argv:
        market = market_summary(fetch_index())
        for name, cfg in effective_stocks().items():
            df = fetch_daily(cfg["code"])
            if df.empty:
                print(f"{name}：資料缺漏")
                continue
            ind = compute_indicators(df, cfg.get("supports", {}))
            pred = make_prediction(ind, name, market=market)
            print(format_prediction(name, str(df.index[-1].date()), pred))
            print()
    else:
        run()
