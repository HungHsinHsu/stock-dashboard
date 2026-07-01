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
from core.store import (
    load_history, save_history, upsert_record, get_record, HISTORY_PATH,
)
from core.watchlist import effective_stocks
from core.positions import get_batches
from core.lessons import lessons_prompt
import core.telegram as tg
from datetime import datetime


def run(today=None, llm=generate_json, fetch=fetch_daily,
        fetch_idx=fetch_index, notify=None, stocks=None,
        fetch_us=fetch_us_overnight, fetch_tf=fetch_taifex,
        fetch_fg=fetch_foreign_flow):
    from core import db
    db.migrate_from_json()     # DB 首次啟用時匯入舊 JSON（無 DB 則 no-op）
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
    locked_any = False   # 今日已有預測被鎖定（多半是備援班次重跑）→ 靜默不報缺漏

    # 大盤(加權指數)開盤預測：以美股隔夜 + 台指期為領先指標、大盤技術面為輔
    # 鐵律：當日大盤預測一旦存在就鎖死，重跑不覆蓋（不可篡改歷史預測）。
    _m_exist = get_record(records, run_date, "大盤")
    if not index_df.empty and not (_m_exist and _m_exist.get("prediction")):
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
    elif _m_exist and _m_exist.get("prediction"):
        locked_any = True
        print(f"大盤 {run_date} 已有預測，鎖定不覆蓋")

    for name, cfg in stocks.items():
        df = fetch(cfg["code"], today=today)
        if df.empty:
            skipped.append(name)
            continue
        date = run_date
        # 鐵律：同日同股預測一旦存在就鎖死，重跑只補缺、不覆蓋（不可篡改歷史）。
        _exist = get_record(records, date, cfg["code"])
        if _exist and _exist.get("prediction"):
            locked_any = True
            print(f"  {name}: 已有 {date} 預測，鎖定不覆蓋")
            continue
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
        print(f"  {name}: 方向={prediction['direction']} "
              f"信心={prediction['confidence']} 訊號={prediction['signal']}")
        # 個股預測不自動推播，僅存檔；改由 Telegram /p 指令查詢、儀表板顯示。
        produced.append(record)

    if produced or market_done:
        save_history(records, HISTORY_PATH)
    # 只有「真的沒資料、且今日尚無任何預測」才報缺漏；
    # 備援班次重跑(全被鎖定)或大盤已推但個股鎖定，一律不誤報。
    if not produced and not market_done and not locked_any:
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
