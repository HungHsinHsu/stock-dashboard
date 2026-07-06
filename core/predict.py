import json
import re
from core.llm import generate_json
from core.config import DASHBOARD_URL
from core.rules import constrain_signal, is_etf, ETF_UNDERLYING, ETF_SIGNAL_LABEL
from core.textclean import humanize


def _chart_link(stock_name):
    """個股圖表深連結：?code=代號，讓儀表板直接開到該股個股頁。"""
    m = re.search(r"\(([0-9A-Za-z]+)\)\s*$", stock_name or "")
    sep = "&" if "?" in DASHBOARD_URL else "?"
    return f"{DASHBOARD_URL}{sep}code={m.group(1)}" if m else DASHBOARD_URL

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
    "另提供【籌碼面】法人三大(外資/投信/自營商/合計)買賣超與融資融券："
    "外資投信『同買』是較強的偏多籌碼、法人合計方向反映主力態度；"
    "外資仍連續賣超(未止穩)偏空且依紀律不宜進場承接；"
    "融資餘額大增代表散戶追高、屬過熱警訊(回檔承接法要避免追高)，"
    "融券過高則留意軋空。把籌碼面納入多空權衡並在訊號中具體點名。\n"
    "【最重要：嚴禁『大盤/美股漲就預設個股看漲』的 beta 偏誤】"
    "個股當日方向『主要由其自身技術面決定』，大盤與美股只是輔助修正、不是主因。"
    "請務必評估【相對強弱】：若大盤/美股偏多、但本檔自身技術面偏空"
    "(跌破均線、MACD 翻負且柱狀擴大、KD 未交叉、外資賣超、量價背離、相對大盤弱)，"
    "這代表『相對弱勢』，是強烈看跌訊號——此時 direction 應取『跌』，不可因大盤漲就喊漲；"
    "反之大盤弱但個股逆勢強亦然。direction 必須誠實反映『個股自身淨多空』，"
    "不可每檔都偏多；多空若各半，寧可給『跌』或低信心，也不要一律順著大盤喊漲。"
    "判斷後請自我檢查：『我是不是只因為大盤/美股漲就給漲？』若是，重新檢視個股自身訊號。\n"
    "若提供【過去復盤回饋】，請一併參考：猜錯的避免重蹈，猜對的也檢視是實力還是運氣、"
    "幅度是否如預期、有無隱憂；但仍以當前技術面客觀判斷，不可因過去就一律反向或過度反應。\n"
    "bull_signals/bear_signals/reason 一律用自然中文，禁止出現程式變數/欄位名(如 hold_ma20、hold_support1、signal、direction、vol_ratio、macd_hist、ma20_slope5、dist_support1_pct、ma_align 等)，改用中文說法(站穩MA20、守住支撐1、進場訊號、方向、量比、MACD柱、MA20斜率、距支撐距離、均線排列)。"
)


_ETF_SYSTEM = (
    "你是台股 ETF 趨勢分析師。此標的是 ETF、不是個股——"
    "『不看個股籌碼(外資/投信/融資融券)、不套用個股的三批承接/停損與禁區規則』。\n"
    "請用【趨勢框架】預測此 ETF『今日相對昨收的方向(漲或跌)』：\n"
    "・主要驅動＝它追蹤的標的：{underlying}。半導體 ETF 看費半(SOX)、"
    "台股大盤型看加權指數；美股隔夜與大盤方向是主因。\n"
    "・輔以 ETF 自身均線趨勢(MA5/MA20/MA60、多頭/空頭排列、是否站上季線)、"
    "MACD、KD、量價。\n"
    "signal 沿用同組欄位但語意是趨勢：多頭順勢→進場、趨勢糾結/轉弱→觀望、"
    "空頭排列且跌破季線(明顯轉空)→避開(不接刀)；實際會被規則再夾一次。\n"
    "hold_ma20/hold_support1 就照技術面(站穩MA20否/此ETF無自訂支撐可填 false)。\n"
    "bull_signals/bear_signals/reason 一律自然中文，禁止出現程式變數/欄位名。"
)


def _chip_summary(foreign, margin):
    """把籌碼面(法人三大＋融資融券)整理成人話，餵給 LLM。無資料回提示字。"""
    def z(shares):
        return None if shares is None else round(shares / 1000)

    parts = []
    if foreign:
        fo = [f"外資{z(foreign.get('net'))}"]
        for label, key in (("投信", "trust_net"), ("自營商", "dealer_net"),
                           ("三大法人合計", "total_net")):
            if foreign.get(key) is not None:
                fo.append(f"{label}{z(foreign.get(key))}")
        parts.append("法人買賣超(張，正=買超)：" + "、".join(fo)
                     + f"；外資連續賣超{foreign.get('sold_streak')}日、"
                     f"是否止穩(停止倒貨)={foreign.get('stopped')}")
    if margin and margin.get("margin_chg") is not None:
        parts.append(
            f"融資餘額增減{z(margin.get('margin_chg'))}張"
            "(正=散戶加碼追高，回檔承接法視為過熱警訊)、"
            f"融券餘額{z(margin.get('short_bal'))}張")
    return "\n".join(parts) if parts else "（無籌碼資料）"


def make_prediction(indicators, stock_name, market=None, us_overnight=None,
                    llm=generate_json, code=None, foreign=None, batches=None,
                    lessons="", margin=None):
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
    etf = is_etf(code)
    underlying = ETF_UNDERLYING.get(str(code), "台股大盤（加權指數）")
    if etf:
        # ETF 走趨勢框架：不看個股籌碼，主要看追蹤標的與自身均線趨勢。
        foreign = margin = None
        system = _ETF_SYSTEM.format(underlying=underlying)
        user = (
            f"ETF：{stock_name}（追蹤標的：{underlying}）\n"
            f"技術指標(到昨日收盤為止)：\n{json.dumps(indicators, ensure_ascii=False)}\n"
            f"大盤(加權指數)昨收摘要：\n{json.dumps(market, ensure_ascii=False)}\n"
            f"美股隔夜四大指數漲跌(%)：\n{json.dumps(us_overnight, ensure_ascii=False)}"
        )
    else:
        system = _SYSTEM
        user = (
            f"股票：{stock_name}\n"
            f"技術指標(到昨日收盤為止)：\n{json.dumps(indicators, ensure_ascii=False)}\n"
            f"大盤(加權指數)昨收摘要：\n{json.dumps(market, ensure_ascii=False)}{rel_txt}\n"
            f"美股隔夜四大指數漲跌(%)：\n{json.dumps(us_overnight, ensure_ascii=False)}\n"
            f"籌碼面(法人三大＋融資融券)：\n{_chip_summary(foreign, margin)}"
        )
    if lessons:
        user += f"\n\n{lessons}"
    pred = llm(system, user, PREDICTION_SCHEMA)
    # 進場與否：規則為主、LLM 受限。把 LLM 的 signal 夾進紀律允許範圍。
    foreign_stopped = foreign.get("stopped") if foreign else None
    final_signal, rule_note = constrain_signal(pred, indicators, code,
                                               foreign_stopped)
    # 分批部位：ETF 不適用三批承接，略過；個股依已進批數調整。
    if batches is not None and not etf:
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
    pred["margin"] = margin
    pred["batches"] = None if etf else batches
    pred["is_etf"] = etf
    if etf:
        pred["underlying"] = underlying
    return pred


def _join_note(note, extra):
    return (note + "；" + extra) if note else extra


def format_prediction(stock_name, date, prediction, forecast=False):
    ind = prediction.get("indicators", {})
    ma20 = ind.get("ma20")
    ma20_txt = f"（{ma20:.1f}）" if isinstance(ma20, (int, float)) else ""

    def mark(ok):
        return "✅" if ok else "⚠️"

    conf = prediction.get("confidence")
    conf_txt = f"（信心{conf}）" if conf else ""
    head = "下一交易日開盤前預測" if forecast else "開盤前預測"
    date_line = (f"🗓 依 {date} 收盤試算　→　預測下一個交易日"
                 if forecast else f"🗓 {date}")
    etf = prediction.get("is_etf")
    sig = prediction["signal"]
    sig_txt = ETF_SIGNAL_LABEL.get(sig, sig) if etf else sig
    lines = [
        f"📈 {stock_name}｜{head}",
        date_line,
        "",
        f"🚦 訊號：{sig_txt}",
        f"🧭 方向：預期{prediction['direction']}{conf_txt}",
    ]
    if etf:
        lines.append(f"🎯 追蹤標的：{prediction.get('underlying', '台股大盤')}"
                     "（ETF 走趨勢框架，不看個股籌碼/三批）")
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
        lines += [f"🟢 {humanize(s)}" for s in bull]
        lines += [f"🔴 {humanize(s)}" for s in bear]
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
        extra = []
        for label, key in (("投信", "trust_net"), ("自營", "dealer_net"),
                           ("合計", "total_net")):
            v = fo.get(key)
            if v is not None:
                z = v / 1000.0
                extra.append(f"{label}{'買' if z >= 0 else '賣'}{abs(z):,.0f}")
        if extra:
            lines.append("　┗ " + "、".join(extra) + "（張）")
    mg = prediction.get("margin") or {}
    if mg.get("margin_chg") is not None:
        mc = mg["margin_chg"] / 1000.0
        seg = f"💳 融資：{'增' if mc >= 0 else '減'} {abs(mc):,.0f} 張"
        if mg.get("short_bal") is not None:
            seg += f"｜融券餘 {mg['short_bal'] / 1000.0:,.0f} 張"
        lines.append(seg)
    mk = prediction.get("market") or {}
    if mk.get("direction"):
        pct = mk.get("pct")
        pct_txt = f" {pct:+.2f}%" if isinstance(pct, (int, float)) else ""
        ma_txt = "站上" if mk.get("above_ma20") else "跌破"
        lines.append(f"🌐 大盤昨收：{mk['direction']}{pct_txt}（{ma_txt}MA20）")
    lines += [
        "",
        "──── 理由 ────",
        humanize(prediction["reason"]),
        "",
        f"🔗 看圖表：{_chart_link(stock_name)}",
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
    "【最重要：美股隔夜是已實現的隔夜方向，為主要領先指標】尤其費城半導體 SOX，"
    "對台股電子權值與台積電影響最大；費半大跌(如 -3% 以上)時，加權指數當日偏空機率很高，"
    "除非有明確且強力的反向證據，否則不可預測上漲。\n"
    "【台指期夜盤只是輔助確認】它可能過時或有雜訊。當台指期方向與美股隔夜明顯衝突時，"
    "一律以美股隔夜為準——切勿因為台指期小漲，就在美股(費半)大跌的夜晚預測大盤上漲。"
    "(系統偵測到台指期與美股嚴重背離時會直接不提供台指期，此時純以美股隔夜與技術面判斷。)\n"
    "技術面(均線/MACD/KD/RSI)為最後輔助。列出 drivers(引用具體數字)，再給 direction 與 confidence。"
    "美股與台指期方向一致時 confidence 可較高；資料缺漏或彼此矛盾則用『低』。\n"
    "若提供【過去教訓】，參考過去誤判避免重蹈，但仍以當前領先指標客觀判斷，不可因過去錯誤就一律反向。"
)


def _taifex_conflicts_us(taifex_night, us_overnight):
    """台指期與美股隔夜是否『嚴重背離』：美股(費半)大動作卻和台指期方向相反。
    是 → 台指期多半過時/雜訊，應丟棄不用（避免誤導，例如費半崩、台指期卻顯示漲）。"""
    if taifex_night is None:
        return False
    sox = (us_overnight or {}).get("費半SOX")
    if sox is None:
        return False
    return (sox <= -2 and taifex_night >= 0) or (sox >= 2 and taifex_night <= 0)


def make_market_prediction(index_indicators, us_overnight, market_data,
                           taifex_night=None, llm=generate_json, lessons="",
                           taifex_asof=None, us_asof=None, tw_last=None):
    # 台指期與美股嚴重背離（費半大跌卻顯示漲等）→ 台指期多半過時/雜訊，丟棄不用
    tf_conflict = _taifex_conflicts_us(taifex_night, us_overnight)
    if tf_conflict:
        taifex_night, taifex_asof = None, None
    us_txt = (f"{json.dumps(us_overnight, ensure_ascii=False)}（{us_asof} 收盤）"
              if us_asof else json.dumps(us_overnight, ensure_ascii=False))
    tf_txt = (f"{taifex_night}（{taifex_asof} 那一場）" if taifex_asof
              else json.dumps(taifex_night, ensure_ascii=False))
    if taifex_night is None:
        tf_txt = ("無（台指期與美股隔夜嚴重背離、多半過時，本次不納入；純以美股隔夜與技術面判斷）"
                  if tf_conflict else "無（抓不到或資料過時，本次不納入判斷）")
    # 已消化偵測：美股/台指期資料日期若『不晚於台股上一個交易日』，代表台股已反映過，
    # 屬舊訊息(例：週五美股放假→週一還是拿週四美股，而台股週五早已反映)，不可重複計入。
    digested = []
    if us_asof and tw_last and us_asof <= tw_last:
        digested.append(
            f"美股隔夜為 {us_asof} 收盤，不晚於台股上一個交易日 {tw_last}——台股上個交易日"
            "已反映過這波美股(可能因美股放假沒新盤)，屬『已消化的舊訊息』，"
            "不可再當今日新的利多/利空重複計入；今日方向請以台股自身技術面與籌碼為主。")
    if taifex_night is not None and taifex_asof and tw_last and taifex_asof <= tw_last:
        digested.append(
            f"台指期為 {taifex_asof} 那場，不晚於台股上一個交易日 {tw_last}，同屬已消化、勿重複計入。")
    user = (
        f"美股隔夜漲跌(%)：{us_txt}\n"
        f"台指期夜盤漲跌(%)：{tf_txt}\n"
        f"大盤昨收摘要：{json.dumps(market_data, ensure_ascii=False)}\n"
        f"大盤技術指標(到昨收)：{json.dumps(index_indicators, ensure_ascii=False)}"
    )
    if digested:
        user += "\n\n⚠️ 資料新鮮度提醒（重要）：\n" + "\n".join(f"・{d}" for d in digested)
    if lessons:
        user += f"\n\n{lessons}"
    out = llm(_MARKET_SYSTEM, user, MARKET_PRED_SCHEMA)
    out["us_overnight"] = us_overnight
    out["us_date"] = us_asof
    out["us_digested"] = bool(us_asof and tw_last and us_asof <= tw_last)
    out["taifex_night"] = taifex_night
    out["taifex_date"] = taifex_asof
    out["market_data"] = market_data
    return out


def format_market_prediction(date, pred, forecast=False):
    us = pred.get("us_overnight") or {}
    mk = pred.get("market_data") or {}
    conf = pred.get("confidence")
    conf_txt = f"（信心{conf}）" if conf else ""
    head = "下一交易日開盤前預測" if forecast else "開盤前預測"
    date_line = (f"🗓 依 {date} 收盤試算　→　預測下一個交易日"
                 if forecast else f"🗓 {date}")
    lines = [
        f"🌐 加權指數｜{head}",
        date_line,
        "",
        f"🔮 預測開盤方向：{pred.get('direction', '—')}{conf_txt}",
    ]
    if us:
        us_date = pred.get("us_date")
        lines.append("")
        lines.append(f"──── 美股隔夜{f'（{us_date} 收盤）' if us_date else ''} ────")
        for name, pct in us.items():
            # 台灣慣例：漲紅、跌綠
            lines.append(f"{'🔴' if pct >= 0 else '🟢'} {name}：{pct:+.2f}%")
        if pred.get("us_digested"):
            lines.append("⚠️ 此為放假前的美股（台股上一個交易日已反映過），非今日新訊息、"
                         "未當作新的多空重複計入。")
    tf = pred.get("taifex_night")
    tf_date = pred.get("taifex_date")
    if isinstance(tf, (int, float)):
        asof = f"（{tf_date}）" if tf_date else ""
        tf_txt = f"{tf:+.2f}%{asof}"
    else:
        tf_txt = "（無資料，本次未納入判斷）"
    lines.append(f"📊 台指期夜盤：{tf_txt}")
    if mk.get("direction"):
        pct = mk.get("pct")
        pt = f" {pct:+.2f}%" if isinstance(pct, (int, float)) else ""
        lines.append(f"🌐 大盤昨收：{mk['direction']}{pt}")
    drivers = pred.get("drivers") or []
    if drivers:
        lines += ["", "──── 依據 ────"] + [f"・{humanize(d)}" for d in drivers]
    lines += ["", "──── 理由 ────", humanize(pred.get("reason", ""))]
    lines += ["", f"🔗 看圖表：{DASHBOARD_URL}"]
    return "\n".join(lines)
