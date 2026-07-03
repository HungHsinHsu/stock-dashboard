"""日線序列化：把 fetch_daily/fetch_index 回的 DataFrame 存進 DB（Actions 端每天存），
網頁端讀回來重建 DataFrame——這樣網頁不必自己去打證交所，永遠不會被限流。
"""
import pandas as pd

_COLS = (("Open", "o"), ("High", "h"), ("Low", "l"), ("Close", "c"), ("Volume", "v"))


def _num(v):
    try:
        return round(float(v), 4)
    except (TypeError, ValueError):
        return None


def dump_bars(df, keep=420):
    """DataFrame(index=日期, 含 OHLCV) → list[dict]，只留最後 keep 根（約 1.7 年）。"""
    if df is None or getattr(df, "empty", True):
        return []
    d = df.tail(keep)
    out = []
    for idx, row in d.iterrows():
        rec = {"d": str(pd.Timestamp(idx).date())}
        for col, k in _COLS:
            if col in d.columns:
                rec[k] = _num(row[col])
        out.append(rec)
    return out


def load_bars(rows):
    """list[dict] → DataFrame(index=DatetimeIndex, 含 OHLCV＋MA20)；空則回空表。"""
    if not rows:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume", "MA20"])
    idx = pd.to_datetime([r["d"] for r in rows])
    data = {col: [r.get(k) for r in rows] for col, k in _COLS}
    df = pd.DataFrame(data, index=idx).sort_index()
    df["MA20"] = df["Close"].rolling(20).mean()
    return df
