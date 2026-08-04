"""臨時／診斷用：撈指定股票的收盤價與三條均線（支撐1/2/3 = MA5/MA20/MA60），
供人工決定「回檔承接法」的分批掛單價。

跑在 GitHub Actions（乾淨 IP）避開 TWSE 對雲端 IP（Streamlit/本機 sandbox）的限流。

用法：
  python -m jobs.quote                    # 預設四檔（南亞科/玉山金/凱基金/富邦金）
  QUOTE_CODES=2330,2454 python -m jobs.quote
"""
import os
from core.data import fetch_daily, fetch_foreign_flow, fetch_intraday
from core.indicators import compute_indicators
from core.holdings import position_pct, HIGH_POS_PCT
from core.tz import now_tw

DEFAULT = ["2408", "2884", "2883", "2881"]


def _print_intraday(codes):
    """盤中即時報價（唯一的盤中資料來源）。⚠️ 只供人工看『現在到哪了』——
    紀律判斷一律看收盤，盤中影線會把均線規則洗成雜訊。"""
    print(f"===== 盤中即時（查詢時間 {now_tw():%Y-%m-%d %H:%M:%S} 台灣）=====")
    quotes = fetch_intraday(codes)
    if not quotes:
        print("  抓不到盤中報價（非交易時段、或 MIS 未回應）")
        return
    for c in codes:
        q = quotes.get(str(c))
        if not q:
            print(f"  {c}: 無報價")
            continue
        chg = f"{q['chg_pct']:+.2f}%" if q.get("chg_pct") is not None else "—"
        print(f"  {q.get('name') or c} ({c}): {q.get('price')} {chg}"
              f"（昨收 {q.get('prev_close')}｜開 {q.get('open')} 高 {q.get('high')} "
              f"低 {q.get('low')}｜量 {q.get('volume')}｜{q.get('at')}｜{q.get('source')}）")
    print()


def run(codes=None):
    if codes is None:
        env = [c.strip() for c in os.environ.get("QUOTE_CODES", "").split(",") if c.strip()]
        codes = env or DEFAULT
    _print_intraday(codes)
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
        # 位階：判斷「貴不貴」的關鍵。同樣的進場訊號，位階 30% 跟 85% 的風險報酬天差地遠，
        # 所以決定掛單價時一定要跟均線一起看（≥HIGH_POS_PCT 時承接法連加碼都會擋下來）。
        pos = position_pct(df)
        if pos is None:
            print("  位階     : 無法計算（資料不足 120 日，可能是新上市）")
        else:
            print(f"  位階     : {pos:.1f}%（近120日收盤區間；≥{HIGH_POS_PCT:.0f}% 算中上緣）")
        # 最近 7 根收盤，看近期走勢(噴高→回落 or 續強)
        tail = df.tail(7)
        print("  近7日收盤:", ", ".join(f"{d.date()}={round(float(v), 2)}"
              for d, v in tail["Close"].items()))
        # 外資買賣超(T86)：判斷賣壓有沒有停、是承接法第四關
        try:
            fo = fetch_foreign_flow(c)
        except Exception as e:
            fo = {"err": f"{type(e).__name__}: {e}"}
        print(f"  外資T86: {fo}")


if __name__ == "__main__":
    run()
