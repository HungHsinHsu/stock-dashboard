import json
from core.llm import generate_json

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
)


def make_prediction(indicators, stock_name, llm=generate_json):
    user = (
        f"股票：{stock_name}\n"
        f"技術指標(到昨日收盤為止)：\n{json.dumps(indicators, ensure_ascii=False)}"
    )
    pred = llm(_SYSTEM, user, PREDICTION_SCHEMA)
    pred["indicators"] = indicators
    return pred


def format_prediction(stock_name, date, prediction):
    ind = prediction.get("indicators", {})
    ma20 = ind.get("ma20")
    ma20_txt = f"{ma20:.1f}" if isinstance(ma20, (int, float)) else "—"
    return (
        f"📈 {stock_name} 開盤預測 {date}\n"
        f"進場訊號：{prediction['signal']}\n"
        f"方向：預期{prediction['direction']}\n"
        f"站穩MA20：{'是' if prediction['hold_ma20'] else '否'}(MA20={ma20_txt})\n"
        f"守住支撐1：{'是' if prediction['hold_support1'] else '否'}\n"
        f"理由：{prediction['reason']}"
    )
