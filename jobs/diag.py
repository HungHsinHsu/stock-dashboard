"""一次性診斷：看 fetch_daily 對幾檔股票實際抓到什麼（最後幾根日線、最後日期），
用來確認個股頁「現價」是否卡在舊月份（當月被限流漏抓 → 最後收盤停在上月底）。
"""
from core.data import fetch_daily, _fetch_stock_month
from core.tz import now_tw
from datetime import timedelta


def run():
    print("now_tw:", now_tw())
    for code in ["2344", "2330", "2454"]:
        print(f"\n===== {code} =====")
        # 先單獨看『當月』抓到幾根（測當月是否漏抓）
        cur = _fetch_stock_month(code, now_tw())
        print(f"當月單抓 rows={len(cur)}",
              f"最後={cur[-1]['Date'].date()} close={cur[-1]['Close']}" if cur else "當月抓不到！")
        df = fetch_daily(code, months=6, workers=2)
        print(f"fetch_daily rows={len(df)}")
        if not df.empty:
            print(df.tail(4)[["Open", "High", "Low", "Close", "Volume"]].to_string())
            last_date = df.index[-1]
            print(f"last_close={df['Close'].iloc[-1]} last_date={last_date.date()}")
            stale_days = (now_tw().date() - last_date.date()).days
            print(f"距今 {stale_days} 天{'  ⚠️ 疑似過期' if stale_days > 5 else ''}")


if __name__ == "__main__":
    run()
