import json
from core.llm import generate_json
from core.config import DASHBOARD_URL

PREDICTION_SCHEMA = {
    "type": "object",
    "properties": {
        "signal": {"type": "string", "enum": ["進場", "觀望", "避開"]},
        "direction": {"type": "string", "enum": ["漲", "跌"]},
        "hold_ma20": {"type": "boolean"},
        "hold_support1": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["signal", "direction", "hold_ma20", "hold_support1", "reason"],
    "additionalProperties": False,
}

_SYSTEM = (
    "你是台股技術分析助手。根據提供的技術指標，對指定股票做出當日預測。"
    "可驗證宣告以『今日收盤 vs 昨日收盤』為準。"
    "務必同時給：進場訊號(進場/觀望/避開)、方向(漲/跌)、是否站穩MA20、"
    "是否守住支撐1，以及白話理由。理由要引用具體指標。"
    "另提供大盤(加權指數)摘要，預測時請把大盤趨勢一併考慮。"
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

    lines = [
        f"📈 {stock_name}｜開盤預測",
        f"🗓 {date}",
        "",
        f"🚦 訊號：{prediction['signal']}",
        f"🧭 方向：預期{prediction['direction']}",
        "",
        "──── 技術面 ────",
        f"{mark(prediction['hold_ma20'])} 站穩 MA20{ma20_txt}",
        f"{mark(prediction['hold_support1'])} 守住支撐1",
    ]
    mk = prediction.get("market") or {}
    if mk.get("direction"):
        pct = mk.get("pct")
        pct_txt = f" {pct:+.2f}%" if isinstance(pct, (int, float)) else ""
        ma_txt = "站上" if mk.get("above_ma20") else "跌破"
        lines.append(f"🌐 大盤：{mk['direction']}{pct_txt}（{ma_txt}MA20）")
    lines += [
        "",
        "──── 理由 ────",
        prediction["reason"],
        "",
        f"🔗 看圖表：{DASHBOARD_URL}",
    ]
    return "\n".join(lines)
