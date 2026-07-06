"""一次性診斷：檢查『大盤預測』的兩個領先指標——美股隔夜、台指期夜盤——實際抓到什麼
日期/數值，以及今天已存的大盤預測用了哪一份，確認有沒有拿到放假日/過舊的資料。
"""
import json
import requests
from core.data import fetch_us_overnight, fetch_taifex_detail, HEADERS, US_INDICES
from core.tz import now_tw, today_tw


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


def run():
    print("now_tw:", now_tw(), " today_tw:", today_tw())
    print("\n===== 美股隔夜 fetch_us_overnight() =====")
    print(json.dumps(fetch_us_overnight(), ensure_ascii=False))
    print("--- 各指數 Yahoo 最後幾根(日期,收盤) 看有沒有放假日缺一天/最後一天是幾號 ---")
    for name, sym in US_INDICES.items():
        print(f"  {name}({sym}):", _yahoo_last_bars(sym))

    print("\n===== 台指期 fetch_taifex_detail =====")
    print("無 min_date :", fetch_taifex_detail())
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
            print("taifex_night:", p.get("taifex_night"), "taifex_date:", p.get("taifex_date"))
        else:
            print("找不到大盤預測紀錄")
    except Exception as e:
        print("讀預測紀錄失敗：", e)


if __name__ == "__main__":
    run()
