import json
from core.llm import generate_json

CRITIQUE_SCHEMA = {
    "type": "object",
    "properties": {"critique": {"type": "string"}},
    "required": ["critique"],
    "additionalProperties": False,
}

_SYSTEM = (
    "你是台股技術分析助手。早盤的預測在收盤後被驗證為失敗。"
    "請根據當天的技術指標，分析『為什麼預測會錯』，"
    "例如量價背離、假突破、大盤拖累等，給出具體檢討。"
    "檢討時請一併參考當日大盤(加權指數)走勢,例如大盤拖累或大盤帶動。"
)


def judge(prediction, today_close, prev_close, today_ma20, support1):
    direction_actual = "漲" if today_close >= prev_close else "跌"
    hold_ma20_actual = today_ma20 is not None and today_close >= today_ma20
    hold_s1_actual = today_close >= support1
    results = {
        "direction": prediction.get("direction") == direction_actual,
        "hold_ma20": prediction.get("hold_ma20") == hold_ma20_actual,
        "hold_support1": prediction.get("hold_support1") == hold_s1_actual,
    }
    return {
        "actual_close": today_close,
        "prev_close": prev_close,
        "direction_actual": direction_actual,
        "results": results,
        "success": all(results.values()),
    }


def hit_rate(records):
    vals = [
        r["review"]["results"]["direction"]
        for r in records
        if r.get("review") and "results" in r["review"]
    ]
    if not vals:
        return None
    return round(sum(1 for v in vals if v) / len(vals), 2)


def make_review(prediction, judged, indicators, stock_name,
                market=None, llm=generate_json):
    review = dict(judged)
    review["market"] = market
    if judged["success"]:
        review["critique"] = None
        return review
    user = (
        f"股票：{stock_name}\n"
        f"原預測：{json.dumps(prediction, ensure_ascii=False)}\n"
        f"實際結果：{json.dumps(judged, ensure_ascii=False)}\n"
        f"當日指標：{json.dumps(indicators, ensure_ascii=False)}\n"
        f"當日大盤：{json.dumps(market, ensure_ascii=False)}"
    )
    review["critique"] = llm(_SYSTEM, user, CRITIQUE_SCHEMA)["critique"]
    return review


def format_review(stock_name, date, review, rate):
    r = review["results"]

    def mark(ok):
        return "✅" if ok else "❌"

    chg = review["actual_close"] - review["prev_close"]
    lines = [
        f"🔍 {stock_name} 收盤復盤 {date}",
        f"今日收盤：{review['actual_close']:.2f}（{chg:+.2f}）",
        f"方向 實際{review['direction_actual']} {mark(r['direction'])}",
        f"站穩MA20 {mark(r['hold_ma20'])}　守住支撐1 {mark(r['hold_support1'])}",
        f"本日預測：{'命中 ✅' if review['success'] else '未中 ❌'}",
    ]
    mk = review.get("market") or {}
    if mk.get("direction"):
        pct = mk.get("pct")
        pct_txt = f" {pct:+.2f}%" if isinstance(pct, (int, float)) else ""
        lines.append(f"大盤：{mk['direction']}{pct_txt}")
    if rate is not None:
        lines.append(f"歷史方向命中率：{rate * 100:.0f}%")
    if review.get("critique"):
        lines.append(f"檢討：{review['critique']}")
    return "\n".join(lines)
