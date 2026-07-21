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

鐵律：① 永遠看收盤、不看盤中 ② 均線每天移動，當天重新確認 ③ 訊號依收盤算、
資料(外資等)收盤後才齊，故當『隔日承接清單』——隔日在該支撐價附近確認站穩再接
(自動系統趕不上當日盤後定價 14:00–14:30，要用盤後定價就用隔日那班)。

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

# 市值型大盤 ETF＝追蹤加權指數本身者（0050、006208…）。這類 ETF≈大盤，
# 預測方向理應等同大盤；否則會出現「大盤跌、0050 漲」的自我矛盾。
MARKET_INDEX_UNDERLYING = "台股大盤（加權指數）"

# ETF 走趨勢框架，訊號沿用同一組欄位但改成趨勢語意（顯示時翻成人話）
ETF_SIGNAL_LABEL = {"進場": "順勢偏多", "觀望": "趨勢轉弱觀望", "避開": "明顯轉空避開"}


def is_etf(code):
    """台股 ETF 代號以 00 開頭（0050、00830、006208…）；個股（2330…）為 False。"""
    return str(code or "").strip().startswith("00")


def is_leveraged_etf(code):
    """槓桿/反向 ETF（代號結尾 L 或 R，如 00631L 正2、00632R 反1）。"""
    c = str(code or "").strip().upper()
    return c.startswith("00") and (c.endswith("L") or c.endswith("R"))


def is_market_index_etf(code):
    """是否為『追蹤加權指數本身』的市值型大盤 ETF（0050、006208…），方向應對齊大盤。
    高股息(0056/00878)、費半(00830/00891)、半導體(00881)追蹤別的指數，不算。"""
    return ETF_UNDERLYING.get(str(code or "").strip()) == MARKET_INDEX_UNDERLYING


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
# 當日漲幅 ≥ 此值＝大漲/漲停：那根是「噴出」不是「回檔」，即使貼近均線也不承接（避免追高）。
# 承接法的關卡只看『靜態位置』（離均線多近、量比<1、收≥昨收），漲停會用錯誤理由踩中全部
# （短均線被拉上來貼價→「到支撐」、漲停惜售低量→「量縮」、+10%→「止穩」），故補這道『當日
# 動態』守門，把噴出的那根排除在回檔承接之外。
SURGE_PCT = 4.5

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
    """判斷『回檔承接法』的進場資格。回 dict：
       {ceiling, at_batch, vol_ok, hold_ok, reason}。ceiling = 紀律允許的最高訊號。

    foreign_stopped：外資是否停止倒貨(進場 AND 第四條)。True=已停手、False=仍賣超、
    None=無資料。為 False 時即使技術面到位也夾回觀望；None 時放行但於 note 提醒人工確認。
    """
    close = ind.get("close")
    ma20 = ind.get("ma20")
    prev = ind.get("prev_close")
    vr = ind.get("vol_ratio")
    day_chg = ((close - prev) / prev * 100) if (close is not None and prev) else None
    d1 = ind.get("dist_support1_pct")   # 距支撐1 %（>0 在其上）
    d3 = ind.get("dist_support3_pct")   # 距支撐3 %
    d2 = _pct_to_ma20(close, ma20)      # 距支撐2(MA20) %

    vol_ok = vr is not None and vr < VOL_SHRINK            # 量縮
    hold_ok = prev is None or (close is not None and close >= prev)  # 止穩(收盤沒再破底)

    def result(ceiling, at_batch, reason, tech_ready=False):
        # tech_ready＝技術面四關到位的『健康回檔』(到支撐+止穩+量縮+趨勢健康)，只差外資那關。
        # 保守版(右側)要外資也停手(進場)才接；激進版(左側)只要 tech_ready 就當天接、不等外資。
        return {"ceiling": ceiling, "at_batch": at_batch, "vol_ok": vol_ok,
                "hold_ok": hold_ok, "reason": reason, "tech_ready": tech_ready}

    # 禁區
    if is_denied(code):
        return result("避開", None, f"禁區：{DENYLIST[str(code)]}")

    # 停損：收盤跌破長期均線(支撐3)
    if d3 is not None and d3 < 0:
        return result("避開", None, "收盤跌破支撐3(長期均線)＝停損區，全數出場")

    def near(dpct):
        return dpct is not None and -NEAR_PCT <= dpct <= NEAR_PCT

    at_batch = None
    if near(d1):
        at_batch = "支撐1(第一批)"
    elif near(d2):
        at_batch = "支撐2/MA20(第二批)"
    elif near(d3):
        at_batch = "支撐3(第三批)"

    # 技術面是否符合進場情境
    qualified, base_reason = False, ""
    if at_batch and hold_ok and vol_ok:                  # 情境一：往下接
        qualified = True
        base_reason = f"回檔到{at_batch}、收盤止穩且量縮，符合往下接情境"
    else:
        just_reclaimed = d2 is not None and 0 <= d2 <= NEAR_PCT
        if (just_reclaimed and vr is not None and vr > VOL_EXPAND and hold_ok
                and ind.get("ma_align") != "空頭排列"):  # 情境二：往上站
            qualified, at_batch = True, "站回均線"
            base_reason = "帶量站回上方均線且收盤站穩，符合往上站情境"

    if qualified:
        # 當日動態守門：那根若是大漲/漲停（≥SURGE_PCT），就不是「回檔」而是「噴出、追高」——
        # 貼近均線只是短均線被拉上來，非真回檔。優先於外資關，直接夾成觀望（避免叫人追漲停）。
        if day_chg is not None and day_chg >= SURGE_PCT:
            return result("觀望", at_batch,
                          f"當日大漲/漲停(+{day_chg:.1f}%)＝噴出非回檔；貼近{at_batch}只是"
                          "短均線被拉上來，非真回檔→觀望，不追高，等回穩再承接")
        # 趨勢健康關：進場只接『上升趨勢中的健康回檔』。中期均線(月線 MA20)還在往上＝趨勢沒壞；
        # 走平或下彎＝高檔摔下來(像仁寶、晶豪科那種噴上去又回落)，短線就算到價站穩量縮也不是好承接點 → 降觀望。
        slope = ind.get("ma20_slope5")
        if slope is not None and slope <= 0:
            return result("觀望", at_batch,
                          f"已到{at_batch}、站穩量縮，但中期均線(月線MA20)走平/下彎、"
                          "趨勢轉弱(高檔回落)，非上升趨勢中的健康回檔→保守觀望，等月線重新翻揚再看")
        # 到這裡＝技術面『健康回檔』四關到位（tech_ready），只差外資那關 → 激進版可接。
        # 進場 AND 第四條：外資停止倒貨。缺一或無法確認外資，保守版一律 → 觀望，不給進場。
        if foreign_stopped is True:
            return result("進場", at_batch, base_reason + "，且外資已停止倒貨", tech_ready=True)
        if foreign_stopped is False:
            return result("觀望", at_batch, base_reason + "，但外資仍在賣超→等外資停手",
                          tech_ready=True)
        # None＝外資資料缺漏/無法確認 → 不當作進場（資料闕漏不放行）
        return result("觀望", at_batch,
                      base_reason + "，但外資買賣超無法確認→保守觀望，確認外資已停手再進",
                      tech_ready=True)

    # 其餘：真空帶/未到價/未止穩/放量殺 → 等
    # 註：此清單為收盤後快照(當日一次)，「站穩」指的是隔日承接——隔日回到支撐、
    # 看隔日那根收盤站不站得穩，不是叫你當天再等一個收盤(當天已收)。
    if at_batch and not (hold_ok and vol_ok):
        miss = []
        if not hold_ok:
            miss.append("收盤未站穩")
        if not vol_ok:
            miss.append("量未縮")
        return result("觀望", at_batch,
                      f"已到{at_batch}，但今日{'、'.join(miss)}；隔日回到支撐、"
                      "收盤站穩再分批接（此清單當日一次、盤中不即時更新）")
    return result("觀望", None, "未到任一支撐(真空帶/位置偏高)，不是進場點")


def exit_setup(ind, batches=None):
    """已持有部位（回檔承接法）的出場紀律。回 {action, reason}。
    action ∈ {"出場", "減碼", "續抱", None}。與進場對稱、沿用同一組均線做『移動停利』：

      ・收盤跌破季線 MA60（支撐3）→ 出場：趨勢確認轉壞，全數認賠/獲利了結。
        （MA60 隨股價上漲墊高＝天然移動停利，漲越多、出場線越高、鎖越多獲利。）
      ・收盤跌破月線 MA20（但仍在季線之上）→ 減碼：短線轉弱第一警訊，先出一半、
        剩餘移到季線停損。但『建倉未滿三批(batches<3)』時月線 MA20 是加碼支撐(支撐2)、
        不是減碼點，故此情況回『續抱』（越跌越買、不自打嘴巴）。
      ・站穩月線之上 → 續抱：趨勢未壞，讓獲利奔跑。

    batches=None（例：網頁把追蹤清單全當持有、不知實際批數）→ 跌破月線一律當『減碼』警訊。
    """
    close = ind.get("close")
    ma20 = ind.get("ma20")
    ma60 = ind.get("ma60")
    if close is None:
        return {"action": None, "reason": "無現價資料，無法判定出場"}
    if ma60 is not None and close < ma60:
        return {"action": "出場",
                "reason": "收盤跌破季線(MA60)＝趨勢確認轉壞，依紀律全數出場"
                          "（移動停利/停損：季線隨股價墊高，漲越多鎖越多）"}
    if ma20 is not None and close < ma20:
        if batches is not None and batches < 3:
            return {"action": "續抱",
                    "reason": "跌破月線(MA20)但建倉未滿三批：月線是加碼支撐(支撐2)、"
                              "非減碼點；守住季線即可"}
        return {"action": "減碼",
                "reason": "收盤跌破月線(MA20)＝短線轉弱，先減碼約一半、"
                          "剩餘移到季線(MA60)停損"}
    return {"action": "續抱",
            "reason": "站穩月線(MA20)之上、趨勢未壞→續抱"
                      "（跌破月線減碼、跌破季線全數出場）"}


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
        else:
            # 進場代表外資已確認停手（None 已被夾成觀望）。訊號依收盤算、資料收盤後才齊，
            # 當日盤後定價通常趕不上 → 當隔日承接清單，隔日確認站穩再接。
            note = f"{setup['reason']}；當隔日承接清單，隔日確認站穩再接（當日盤後定價多半已過）"
    return final, note
