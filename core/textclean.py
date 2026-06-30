import re

# 程式變數/欄位名 → 中文（讓 LLM 殘留的變數名也能正常顯示；長鍵先換）
_VAR_MAP = {
    "hold_support1": "守住支撐1", "hold_ma20": "站穩MA20",
    "dist_support1_pct": "距支撐1%", "dist_support3_pct": "距支撐3%",
    "ma20_slope5": "MA20斜率", "macd_signal": "MACD訊號線", "macd_hist": "MACD柱",
    "vol_ratio": "量比", "ma_align": "均線排列", "boll_upper": "布林上軌",
    "boll_lower": "布林下軌", "kd_k": "K值", "kd_d": "D值", "rsi14": "RSI",
    "prev_close": "昨收", "bull_signals": "偏多訊號", "bear_signals": "偏空訊號",
    "signal": "進場訊號", "direction": "方向", "confidence": "信心",
}


def humanize(text):
    """把殘留的程式變數名換成中文說法。

    變數常緊鄰中文（例：『hold_ma20正確』），中英之間 \\b 不會觸發，
    故改用 ASCII 識別字邊界 lookaround；長鍵先換以免被短鍵截斷。
    """
    if not isinstance(text, str):
        return text
    for k in sorted(_VAR_MAP, key=len, reverse=True):
        text = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(k)}(?![A-Za-z0-9_])",
                      _VAR_MAP[k], text)
    return text
