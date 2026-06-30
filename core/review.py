import json
from core.llm import generate_json
from core.config import DASHBOARD_URL

CRITIQUE_SCHEMA = {
    "type": "object",
    "properties": {"critique": {"type": "string"}},
    "required": ["critique"],
    "additionalProperties": False,
}

_SYSTEM = (
    "你是台股技術分析助手。以下是某股『早盤方向預測』與『當日實際結果』。"
    "不論方向猜對或猜錯，都要檢討、不可因猜對就略過：\n"
    "・方向錯 → 分析為何看錯(量價背離、假突破、大盤拖累、相對強弱誤判等)。\n"
    "・方向對 → 別自滿，檢討：是實力還是運氣？幅度是否如預期(漲/跌得比預期多或少)？"
    "盤中是否劇烈震盪、開高走低或開低走高？有沒有沒料到的狀況？下次能更準的地方？\n"
    "請參考當日 K 棒(開高低收量)與大盤走勢。"
    "輸出精簡分點：3~5 條重點、每條一句話、可直接拿來修正下次判斷；每條獨立一行、以「- 」開頭。"
    "用自然中文，禁止出現程式變數/欄位名(如 hold_ma20、hold_support1、signal、direction、vol_ratio、macd_hist、ma20_slope5、dist_support1_pct、ma_align 等)，改用中文說法(站穩MA20、守住支撐1、進場訊號、方向、量比、MACD柱、MA20斜率、距支撐距離、均線排列)。"
)


def judge(prediction, today_close, prev_close, today_ma20, support1=None):
    direction_actual = "漲" if today_close >= prev_close else "跌"
    hold_ma20_actual = today_ma20 is not None and today_close >= today_ma20
    results = {
        "direction": prediction.get("direction") == direction_actual,
        "hold_ma20": prediction.get("hold_ma20") == hold_ma20_actual,
    }
    if support1 is not None:
        hold_s1_actual = today_close >= support1
        results["hold_support1"] = prediction.get("hold_support1") == hold_s1_actual
    return {
        "actual_close": today_close,
        "prev_close": prev_close,
        "direction_actual": direction_actual,
        "results": results,
        "success": all(results.values()),
    }


def judge_market(prediction, today_close, prev_close):
    """大盤只驗方向（漲/跌），無 MA20/支撐概念。"""
    direction_actual = "漲" if today_close >= prev_close else "跌"
    hit = prediction.get("direction") == direction_actual
    return {
        "actual_close": today_close,
        "prev_close": prev_close,
        "direction_actual": direction_actual,
        "results": {"direction": hit},
        "success": hit,
    }


_MARKET_REVIEW_SYSTEM = (
    "你是台股大盤(加權指數)分析師。以下是早盤對加權指數『開盤方向』的預測與當日實際結果。"
    "不論猜對猜錯都要檢討：\n"
    "・方向錯 → 依美股隔夜、台指期夜盤等領先指標與技術面，分析為何看錯"
    "(開高走低、權值股拖累、夜盤領先指標失靈、過度樂觀/悲觀、量能不足等)。\n"
    "・方向對 → 別自滿，檢討：是實力還是運氣？漲跌幅是否如預期？盤中是否劇烈震盪？"
    "有沒有沒料到的狀況？下次能更準的地方？\n"
    "輸出精簡分點：3~5 條重點，每條一句話；每條獨立一行、以「- 」開頭。"
    "用自然中文，禁止出現程式變數/欄位名(如 hold_ma20、hold_support1、signal、direction、vol_ratio、macd_hist、ma20_slope5、dist_support1_pct、ma_align 等)，改用中文說法(站穩MA20、守住支撐1、進場訊號、方向、量比、MACD柱、MA20斜率、距支撐距離、均線排列)。"
)


def make_market_review(prediction, judged, today_bar=None, llm=generate_json):
    """大盤復盤：無論方向對錯都產生檢討（猜對也要檢討幅度/震盪/是否運氣）。"""
    review = dict(judged)
    user = (
        f"原大盤預測：{json.dumps(prediction, ensure_ascii=False)}\n"
        f"實際結果：{json.dumps(judged, ensure_ascii=False)}\n"
        f"當日加權指數K棒(開高低收)：{json.dumps(today_bar, ensure_ascii=False)}"
    )
    review["critique"] = llm(_MARKET_REVIEW_SYSTEM, user, CRITIQUE_SCHEMA)["critique"]
    return review


def format_market_review(date, review, rate):
    chg = review["actual_close"] - review["prev_close"]
    trend = "📈" if chg >= 0 else "📉"
    lines = [
        "🌐 加權指數｜收盤復盤",
        f"🗓 {date}",
        "",
        f"{trend} 收盤：{review['actual_close']:.2f}（{chg:+.2f}）",
        f"🎯 預測方向：{'命中 ✅' if review['success'] else '未中 ❌'}"
        f"（實際{review['direction_actual']}）",
    ]
    if rate is not None:
        lines += ["", f"📊 大盤方向命中率：{rate * 100:.0f}%"]
    if review.get("critique"):
        lines += ["", "──── 檢討 ────", review["critique"]]
    lines += ["", f"🔗 看圖表：{DASHBOARD_URL}"]
    return "\n".join(lines)


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
                market=None, today_bar=None, llm=generate_json):
    """個股復盤：無論方向對錯都產生檢討（猜對也要檢討幅度/震盪/是否運氣）。"""
    review = dict(judged)
    review["market"] = market
    user = (
        f"股票：{stock_name}\n"
        f"原預測：{json.dumps(prediction, ensure_ascii=False)}\n"
        f"實際結果：{json.dumps(judged, ensure_ascii=False)}\n"
        f"當日K棒(開高低收量)：{json.dumps(today_bar, ensure_ascii=False)}\n"
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
    trend = "📈" if chg >= 0 else "📉"
    lines = [
        f"🔍 {stock_name}｜收盤復盤",
        f"🗓 {date}",
        "",
        f"{trend} 收盤：{review['actual_close']:.2f}（{chg:+.2f}）",
        f"🎯 本日預測：{'命中 ✅' if review['success'] else '未中 ❌'}",
        "",
        "──── 對錯一覽 ────",
        f"{mark(r['direction'])} 方向（實際{review['direction_actual']}）",
        f"{mark(r['hold_ma20'])} 站穩 MA20",
    ]
    if "hold_support1" in r:
        lines.append(f"{mark(r['hold_support1'])} 守住支撐1")
    mk = review.get("market") or {}
    if mk.get("direction"):
        pct = mk.get("pct")
        pct_txt = f" {pct:+.2f}%" if isinstance(pct, (int, float)) else ""
        lines.append(f"🌐 大盤今收：{mk['direction']}{pct_txt}")
    if rate is not None:
        lines += ["", f"📊 歷史方向命中率：{rate * 100:.0f}%"]
    if review.get("critique"):
        lines += ["", "──── 檢討 ────", review["critique"]]
    lines += ["", f"🔗 看圖表：{DASHBOARD_URL}"]
    return "\n".join(lines)
