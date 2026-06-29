import pandas as pd


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss
    out = 100 - (100 / (1 + rs))
    out = out.where(loss != 0, 100.0)  # 全漲：loss=0 -> RSI=100
    return out


def _last(series):
    if series is None or len(series) == 0:
        return None
    v = series.iloc[-1]
    return None if pd.isna(v) else float(v)


def compute_indicators(df: pd.DataFrame, supports: dict) -> dict:
    close = df["Close"]
    last_close = _last(close)
    prev_close = _last(close.iloc[:-1]) if len(close) >= 2 else None
    ma20 = _last(df["MA20"]) if "MA20" in df else _last(close.rolling(20).mean())
    vol = _last(df["Volume"])
    vol_ma20 = _last(df["Volume"].rolling(20).mean())
    s1 = supports.get("支撐1 (短期)")
    s3 = supports.get("支撐3 (長期)")

    def dist(level):
        if last_close is None or not level:
            return None
        return round((last_close - level) / level * 100, 2)

    return {
        "close": last_close,
        "prev_close": prev_close,
        "ma5": _last(close.rolling(5).mean()),
        "ma20": ma20,
        "ma60": _last(close.rolling(60).mean()),
        "rsi14": _last(rsi(close, 14)),
        "vol": vol,
        "vol_ratio": round(vol / vol_ma20, 2) if vol and vol_ma20 else None,
        "dist_support1_pct": dist(s1),
        "dist_support3_pct": dist(s3),
    }
