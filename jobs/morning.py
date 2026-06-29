from core.data import fetch_daily, STOCKS
from core.indicators import compute_indicators
from core.predict import make_prediction, format_prediction, PREDICTION_SCHEMA  # noqa: F401
from core.llm import generate_json
from core.store import load_history, save_history, upsert_record, HISTORY_PATH
import core.telegram as tg


def run(today=None, llm=generate_json, fetch=fetch_daily, notify=None):
    name, cfg = next(iter(STOCKS.items()))
    df = fetch(cfg["code"], today=today)

    if df.empty:
        tg.send("⚠️ 今日資料缺漏，已跳過開盤預測。")
        return None

    date = str(df.index[-1].date()) if today is None else str(today.date())
    indicators = compute_indicators(df, cfg["supports"])
    prediction = make_prediction(indicators, name, llm=llm)

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
            pred = make_prediction(ind, name)
            print(format_prediction(name, str(df.index[-1].date()), pred))
    else:
        run()
