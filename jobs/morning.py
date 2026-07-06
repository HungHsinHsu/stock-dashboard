from core.data import (
    fetch_daily, fetch_index, fetch_us_overnight, fetch_taifex_detail,
    fetch_foreign_flow, fetch_margin,
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
from core.watchlist import all_tracked_stocks
from core.positions import get_batches
from core.lessons import lessons_prompt
from core.config import DASHBOARD_URL
import core.telegram as tg
import time
from datetime import datetime
from core.tz import today_tw


def _stock_pred_digest(items, date):
    """把個股開盤預測壓成一則精簡總表（避免逐檔卡片洗版）。items=[(name, prediction)]。"""
    lines = [f"📋 今日個股開盤預測出爐（{date}）", ""]
    for name, p in items:
        conf = p.get("confidence")
        conf_txt = f"（{conf}）" if conf else ""
        lines.append(
            f"📈 {name}：{p.get('signal', '—')}｜預期{p.get('direction', '—')}{conf_txt}")
    lines += ["", "詳細看網頁，或用 /預測 代號 即時試算", f"🔗 {DASHBOARD_URL}"]
    return "\n".join(lines)


def run(today=None, llm=generate_json, fetch=fetch_daily,
        fetch_idx=fetch_index, notify=None, stocks=None,
        fetch_us=fetch_us_overnight, fetch_tf=fetch_taifex_detail,
        fetch_fg=fetch_foreign_flow, fetch_mg=fetch_margin):
    from core import db
    db.migrate_from_json()     # DB 首次啟用時匯入舊 JSON（無 DB 則 no-op）
    db.migrate_owner_data()    # 舊單一清單/部位 → admin 帳號命名空間
    stocks = all_tracked_stocks() if stocks is None else stocks
    # 預測以「執行當日」為標籤(今日開盤前預測)，供當日收盤復盤對得上。
    run_date = str(today.date()) if today is not None else str(today_tw())
    index_df = fetch_idx(today=today)
    market = market_summary(index_df)
    _u = fetch_us(with_date=True)
    us, us_asof = _u if isinstance(_u, tuple) else (_u, None)
    # 台指期新鮮度防呆：報表日期不得早於大盤最近收盤日，否則視為過時、丟棄不用。
    tw_last = str(index_df.index[-1].date()) if not index_df.empty else None
    tf_detail = fetch_tf(min_date=tw_last)
    taifex = tf_detail["pct"] if tf_detail else None
    taifex_asof = tf_detail["date"] if tf_detail else None
    print("美股隔夜:", us, "@", us_asof, "| 台指期(夜盤):", taifex, "@", taifex_asof,
          "| 台股上一交易日:", tw_last)
    records = load_history(HISTORY_PATH)
    produced, skipped, failed, market_done = [], [], [], False
    stock_summ = []      # (name, prediction) 供結束後發一則個股預測總表
    locked_any = False   # 今日已有預測被鎖定（多半是備援班次重跑）→ 靜默不報缺漏

    # 大盤(加權指數)開盤預測：以美股隔夜 + 台指期為領先指標、大盤技術面為輔
    # 鐵律：當日大盤預測一旦存在就鎖死，重跑不覆蓋（不可篡改歷史預測）。
    _m_exist = get_record(records, run_date, "大盤")
    if not index_df.empty and not (_m_exist and _m_exist.get("prediction")):
        try:
            idx_ind = compute_indicators(index_df, {})
            mpred = make_market_prediction(idx_ind, us, market, taifex, llm=llm,
                                           lessons=lessons_prompt(records, "大盤"),
                                           taifex_asof=taifex_asof,
                                           us_asof=us_asof, tw_last=tw_last)
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
            margin = fetch_mg(cfg["code"], today=today)
        except Exception as e:
            print(f"{name} 融資融券資料失敗：", e)
            margin = None
        # 單檔 AI 試算失敗不影響其他檔；批次連呼叫時偶爾踩到暫時性限流，
        # 故給第二輪機會（隔一個限流視窗再試），避免像 00830 這樣總是最後一筆被犧牲。
        prediction = None
        for attempt in range(2):
            try:
                prediction = make_prediction(
                    indicators, name, market=market, us_overnight=us, llm=llm,
                    code=cfg["code"], foreign=foreign,
                    batches=get_batches(cfg["code"]),
                    lessons=lessons_prompt(records, cfg["code"]), margin=margin)
                break
            except Exception as e:
                print(f"{name} AI 試算失敗(第{attempt + 1}輪)：{type(e).__name__}: {e}")
                if attempt == 0:
                    time.sleep(30)
        if prediction is None:
            failed.append(name)      # 資料有到、是 AI 試算失敗，跟「沒資料」分開講
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
        # 個股預測不逐檔推卡片，改為結束後發一則精簡總表
        produced.append(record)
        stock_summ.append((name, prediction))

    if produced or market_done:
        save_history(records, HISTORY_PATH)
    if stock_summ:                       # 個股預測出爐 → 一則總表通知
        tg.send(_stock_pred_digest(stock_summ, run_date))
    # 只有「真的沒資料、且今日尚無任何預測」才報缺漏；
    # 備援班次重跑(全被鎖定)或大盤已推但個股鎖定，一律不誤報。
    if not produced and not market_done and not locked_any and not failed:
        tg.send("⚠️ 今日資料缺漏，已跳過開盤預測。")
    else:
        if skipped:      # 真的抓不到日線
            tg.send("⚠️ 今日資料缺漏（抓不到日線），已略過：" + "、".join(skipped))
        if failed:       # 資料有到、只是 AI 試算暫時失敗（跟沒資料分開講清楚）
            tg.send("⚠️ 這幾檔『資料有到、但 AI 試算暫時失敗』（多半是偶發限流，"
                    "非抓不到資料）：" + "、".join(failed) +
                    "。稍後可用 /預測 代號 手動重試。")
    return produced


if __name__ == "__main__":
    import sys
    if "--dry-run" in sys.argv:
        market = market_summary(fetch_index())
        for name, cfg in all_tracked_stocks().items():
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
