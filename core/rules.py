"""進場紀律：依《交易規則手冊 v3.0》的「回檔承接法」。規則為主、LLM 受限。

核心精神：該等就等、該動就動。LLM 只預測方向/信心/理由；『要不要進場』由
本檔紀律硬性決定，避免手癢亂喊進場。

回檔承接法（跟「追高」相反）：專挑回檔、有支撐、能照表操作的股票，在支撐位
分批往下接，越跌買越多。三段支撐 = 日線三條均線：
  ・支撐1 短期均線(橘)   回檔到此止穩 → 第一批 1/3
  ・支撐2 中期均線(MA20) 續跌到此     → 第二批 1/3
  ・支撐3 長期均線(紫紅) 再跌到此     → 第三批 1/3
  ・停損：收盤跌破長期均線(支撐3) → 全部認賠出場

進場條件（AND，缺一不進）：
  價格到位 + 收盤站穩 + 量縮 + 外資停止倒貨
  情境一(往下接)：跌到支撐並止穩(收盤站穩、最好收紅/長下影、量縮)
  情境二(往上站)：帶量站回上方均線並收盤站穩

鐵律：① 永遠看收盤、不看盤中 ② 均線每天移動，當天重新確認 ③ 用盤後定價
(14:00–14:30) 進場。

※ 本檔只能驗證『價格到位/收盤止穩/量縮』；【外資是否停止倒貨】無資料來源，
  目前無法自動檢核（見 note 提醒）。門檻常數可調。
"""

# 禁區標的：動能股/槓桿，不屬於回檔承接法的牌局 → 一律避開
DENYLIST = {
    "3481": "群創（漲停追高禁區、動能股）",
    "00631L": "正2（槓桿耗損、不碰）",
}

# ETF 追蹤標的（判方向時的主要驅動；沒列到的預設看台股大盤）
ETF_UNDERLYING = {
    "0050": "台股大盤（加權指數）",
    "006208": "台股大盤（加權指數）",
    "0056": "台股大盤（高股息）",
    "00878": "台股大盤（高股息）",
    "00830": "美國費城半導體指數（SOX 費半）",
    "00891": "美國費城半導體指數（SOX 費半）",
    "00881": "台股半導體類股",
}

# ETF 走趨勢框架，訊號沿用同一組欄位但改成趨勢語意（顯示時翻成人話）
ETF_SIGNAL_LABEL = {"進場": "順勢偏多", "觀望": "趨勢轉弱觀望", "避開": "明顯轉空避開"}


def is_etf(code):
    """台股 ETF 代號以 00 開頭（0050、00830、006208…）；個股（2330…）為 False。"""
    return str(code or "").strip().startswith("00")


def is_leveraged_etf(code):
    """槓桿/反向 ETF（代號結尾 L 或 R，如 00631L 正2、00632R 反1）。"""
    c = str(code or "").strip().upper()
    return c.startswith("00") and (c.endswith("L") or c.endswith("R"))


def etf_setup(ind, code=None):
    """ETF 用『趨勢框架』決定訊號上限（不套個股籌碼/禁區/三批停損）：
    多頭順勢=進場、趨勢糾結/轉弱=觀望、空頭排列且跌破季線=避開（不接刀）。
    回傳與 entry_setup 相同 key 的 dict，供 constrain_signal 共用。"""
    if is_leveraged_etf(code):
        return {"ceiling": "避開", "at_batch": None, "vol_ok": None,
                "hold_ok": None, "reason": "槓桿/反向 ETF，不做波段承接，避開"}
    close, ma20, ma60 = ind.get("close"), ind.get("ma20"), ind.get("ma60")
    align = ind.get("ma_align")
    below60 = ma60 is not None and close is not None and close < ma60
    above60 = ma60 is not None and close is not None and close >= ma60
    above20 = ma20 is not None and close is not None and close >= ma20
    if align == "空頭排列" and below60:
        return {"ceiling": "避開", "at_batch": None, "vol_ok": None, "hold_ok": None,
                "reason": "空頭排列且跌破季線(MA60)，趨勢明顯轉空→不順勢承接、避免接刀"}
    if align == "多頭排列" or (above60 and above20):
        return {"ceiling": "進場", "at_batch": None, "vol_ok": None, "hold_ok": None,
                "reason": "站上季線/多頭排列，順勢偏多（可順勢或定期定額）"}
    return {"ceiling": "觀望", "at_batch": None, "vol_ok": None, "hold_ok": None,
            "reason": "趨勢糾結/轉弱，等站回均線或回穩再順勢"}

NEAR_PCT = 2.0     # 收盤距某支撐 ±2% 內算「到價」
VOL_SHRINK = 1.0   # 量比 < 此值算「量縮」（vs 20 日均量）
VOL_EXPAND = 1.2   # 量比 > 此值算「帶量」

SIGNAL_RANK = {"避開": 0, "觀望": 1, "進場": 2}


def _rank(sig):
    return SIGNAL_RANK.get(sig, 1)


def is_denied(code):
    return str(code) in DENYLIST if code is not None else False


def _pct_to_ma20(close, ma20):
    if close is None or not ma20:
        return None
    return (close - ma20) / ma20 * 100


def entry_setup(ind, code=None, foreign_stopped=None):
    """判斷『回檔承接法』的進場資格（中間版：分級進場，不必四關全過）。
    回 dict：{ceiling, at_batch, vol_ok, hold_ok, reason, tier}。

    紀律：
    - ① 價格到位（到支撐 ±2%）＝必要門檻，沒到不談。
    - ② 收盤站穩（收盤沒再破底）＝必要（防接刀），還在破底一律觀望。
    - ④ 外資『明確賣超』＝否決（不跟法人對做），夾回觀望。
    - ①②過、外資非明確賣超後，用 ③量縮＋④外資停手『計分』定強度：
      兩項全過＝標準進場（強）；過一項＝標準；都沒過＝半批試單（小量）。
    foreign_stopped：True=已停手、False=仍賣超、None=無資料（中性、不加分也不否決）。
    """
    close = ind.get("close")
    ma20 = ind.get("ma20")
    prev = ind.get("prev_close")
    vr = ind.get("vol_ratio")
    d1 = ind.get("dist_support1_pct")   # 距支撐1 %（>0 在其上）
    d3 = ind.get("dist_support3_pct")   # 距支撐3 %
    d2 = _pct_to_ma20(close, ma20)      # 距支撐2(MA20) %

    vol_ok = vr is not None and vr < VOL_SHRINK            # 量縮
    hold_ok = prev is None or (close is not None and close >= prev)  # 止穩(收盤沒再破底)

    def result(ceiling, at_batch, reason, tier=None):
        return {"ceiling": ceiling, "at_batch": at_batch, "vol_ok": vol_ok,
                "hold_ok": hold_ok, "reason": reason, "tier": tier or ceiling}

    # 禁區
    if is_denied(code):
        return result("避開", None, f"禁區：{DENYLIST[str(code)]}", "避開：禁區")

    # 停損：收盤跌破長期均線(支撐3)
    if d3 is not None and d3 < 0:
        return result("避開", None, "收盤跌破支撐3(長期均線)＝停損區，全數出場",
                      "避開：跌破支撐3停損")

    def near(dpct):
        return dpct is not None and -NEAR_PCT <= dpct <= NEAR_PCT

    at_batch = None
    if near(d1):
        at_batch = "支撐1(第一批)"
    elif near(d2):
        at_batch = "支撐2/MA20(第二批)"
    elif near(d3):
        at_batch = "支撐3(第三批)"

    # 情境二：帶量站回上方均線（優先於分批承接判定；帶量而非量縮）
    just_reclaimed = d2 is not None and 0 <= d2 <= NEAR_PCT
    if (just_reclaimed and vr is not None and vr > VOL_EXPAND and hold_ok
            and ind.get("ma_align") != "空頭排列"):
        if foreign_stopped is False:
            return result("觀望", "站回均線",
                          "帶量站回上方均線，但外資仍在賣超→等外資停手", "觀望：外資仍賣超")
        return result("進場", "站回均線",
                      "帶量站回上方均線且收盤站穩，符合往上站情境", "進場：站回均線")

    # ① 價格到位（必要）
    if not at_batch:
        return result("觀望", None, "未到任一支撐(真空帶/位置偏高)，不是進場點",
                      "觀望：未到支撐")
    # ② 收盤站穩（必要，防接刀）
    if not hold_ok:
        return result("觀望", at_batch,
                      f"已到{at_batch}，但收盤仍在破底、未站穩→先等站穩（不接刀）",
                      "觀望：未站穩")
    # ④ 外資『明確賣超』→ 否決（不跟法人對做）
    if foreign_stopped is False:
        return result("觀望", at_batch,
                      f"已到{at_batch}、收盤也站穩，但外資仍在賣超→等外資停手再接",
                      "觀望：外資仍賣超")

    # ①② 已過、外資非明確賣超 → 用 ③量縮＋④外資停手 計分定強度
    base = f"回檔到{at_batch}、收盤站穩"
    score = (1 if vol_ok else 0) + (1 if foreign_stopped is True else 0)
    if score == 2:
        return result("進場", at_batch,
                      base + "、量縮，且外資已停止倒貨→標準進場（訊號較強）",
                      "進場：標準（量縮＋外資停手）")
    if score == 1:
        which = "量縮" if vol_ok else "外資已停止倒貨"
        return result("進場", at_batch,
                      base + f"、{which}（另一項未確認）→進場（標準偏中）",
                      f"進場：標準（{which}）")
    return result("進場", at_batch,
                  base + "，但量未縮、外資未確認→半批試單（小量，等量縮/外資停手再加碼）",
                  "進場：半批試單（確認不足）")


def signal_ceiling(ind, code=None, foreign_stopped=None):
    return entry_setup(ind, code, foreign_stopped)["ceiling"]


def constrain_signal(pred, ind, code=None, foreign_stopped=None):
    """把 LLM 的 signal 夾進紀律允許範圍。回 (final_signal, note|None)。
    ETF 走趨勢框架(etf_setup)，個股走回檔承接法(entry_setup)。"""
    llm_sig = pred.get("signal", "觀望")
    etf = is_etf(code)
    setup = etf_setup(ind, code) if etf else entry_setup(ind, code, foreign_stopped)
    ceil = setup["ceiling"]
    final = llm_sig if _rank(llm_sig) <= _rank(ceil) else ceil
    note = None
    if final != llm_sig:
        note = f"{setup['reason']}（紀律上限：{ceil}）"
    elif final == "進場":
        if etf:                                   # ETF 順勢，不談外資/三批
            note = setup["reason"]
        elif foreign_stopped is None:
            # 外資資料缺 → 放行但提醒人工確認
            note = f"{setup['reason']}；⚠️外資是否停止倒貨請自行確認，並用盤後定價(14:00–14:30)進場"
        else:
            note = f"{setup['reason']}；用盤後定價(14:00–14:30)進場"
    return final, note
