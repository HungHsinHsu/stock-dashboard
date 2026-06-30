import json
from core.llm import generate_json
from core.config import DASHBOARD_URL
from core.rules import constrain_signal

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
    "另給：進場訊號(進場/觀望/避開)、是否站穩 MA20、是否守住支撐1、白話總結理由。\n"
    "【進場紀律＝回檔承接法(signal 會被規則硬性夾住，切勿手癢追高亂喊進場)】"
    "本法只在『回檔到支撐並收盤止穩、量縮』時分批承接，不追噴出的股票。"
    "・收盤跌破支撐3(長期均線)→避開(停損)；"
    "・回檔到支撐1/MA20/支撐3其一、收盤止穩且量縮→才可進場(該批)；"
    "・帶量站回上方均線且收盤站穩→可進場；"
    "・位置偏高或在真空帶(未到任一支撐)→觀望，錯過無傷。"
    "可驗證宣告以『今日收盤 vs 昨日收盤』為準。大盤(加權指數)趨勢一併納入考量。\n"
    "另提供【美股隔夜】四大指數(費半SOX/Nasdaq/標普500/道瓊)漲跌(%)。"
    "請依【本檔股票所屬產業】調整參考權重：半導體/IC 以費半(SOX)為主、"
    "科技電子看 Nasdaq、傳產與工業看道瓊、其餘看標普；把對應的美股隔夜訊號"
    "納入今日開盤方向判斷(例如半導體股遇費半大漲偏多、大跌偏空)，並在 bull/bear "
    "訊號中具體點名是哪個美股指數。\n"
    "【最重要：嚴禁『大盤/美股漲就預設個股看漲』的 beta 偏誤】"
    "個股當日方向『主要由其自身技術面決定』，大盤與美股只是輔助修正、不是主因。"
    "請務必評估【相對強弱】：若大盤/美股偏多、但本檔自身技術面偏空"
    "(跌破均線、MACD 翻負且柱狀擴大、KD 未交叉、外資賣超、量價背離、相對大盤弱)，"
    "這代表『相對弱勢』，是強烈看跌訊號——此時 direction 應取『跌』，不可因大盤漲就喊漲；"
    "反之大盤弱但個股逆勢強亦然。direction 必須誠實反映『個股自身淨多空』，"
    "不可每檔都偏多；多空若各半，寧可給『跌』或低信心，也不要一律順著大盤喊漲。"
    "判斷後請自我檢查：『我是不是只因為大盤/美股漲就給漲？』若是，重新檢視個股自身訊號。\n"
    "若提供【過去教訓】，請參考過去誤判原因以避免重蹈；但仍以當前技術面客觀判斷，"
    "不可因過去錯誤就一律反向或過度反應。"
)


def make_prediction(indicators, stock_name, market=None, us_overnight=None,
                    llm=generate_json, code=None, foreign=None, batches=None,
                    lessons=""):
    # 客觀相對強弱：個股昨日漲跌 vs 大盤昨日漲跌（負=弱於大盤，看跌參考）
    rel_txt = ""
    try:
        c, pc = indicators.get("close"), indicators.get("prev_close")
        mp = (market or {}).get("pct")
        if c and pc and isinstance(mp, (int, float)):
            sp = (c - pc) / pc * 100
            rel = sp - mp
            tag = "弱於大盤(看跌參考)" if rel < 0 else "強於大盤"
            rel_txt = (f"\n相對強弱：個股昨日 {sp:+.2f}% vs 大盤昨日 {mp:+.2f}%，"
                       f"相對 {rel:+.2f}% → {tag}")
    except Exception:
        pass
    user = (
        f"股票：{stock_name}\n"
        f"技術指標(到昨日收盤為止)：\n{json.dumps(indicators, ensure_ascii=False)}\n"
        f"大盤(加權指數)昨收摘要：\n{json.dumps(market, ensure_ascii=False)}{rel_txt}\n"
        f"美股隔夜四大指數漲跌(%)：\n{json.dumps(us_overnight, ensure_ascii=False)}\n"
        f"外資對本股近期買賣超：\n{json.dumps(foreign, ensure_ascii=False)}"
    )
    if lessons:
        user += f"\n\n{lessons}"
    pred = llm(_SYSTEM, user, PREDICTION_SCHEMA)
    # 進場與否：規則為主、LLM 受限。把 LLM 的 signal 夾進紀律允許範圍。
    foreign_stopped = foreign.get("stopped") if foreign else None
    final_signal, rule_note = constrain_signal(pred, indicators, code,
                                               foreign_stopped)
    # 分批部位：依已進批數調整(三批已滿不加碼；停損且有部位提示全數出場)
    if batches is not None:
        if final_signal == "進場":
            if batches >= 3:
                final_signal = "觀望"
                rule_note = _join_note(rule_note, "三批已滿(3/3)，不再加碼")
            else:
                rule_note = _join_note(
                    rule_note, f"目前 {batches}/3 批，本次符合可進第 {batches + 1} 批")
        elif final_signal == "避開" and batches > 0:
            rule_note = _join_note(
                rule_note, f"停損訊號且手上有 {batches}/3 批，依紀律全數出場（/exit 清空）")

    pred["signal_llm"] = pred.get("signal")     # 保留 LLM 原始判斷供對照
    pred["signal"] = final_signal
    if rule_note:
        pred["signal_rule_note"] = rule_note
    pred["indicators"] = indicators
    pred["market"] = market
    pred["foreign"] = foreign
    pred["batches"] = batches
    return pred


def _join_note(note, extra):
    return (note + "；" + extra) if note else extra


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
    bt = prediction.get("batches")
    if isinstance(bt, int):
        lines.append(f"📦 部位：{bt}/3 批")
    note = prediction.get("signal_rule_note")
    if note:
        lines.append(f"　（紀律調整：{note}）")
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
    fo = prediction.get("foreign") or {}
    if fo.get("net") is not None:
        zhang = fo["net"] / 1000.0   # 股 → 張
        state = "賣超" if zhang < 0 else "買超"
        streak = fo.get("sold_streak") or 0
        streak_txt = f"（連{streak}日賣超）" if streak >= 2 else ""
        lines.append(f"🏦 外資：{state} {abs(zhang):,.0f} 張{streak_txt}")
    mk = prediction.get("market") or {}
    if mk.get("direction"):
        pct = mk.get("pct")
        pct_txt = f" {pct:+.2f}%" if isinstance(pct, (int, float)) else ""
        ma_txt = "站上" if mk.get("above_ma20") else "跌破"
        lines.append(f"🌐 大盤昨收：{mk['direction']}{pct_txt}（{ma_txt}MA20）")
    lines += [
        "",
        "──── 理由 ────",
        prediction["reason"],
        "",
        f"🔗 看圖表：{DASHBOARD_URL}",
    ]
    return "\n".join(lines)


MARKET_PRED_SCHEMA = {
    "type": "object",
    "properties": {
        "direction": {"type": "string", "enum": ["漲", "跌"]},
        "confidence": {"type": "string", "enum": ["高", "中", "低"]},
        "drivers": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string"},
    },
    "required": ["direction", "confidence", "drivers", "reason"],
    "additionalProperties": False,
}

_MARKET_SYSTEM = (
    "你是台股大盤(加權指數)分析師。預測『今日開盤後加權指數相對昨收的方向(漲/跌)』。\n"
    "最重要的領先指標是【台指期夜盤】(盤後盤 15:00~05:00 已反映美股隔夜，最貼近今日開盤)"
    "與【美股隔夜】(尤其費城半導體 SOX 對台股電子權值影響大)；"
    "其次才是大盤自身技術面(均線/MACD/KD/RSI)。\n"
    "以領先指標為主、技術面為輔，列出 drivers(引用具體數字)，再給 direction 與 confidence。"
    "台指期夜盤與美股隔夜方向一致時 confidence 可較高；資料缺漏或彼此矛盾則用『低』。\n"
    "若提供【過去教訓】，參考過去誤判避免重蹈，但仍以當前領先指標與技術面客觀判斷，"
    "不可因過去錯誤就一律反向。"
)


def make_market_prediction(index_indicators, us_overnight, market_data,
                           taifex_night=None, llm=generate_json, lessons=""):
    user = (
        f"美股隔夜漲跌(%)：{json.dumps(us_overnight, ensure_ascii=False)}\n"
        f"台指期夜盤漲跌(%)：{json.dumps(taifex_night, ensure_ascii=False)}\n"
        f"大盤昨收摘要：{json.dumps(market_data, ensure_ascii=False)}\n"
        f"大盤技術指標(到昨收)：{json.dumps(index_indicators, ensure_ascii=False)}"
    )
    if lessons:
        user += f"\n\n{lessons}"
    out = llm(_MARKET_SYSTEM, user, MARKET_PRED_SCHEMA)
    out["us_overnight"] = us_overnight
    out["taifex_night"] = taifex_night
    out["market_data"] = market_data
    return out


def format_market_prediction(date, pred):
    us = pred.get("us_overnight") or {}
    mk = pred.get("market_data") or {}
    conf = pred.get("confidence")
    conf_txt = f"（信心{conf}）" if conf else ""
    lines = [
        "🌐 加權指數｜開盤前預測",
        f"🗓 {date}",
        "",
        f"🔮 預測開盤方向：{pred.get('direction', '—')}{conf_txt}",
    ]
    if us:
        lines.append("")
        lines.append("──── 美股隔夜 ────")
        for name, pct in us.items():
            lines.append(f"{'🟢' if pct >= 0 else '🔴'} {name}：{pct:+.2f}%")
    tf = pred.get("taifex_night")
    tf_txt = f"{tf:+.2f}%" if isinstance(tf, (int, float)) else "（無）"
    lines.append(f"📊 台指期夜盤：{tf_txt}")
    if mk.get("direction"):
        pct = mk.get("pct")
        pt = f" {pct:+.2f}%" if isinstance(pct, (int, float)) else ""
        lines.append(f"🌐 大盤昨收：{mk['direction']}{pt}")
    drivers = pred.get("drivers") or []
    if drivers:
        lines += ["", "──── 依據 ────"] + [f"・{d}" for d in drivers]
    lines += ["", "──── 理由 ────", pred.get("reason", "")]
    lines += ["", f"🔗 看圖表：{DASHBOARD_URL}"]
    return "\n".join(lines)
