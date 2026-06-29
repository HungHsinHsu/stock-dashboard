import pandas as pd


def _last(series):
    if series is None or len(series) == 0:
        return None
    v = series.iloc[-1]
    return None if pd.isna(v) else float(v)


def market_summary(df):
    """加權指數摘要;df 空/None 回 None。值皆 float/bool/None。"""
    if df is None or df.empty:
        return None
    close = df["Close"]
    last_close = _last(close)
    prev_close = _last(close.iloc[:-1]) if len(close) >= 2 else None
    ma20 = _last(df["MA20"]) if "MA20" in df else None
    direction = None
    pct = None
    if last_close is not None and prev_close:
        direction = "漲" if last_close >= prev_close else "跌"
        pct = round((last_close - prev_close) / prev_close * 100, 2)
    return {
        "close": last_close,
        "prev_close": prev_close,
        "ma20": ma20,
        "above_ma20": ma20 is not None and last_close is not None
        and last_close >= ma20,
        "direction": direction,
        "pct": pct,
    }
