import json
from core.llm import generate_json
from core.config import DASHBOARD_URL

PREDICTION_SCHEMA = {
    "type": "object",
    "properties": {
        "signal": {"type": "string", "enum": ["進場", "觀望", "避開"]},
        "direction": {"type": "string", "enum": ["漲", "跌"]},
        "confidence": {"type": "string", "enum": ["高", "中", "低"]},
        "bull_signals": {"type": "array", "items": {"type": "string"}},
        "bear_signals": {"type": "array", "items": {"type": "string"}},
        "hold_ma20": {"type": "boolean"},
        "hold_support1": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["signal", "direction", "confidence", "bull_signals",
                 "bear_signals", "hold_ma20", "hold_support1", "reason"],
    "additionalProperties": False,
}

_SYSTEM = (
    "你是嚴謹的台股技術分析師。只做純技術分析（不看基本面、新聞、財報），"
    "依提供的技術指標，預測該股『今日相對昨收的收盤方向（漲或跌）』。\n"
    "務必綜合多項指標權衡，不可只看最近漲跌就順勢給同方向。重點看：\n"
    "・均線：MA5/MA20/MA60、排列(多頭/空頭/糾結)、MA20 斜率\n"
    "・MACD：快慢線與柱狀(macd_hist)正負、轉強/轉弱、背離\n"
    "・KD：K/D 高低檔、黃金/死亡交叉、鈍化\n"
    "・RSI：超買(>70)/超賣(<30)、背離\n"
    "・布林通道：現價相對 boll_upper/boll_lower 的位置\n"
    "・量價：vol_ratio；20 日高低點(high_20d/low_20d)是否突破/跌破；與支撐距離\n"
    "步驟：先分別整理『偏多訊號』與『偏空訊號』(bull_signals / bear_signals，"
    "每條都要引用具體指標數字)，再淨評估得出 direction 與 confidence。"
    "多空訊號相當或彼此矛盾時，confidence 給『低』、direction 取較可能的一方。\n"
    "另給：進場訊號(進場/觀望/避開)、是否站穩 MA20、是否守住支撐1、白話總結理由。"
    "可驗證宣告以『今日收盤 vs 昨日收盤』為準。大盤(加權指數)趨勢一併納入考量。"
)


def make_prediction(indicators, stock_name, market=None, llm=generate_json):
    user = (
        f"股票：{stock_name}\n"
        f"技術指標(到昨日收盤為止)：\n{json.dumps(indicators, ensure_ascii=False)}\n"
        f"大盤(加權指數)摘要：\n{json.dumps(market, ensure_ascii=False)}"
    )
    pred = llm(_SYSTEM, user, PREDICTION_SCHEMA)
    pred["indicators"] = indicators
    pred["market"] = market
    return pred


def format_prediction(stock_name, date, prediction):
    ind = prediction.get("indicators", {})
    ma20 = ind.get("ma20")
    ma20_txt = f"（{ma20:.1f}）" if isinstance(ma20, (int, float)) else ""

    def mark(ok):
        return "✅" if ok else "⚠️"

    conf = prediction.get("confidence")
    conf_txt = f"（信心{conf}）" if conf else ""
    lines = [
        f"📈 {stock_name}｜開盤前預測",
        f"🗓 {date}",
        "",
        f"🚦 訊號：{prediction['signal']}",
        f"🧭 方向：預期{prediction['direction']}{conf_txt}",
    ]
    bull = prediction.get("bull_signals") or []
    bear = prediction.get("bear_signals") or []
    if bull or bear:
        lines.append("")
        lines.append("──── 技術訊號 ────")
        lines += [f"🟢 {s}" for s in bull]
        lines += [f"🔴 {s}" for s in bear]
    lines.append("")
    lines.append("──── 關鍵價位 ────")
    lines.append(f"{mark(prediction['hold_ma20'])} 站穩 MA20{ma20_txt}")
    if ind.get("dist_support1_pct") is not None:
        lines.append(f"{mark(prediction['hold_support1'])} 守住支撐1")
    mk = prediction.get("market") or {}
    if mk.get("direction"):
        pct = mk.get("pct")
        pct_txt = f" {pct:+.2f}%" if isinstance(pct, (int, float)) else ""
        ma_txt = "站上" if mk.get("above_ma20") else "跌破"
        lines.append(f"🌐 大盤(昨收參考)：{mk['direction']}{pct_txt}（{ma_txt}MA20）")
    lines += [
        "",
        "──── 理由 ────",
        prediction["reason"],
        "",
        f"🔗 看圖表：{DASHBOARD_URL}",
    ]
    return "\n".join(lines)
