from core.data import (
    fetch_daily, fetch_index, fetch_us_overnight, fetch_taifex,
    fetch_foreign_flow,
)
from core.indicators import compute_indicators
from core.predict import (
    make_prediction, format_prediction, PREDICTION_SCHEMA,  # noqa: F401
    make_market_prediction, format_market_prediction,
)
from core.market import market_summary
from core.llm import generate_json
from core.store import load_history, save_history, upsert_record, HISTORY_PATH
from core.watchlist import effective_stocks
from core.positions import get_batches
from core.lessons import lessons_prompt
import core.telegram as tg
from datetime import datetime


def run(today=None, llm=generate_json, fetch=fetch_daily,
        fetch_idx=fetch_index, notify=None, stocks=None,
        fetch_us=fetch_us_overnight, fetch_tf=fetch_taifex,
        fetch_fg=fetch_foreign_flow):
    stocks = effective_stocks() if stocks is None else stocks
    # 預測以「執行當日」為標籤(今日開盤前預測)，供當日收盤復盤對得上。
    run_date = str(today.date()) if today is not None else str(datetime.today().date())
    index_df = fetch_idx(today=today)
    market = market_summary(index_df)
    us = fetch_us()
    taifex = fetch_tf()
    print("美股隔夜:", us, "| 台指期(夜盤):", taifex)
    records = load_history(HISTORY_PATH)
    produced, skipped, market_done = [], [], False

    # 大盤(加權指數)開盤預測：以美股隔夜 + 台指期為領先指標、大盤技術面為輔
    if not index_df.empty:
        try:
            idx_ind = compute_indicators(index_df, {})
            mpred = make_market_prediction(idx_ind, us, market, taifex, llm=llm,
                                           lessons=lessons_prompt(records, "大盤"))
            records = upsert_record(records, {
                "date": run_date, "stock": "大盤",
                "prediction": mpred, "review": None})
            market_done = True
            tg.send(format_market_prediction(run_date, mpred))
        except Exception as e:
            print("大盤預測失敗：", e)

    for name, cfg in stocks.items():
        df = fetch(cfg["code"], today=today)
        if df.empty:
            skipped.append(name)
            continue
        date = run_date
        indicators = compute_indicators(df, cfg.get("supports", {}))
        try:
            foreign = fetch_fg(cfg["code"], today=today)
        except Exception as e:
            print(f"{name} 外資資料失敗：", e)
            foreign = None
        try:
            prediction = make_prediction(indicators, name, market=market,
                                         us_overnight=us, llm=llm,
                                         code=cfg["code"], foreign=foreign,
                                         batches=get_batches(cfg["code"]),
                                         lessons=lessons_prompt(records, cfg["code"]))
        except Exception as e:  # 單檔預測失敗不影響其他檔
            print(f"{name} 預測失敗：", e)
            skipped.append(name)
            continue
        record = {
            "date": date,
            "stock": cfg["code"],
            "prediction": prediction,
            "review": None,
        }
        records = upsert_record(records, record)
        # 個股預測不自動推播，僅存檔；改由 Telegram /p 指令查詢、儀表板顯示。
        produced.append(record)

    if produced or market_done:
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
