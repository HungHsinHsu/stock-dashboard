from core.data import fetch_daily, fetch_index, STOCKS
from core.indicators import compute_indicators
from core.predict import make_prediction, format_prediction, PREDICTION_SCHEMA  # noqa: F401
from core.market import market_summary
from core.llm import generate_json
from core.store import load_history, save_history, upsert_record, HISTORY_PATH
import core.telegram as tg


def run(today=None, llm=generate_json, fetch=fetch_daily,
        fetch_idx=fetch_index, notify=None):
    name, cfg = next(iter(STOCKS.items()))
    df = fetch(cfg["code"], today=today)

    if df.empty:
        tg.send("⚠️ 今日資料缺漏，已跳過開盤預測。")
        return None

    date = str(df.index[-1].date()) if today is None else str(today.date())
    indicators = compute_indicators(df, cfg["supports"])
    market = market_summary(fetch_idx(today=today))
    prediction = make_prediction(indicators, name, market=market, llm=llm)

    record = {
        "date": date,
        "stock": cfg["code"],
        "prediction": prediction,
        "review": None,
    }
    records = upsert_record(load_history(HISTORY_PATH), record)
    save_history(records, HISTORY_PATH)

    tg.send(format_prediction(name, date, prediction))
    return record


if __name__ == "__main__":
    import sys
    if "--dry-run" in sys.argv:
        name, cfg = next(iter(STOCKS.items()))
        df = fetch_daily(cfg["code"])
        if df.empty:
            print("資料缺漏")
        else:
            ind = compute_indicators(df, cfg["supports"])
            market = market_summary(fetch_index())
            pred = make_prediction(ind, name, market=market)
            print(format_prediction(name, str(df.index[-1].date()), pred))
    else:
        run()
