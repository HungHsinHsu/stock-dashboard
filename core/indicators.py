import pandas as pd


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss
    out = 100 - (100 / (1 + rs))
    out = out.where(loss != 0, 100.0)  # 全漲：loss=0 -> RSI=100
    return out


def _ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def macd(close: pd.Series, fast=12, slow=26, signal=9):
    line = _ema(close, fast) - _ema(close, slow)
    sig = _ema(line, signal)
    return line, sig, line - sig


def kd(df: pd.DataFrame, n=9):
    """台股慣用 KD（RSV 經 1/3 平滑）。"""
    low_n = df["Low"].rolling(n).min()
    high_n = df["High"].rolling(n).max()
    rsv = (df["Close"] - low_n) / (high_n - low_n) * 100
    rsv = rsv.where(high_n != low_n, 50.0)
    k = rsv.ewm(com=2, adjust=False).mean()   # alpha=1/3
    d = k.ewm(com=2, adjust=False).mean()
    return k, d


def _last(series):
    if series is None or len(series) == 0:
        return None
    v = series.iloc[-1]
    return None if pd.isna(v) else float(v)


def _r(v, n=2):
    return None if v is None else round(v, n)


def compute_indicators(df: pd.DataFrame, supports: dict) -> dict:
    close = df["Close"]
    last_close = _last(close)
    prev_close = _last(close.iloc[:-1]) if len(close) >= 2 else None
    ma5 = _last(close.rolling(5).mean())
    ma20_series = df["MA20"] if "MA20" in df else close.rolling(20).mean()
    ma20 = _last(ma20_series)
    ma60 = _last(close.rolling(60).mean())

    macd_line, macd_sig, macd_hist = macd(close)
    k, d = kd(df) if {"High", "Low"} <= set(df.columns) else (None, None)

    std20 = close.rolling(20).std()
    boll_up = _last(ma20_series + 2 * std20)
    boll_low = _last(ma20_series - 2 * std20)
    high20 = _last(df["High"].rolling(20).max()) if "High" in df else None
    low20 = _last(df["Low"].rolling(20).min()) if "Low" in df else None

    vol = _last(df["Volume"]) if "Volume" in df else None
    vol_ma20 = _last(df["Volume"].rolling(20).mean()) if "Volume" in df else None

    # MA20 近 5 日斜率（正=上彎、負=下彎）
    ma20_slope = None
    msv = ma20_series.dropna()
    if len(msv) >= 6:
        ma20_slope = round(float(msv.iloc[-1] - msv.iloc[-6]), 2)

    align = "糾結"
    if None not in (ma5, ma20, ma60):
        if ma5 > ma20 > ma60:
            align = "多頭排列"
        elif ma5 < ma20 < ma60:
            align = "空頭排列"

    s1 = supports.get("支撐1 (短期)")
    s3 = supports.get("支撐3 (長期)")

    def dist(level):
        if last_close is None or not level:
            return None
        return round((last_close - level) / level * 100, 2)

    return {
        "close": last_close,
        "prev_close": prev_close,
        "ma5": _r(ma5),
        "ma20": _r(ma20),
        "ma60": _r(ma60),
        "ma20_slope5": ma20_slope,
        "ma_align": align,
        "rsi14": _r(_last(rsi(close, 14)), 1),
        "kd_k": _r(_last(k), 1),
        "kd_d": _r(_last(d), 1),
        "macd": _r(_last(macd_line), 3),
        "macd_signal": _r(_last(macd_sig), 3),
        "macd_hist": _r(_last(macd_hist), 3),
        "boll_upper": _r(boll_up),
        "boll_lower": _r(boll_low),
        "high_20d": _r(high20),
        "low_20d": _r(low20),
        "vol": vol,
        "vol_ratio": round(vol / vol_ma20, 2) if vol and vol_ma20 else None,
        "dist_support1_pct": dist(s1),
        "dist_support3_pct": dist(s3),
    }
