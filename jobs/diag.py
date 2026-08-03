"""一次性診斷：檢查『大盤預測』的兩個領先指標——美股隔夜、台指期夜盤——實際抓到什麼
日期/數值，以及今天已存的大盤預測用了哪一份，確認有沒有拿到放假日/過舊的資料。
"""
import json
import requests
from core.data import (
    fetch_us_overnight, fetch_taifex_detail, fetch_index, HEADERS, US_INDICES,
    TAIFEX_SOURCES, _taifex_pick_row, _row_change_pct, _row_date,
)
from core.tz import now_tw, today_tw


def _taifex_raw():
    """逐支台指期 API 直接打，把『到底回了什麼』攤開來——這是判斷『夜盤沒資料』
    究竟是 (a)連不到 (b)回空清單 (c)有回但沒有 TX 契約 (d)有 TX 但欄位解析不出漲跌
    (e)抓得到但被新鮮度防呆丟掉 的唯一辦法。之前只印最終結果，這五種全長一樣。"""
    for session, url in TAIFEX_SOURCES:
        print(f"\n--- {session}：{url}")
        try:
            res = requests.get(url, headers=HEADERS, timeout=20)
        except Exception as e:
            print(f"    (a) 連線失敗：{type(e).__name__} {e}")
            continue
        print(f"    HTTP {res.status_code}、body {len(res.content)} bytes")
        try:
            data = res.json()
        except Exception as e:
            print(f"    回應不是 JSON：{type(e).__name__} {e}；前 300 字：{res.text[:300]!r}")
            continue
        if not isinstance(data, list):
            print(f"    (b) 回應非清單：{type(data).__name__}；內容前 300 字：{str(data)[:300]}")
            continue
        print(f"    列數={len(data)}")
        if not data:
            print("    (b) 空清單——該場尚未發布 / 當日無此場次")
            continue
        print(f"    欄位={list(data[0].keys())}")
        contracts = sorted({str(r.get('Contract') or r.get('契約') or '').strip()
                            for r in data})
        print(f"    契約種類({len(contracts)})：{contracts[:25]}")
        row = _taifex_pick_row(data)
        if row is None:
            print("    (c) 找不到 TX 契約")
            continue
        print(f"    TX 近月列：{json.dumps(row, ensure_ascii=False)[:500]}")
        print(f"    → 解析 pct={_row_change_pct(row)} date={_row_date(row)}")


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

    print("\n===== 台指期：兩支 API 原始回應 =====")
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
