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


def entry_setup(ind, code=None):
    """判斷『回檔承接法』的進場資格。回 dict：
       {ceiling, at_batch, vol_ok, hold_ok, reason}。ceiling = 紀律允許的最高訊號。"""
    close = ind.get("close")
    ma20 = ind.get("ma20")
    prev = ind.get("prev_close")
    vr = ind.get("vol_ratio")
    d1 = ind.get("dist_support1_pct")   # 距支撐1 %（>0 在其上）
    d3 = ind.get("dist_support3_pct")   # 距支撐3 %
    d2 = _pct_to_ma20(close, ma20)      # 距支撐2(MA20) %

    vol_ok = vr is not None and vr < VOL_SHRINK            # 量縮
    hold_ok = prev is None or (close is not None and close >= prev)  # 止穩(收盤沒再破底)

    # 禁區
    if is_denied(code):
        return {"ceiling": "避開", "at_batch": None, "vol_ok": vol_ok,
                "hold_ok": hold_ok, "reason": f"禁區：{DENYLIST[str(code)]}"}

    # 停損：收盤跌破長期均線(支撐3)
    if d3 is not None and d3 < 0:
        return {"ceiling": "避開", "at_batch": None, "vol_ok": vol_ok,
                "hold_ok": hold_ok, "reason": "收盤跌破支撐3(長期均線)＝停損區，全數出場"}

    def near(dpct):
        return dpct is not None and -NEAR_PCT <= dpct <= NEAR_PCT

    at_batch = None
    if near(d1):
        at_batch = "支撐1(第一批)"
    elif near(d2):
        at_batch = "支撐2/MA20(第二批)"
    elif near(d3):
        at_batch = "支撐3(第三批)"

    # 情境一：到價 + 止穩 + 量縮 → 可進該批
    if at_batch and hold_ok and vol_ok:
        return {"ceiling": "進場", "at_batch": at_batch, "vol_ok": vol_ok,
                "hold_ok": hold_ok,
                "reason": f"回檔到{at_batch}、收盤止穩且量縮，符合往下接情境"}

    # 情境二：帶量「站回」上方均線並收盤站穩（剛站回 MA20，須貼近均線、非遠離；非空頭排列）
    just_reclaimed = d2 is not None and 0 <= d2 <= NEAR_PCT
    if (just_reclaimed and vr is not None and vr > VOL_EXPAND and hold_ok
            and ind.get("ma_align") != "空頭排列"):
        return {"ceiling": "進場", "at_batch": "站回均線", "vol_ok": True,
                "hold_ok": hold_ok, "reason": "帶量站回上方均線且收盤站穩，符合往上站情境"}

    # 其餘：真空帶/未到價/未止穩/放量殺 → 等
    if at_batch and not (hold_ok and vol_ok):
        miss = []
        if not hold_ok:
            miss.append("尚未收盤止穩")
        if not vol_ok:
            miss.append("量未縮")
        return {"ceiling": "觀望", "at_batch": at_batch, "vol_ok": vol_ok,
                "hold_ok": hold_ok,
                "reason": f"已到{at_batch}，但{'、'.join(miss)}，等收盤確認"}
    return {"ceiling": "觀望", "at_batch": None, "vol_ok": vol_ok,
            "hold_ok": hold_ok, "reason": "未到任一支撐(真空帶/位置偏高)，不是進場點"}


def signal_ceiling(ind, code=None):
    return entry_setup(ind, code)["ceiling"]


def constrain_signal(pred, ind, code=None):
    """把 LLM 的 signal 夾進紀律允許範圍。回 (final_signal, note|None)。"""
    llm_sig = pred.get("signal", "觀望")
    setup = entry_setup(ind, code)
    ceil = setup["ceiling"]
    final = llm_sig if _rank(llm_sig) <= _rank(ceil) else ceil
    note = None
    if final != llm_sig:
        note = f"{setup['reason']}（紀律上限：{ceil}）"
    elif final == "進場":
        # 合格進場：附帶「外資倒貨」這條本檔無法驗證、需人工確認
        note = f"{setup['reason']}；⚠️外資是否停止倒貨請自行確認，並用盤後定價(14:00–14:30)進場"
    return final, note
