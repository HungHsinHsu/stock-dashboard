"""今日劇本的關鍵價位推導（純均線數學、零重依賴）。

抽成獨立模組是因為 core.predict 會 import core.llm（Claude SDK），在只需要價位計算的
情境（如網頁的『我的持股』頁、core.holdings）不該被迫拉進 LLM 依賴、否則環境沒裝 SDK
就整個 import 失敗。這裡只吃 indicators dict、不碰網路/LLM。"""


def playbook_levels(ind):
    """從收盤＋均線推出關鍵價位。回 (支撐(name,val)|None, 壓力(name,val)|None, 季線停損|None)。
    下方最近的均線＝支撐、上方最近的均線＝壓力、季線＝停損。純用 MA、不需盤中資料。"""
    close = ind.get("close")
    ma60 = ind.get("ma60")
    if not isinstance(close, (int, float)):
        return None, None, None
    mas = [(n, v) for n, v in (("週線MA5", ind.get("ma5")),
                               ("月線MA20", ind.get("ma20")),
                               ("季線MA60", ma60))
           if isinstance(v, (int, float))]
    below = [(n, v) for n, v in mas if v <= close]
    above = [(n, v) for n, v in mas if v > close]
    sup = max(below, key=lambda x: x[1]) if below else None
    res = min(above, key=lambda x: x[1]) if above else None
    return sup, res, (ma60 if isinstance(ma60, (int, float)) else None)


def today_playbook(ind):
    """今日劇本：支撐／壓力／停損 ＋ IF-THEN 情境，可直接照做。回 list[str]（無收盤則空）。"""
    if not isinstance(ind.get("close"), (int, float)):
        return []
    sup, res, stop = playbook_levels(ind)
    lines = ["", "──── 📋 今日劇本 ────"]
    lines.append(f"🟩 支撐：{sup[0]} {sup[1]:.1f}" if sup
                 else "🟩 支撐：已跌破所有均線（弱勢，看前低）")
    lines.append(f"🟥 壓力：{res[0]} {res[1]:.1f}" if res
                 else "🟥 壓力：站上所有均線（多頭，上方無均線壓力）")
    if stop is not None:
        lines.append(f"🛑 停損：收盤跌破季線 {stop:.1f} 全出")
    if sup:
        stop_txt = f"；停損看季線 {stop:.1f}" if stop is not None else ""
        lines.append(f"↗ 收盤守住 {sup[1]:.1f}（{sup[0]}）→ 偏多／續抱")
        lines.append(f"↘ 收盤跌破 {sup[1]:.1f} → 轉弱、減碼{stop_txt}")
    return lines
