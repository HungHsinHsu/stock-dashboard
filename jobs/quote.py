"""臨時／診斷用：撈指定股票的收盤價與三條均線（支撐1/2/3 = MA5/MA20/MA60），
供人工決定「回檔承接法」的分批掛單價。

跑在 GitHub Actions（乾淨 IP）避開 TWSE 對雲端 IP（Streamlit/本機 sandbox）的限流。

用法：
  python -m jobs.quote                    # 預設四檔（南亞科/玉山金/凱基金/富邦金）
  QUOTE_CODES=2330,2454 python -m jobs.quote
"""
import os
from core.data import fetch_daily
from core.indicators import compute_indicators

DEFAULT = ["2408", "2884", "2883", "2881"]


def run(codes=None):
    if codes is None:
        env = [c.strip() for c in os.environ.get("QUOTE_CODES", "").split(",") if c.strip()]
        codes = env or DEFAULT
    for c in codes:
        try:
            df = fetch_daily(c, months=5)
        except Exception as e:
            print(f"{c}: 抓取失敗 {type(e).__name__}: {e}")
            continue
        if df is None or getattr(df, "empty", True):
            print(f"{c}: 無資料（可能限流或代號錯）")
            continue
        ind = compute_indicators(df, {})
        print(f"===== {c} =====")
        print(f"  資料日   : {df.index[-1].date()}")
        print(f"  收盤/前收: {ind.get('close')} / {ind.get('prev_close')}")
        print(f"  支撐1 MA5 : {ind.get('ma5')}")
        print(f"  支撐2 MA20: {ind.get('ma20')}")
        print(f"  支撐3 MA60: {ind.get('ma60')}")
        print(f"  量比={ind.get('vol_ratio')} 排列={ind.get('ma_align')} "
              f"月線斜率(ma20_slope5)={ind.get('ma20_slope5')}")


if __name__ == "__main__":
    run()
