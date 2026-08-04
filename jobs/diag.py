"""一次性診斷：檢查『大盤預測』的兩個領先指標——美股隔夜、台指期夜盤——實際抓到什麼
日期/數值，以及今天已存的大盤預測用了哪一份，確認有沒有拿到放假日/過舊的資料。
"""
import json
import requests
from core.data import (
    fetch_us_overnight, fetch_taifex_detail, fetch_index, HEADERS, US_INDICES,
    TAIFEX_URL, TAIFEX_SESSIONS, _taifex_pick_row, _row_change_pct, _row_date,
)
from core.fundamentals import fetch_valuation, valuation_notes
from core.tz import now_tw, today_tw


def _taifex_raw():
    """台指期 API 直接打，把『到底回了什麼』攤開來——這是判斷『夜盤沒資料』究竟是
    (a)連不到 (b)回的不是 JSON/空清單 (c)有回但沒有該場的 TX 契約 (d)有 TX 但欄位
    解析不出漲跌 (e)抓得到但被新鮮度防呆丟掉 的唯一辦法。只印最終結果的話，這五種
    全長一樣（實例：FutAH 路徑不存在卻回 200＋Swagger HTML，靜默失敗了不知道多久）。

    兩場都在同一份日報裡，用 TradingSession 分；順便印各場列數與近月完整價格列，
    供核對「盤後 D」的時序（實測是 D-1 晚→D 早，跑在 D 日盤之前）。"""
    print(f"\n--- {TAIFEX_URL}")
    try:
        res = requests.get(TAIFEX_URL, headers=HEADERS, timeout=25)
    except Exception as e:
        print(f"    (a) 連線失敗：{type(e).__name__} {e}")
        return
    print(f"    HTTP {res.status_code}、body {len(res.content)} bytes")
    try:
        data = res.json()
    except Exception as e:
        print(f"    回應不是 JSON：{type(e).__name__} {e}；前 300 字：{res.text[:300]!r}")
        return
    if not isinstance(data, list) or not data:
        print(f"    (b) 回應非清單或空：{type(data).__name__}；前 300 字：{str(data)[:300]}")
        return
    print(f"    列數={len(data)}　欄位={list(data[0].keys())}")
    sessions = {}
    for r in data:
        key = str(r.get("TradingSession"))
        sessions[key] = sessions.get(key, 0) + 1
    print(f"    TradingSession 分佈：{sessions}")

    for session, ts_value in TAIFEX_SESSIONS:
        row = _taifex_pick_row(data, session=ts_value)
        if row is None:
            print(f"    (c) {session}（TradingSession={ts_value}）找不到 TX 契約")
            continue
        print(f"    {session} TX 近月：{json.dumps(row, ensure_ascii=False)[:500]}")
        print(f"      → 解析 pct={_row_change_pct(row)} date={_row_date(row)}")


def _yahoo_last_bars(symbol, n=4):
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{symbol.replace('^', '%5E')}?range=10d&interval=1d")
    try:
        res = requests.get(url, headers=HEADERS, timeout=15).json()
        r = res["chart"]["result"][0]
        ts = r["timestamp"]
        cl = r["indicators"]["quote"][0]["close"]
        import datetime as dt
        out = [(dt.datetime.utcfromtimestamp(t).strftime("%Y-%m-%d"), c)
               for t, c in zip(ts, cl) if c is not None]
        return out[-n:]
    except Exception as e:
        return f"抓不到 {symbol}: {e}"


def _valuation_probe():
    """驗證 TWSE 估值(本益比/殖利率/淨值比) API 與解析。用『上一個交易日』打，
    因為當日收盤後才發布——拿今天去打在盤中一定是非 OK，會誤判成 API 壞掉。"""
    try:
        idx = fetch_index()
        last = idx.index[-1].strftime("%Y%m%d") if not idx.empty else None
    except Exception as e:
        print("  抓大盤失敗，改用今天：", e)
        last = None
    import datetime as _dt
    # 抓不到大盤時退而求其次：往前試幾天，避開「今天盤中尚未發布」被誤判成 API 壞掉
    fallback = [(now_tw() - _dt.timedelta(days=i)).strftime("%Y%m%d") for i in range(0, 5)]
    for ymd in [d for d in ([last] + fallback) if d]:
        val = fetch_valuation(ymd)
        print(f"  date={ymd} → {len(val)} 檔")
        for code in ("2408", "2618", "1476", "0050"):
            v = val.get(code)
            print(f"    {code}: {v}　{'｜'.join(valuation_notes(v)) if v else '(無)'}")
        if val:
            break


def run():
    print("now_tw:", now_tw(), " today_tw:", today_tw())
    print("\n===== TWSE 估值 API（本益比/殖利率/淨值比）=====")
    _valuation_probe()
    print("\n===== 美股隔夜 fetch_us_overnight() =====")
    print(json.dumps(fetch_us_overnight(), ensure_ascii=False))
    print("--- 各指數 Yahoo 最後幾根(日期,收盤) 看有沒有放假日缺一天/最後一天是幾號 ---")
    for name, sym in US_INDICES.items():
        print(f"  {name}({sym}):", _yahoo_last_bars(sym))

    print("\n===== 台指期：API 原始回應（兩場都在同一份） =====")
    _taifex_raw()

    print("\n===== 台指期 fetch_taifex_detail =====")
    print("無 min_date :", fetch_taifex_detail())
    try:                       # 實際 job 用的門檻＝台股上一個交易日，不是 today
        idx = fetch_index()
        tw_last = str(idx.index[-1].date()) if not idx.empty else None
    except Exception as e:
        tw_last = None
        print("抓大盤失敗，無法取得台股上一交易日：", e)
    print(f"min_date={tw_last}（台股上一交易日，實際用的門檻）:",
          fetch_taifex_detail(min_date=tw_last))
    print(f"min_date={today_tw()} :", fetch_taifex_detail(min_date=str(today_tw())))

    print("\n===== 今天已存的大盤預測用了哪份 =====")
    try:
        from core.store import load_history
        recs = [r for r in load_history() if r.get("stock") == "大盤" and r.get("prediction")]
        recs.sort(key=lambda r: r["date"])
        if recs:
            r = recs[-1]
            p = r["prediction"]
            print("date:", r["date"], "direction:", p.get("direction"),
                  "confidence:", p.get("confidence"))
            print("us_overnight:", json.dumps(p.get("us_overnight"), ensure_ascii=False))
            print("taifex_night:", p.get("taifex_night"), "taifex_date:", p.get("taifex_date"),
                  "taifex_session:", p.get("taifex_session"))
        else:
            print("找不到大盤預測紀錄")
    except Exception as e:
        print("讀預測紀錄失敗：", e)


if __name__ == "__main__":
    run()
