"""進場紀律：規則為主、LLM 受限。

LLM 只負責預測方向(漲/跌)與信心、寫理由；『要不要進場』的 signal 由本檔
紀律硬性決定上限，避免 LLM 亂喊進場。紀律來源 = 既有 app.py 的支撐/MA20
邏輯：
  ・收盤在支撐1之上、且站穩 MA20  → 可進場
  ・破支撐1但仍在 MA20 之上(真空帶) → 等(最多觀望)
  ・跌破 MA20                      → 最多觀望
  ・跌破支撐3(重新評估)            → 避開
另加兩條一致性護欄：預測『跌』不進場、信心『低』不進場。

※ 門檻數字之後可依你自己的進場規則調整(見 ENTRY RULES 區塊)。
"""

# 訊號積極度排序：避開 < 觀望 < 進場
SIGNAL_RANK = {"避開": 0, "觀望": 1, "進場": 2}


def _rank(sig):
    return SIGNAL_RANK.get(sig, 1)


def signal_ceiling(ind):
    """依紀律算出今天最多可到的訊號等級（'進場'/'觀望'/'避開'）。"""
    close = ind.get("close")
    if close is None:
        return "觀望"
    ma20 = ind.get("ma20")
    above_ma20 = (ma20 is None) or (close >= ma20)
    d1 = ind.get("dist_support1_pct")   # >0 在支撐1之上
    d3 = ind.get("dist_support3_pct")   # >0 在支撐3之上

    # 有支撐位的個股：用支撐分層
    if d1 is not None:
        if d3 is not None and d3 < 0:
            return "避開"                       # 跌破支撐3 → 重新評估
        if d1 < 0:
            return "觀望"                       # 破支撐1(真空帶/更弱) → 等
        return "進場" if above_ma20 else "觀望"  # 在支撐1之上，看是否站穩 MA20
    # 無支撐位：只看 MA20
    return "進場" if above_ma20 else "觀望"


def constrain_signal(pred, ind):
    """把 LLM 的 signal 夾進紀律允許範圍。回 (final_signal, note|None)。

    note：有被紀律調整時的簡短說明（給人看），沒調整則 None。
    """
    llm_sig = pred.get("signal", "觀望")
    final = llm_sig
    reasons = []

    ceil = signal_ceiling(ind)
    if _rank(final) > _rank(ceil):
        final = ceil
        reasons.append(f"未站穩支撐/均線，上限{ceil}")
    if pred.get("direction") == "跌" and _rank(final) >= _rank("進場"):
        final = "觀望"
        reasons.append("方向看跌不進場")
    if pred.get("confidence") == "低" and _rank(final) >= _rank("進場"):
        final = "觀望"
        reasons.append("信心低不進場")

    note = "；".join(reasons) if (reasons and final != llm_sig) else None
    return final, note
