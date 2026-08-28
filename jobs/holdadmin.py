"""持股帳本管理入口（workflow_dispatch 專用）。

使用者常在對話裡回報成交（「南亞科出清了」「00830 買了 83.2」），而真實持股
存在 Postgres，只有 Actions 摸得到——沒有這個入口，就只能請使用者再去 Telegram
打一次 /買了 /賣了，同一筆帳要報兩次。這支讓助手能直接把對話裡的成交同步進 DB。

環境變數 HOLD_OPS：逗號分隔的操作，語意與機器人指令一致（core/holdings 同一套）：
    sell:2451             出清（同 /賣了 2451）
    sell:2618:250         賣出 250 股，均價不變（同 /賣了 2618 250）
    buy:00830:100:83.2    買進 100 股 @83.2，與舊部位加權平均合併（同 /買了）
    set:2383:2:5800       直接覆寫成 2 股 @5800（對話對完帳後的校正用；機器人沒有
                          對應指令——它逐筆記帳不需要，這裡是「以對帳結果為準」）

寧可整批失敗也不要半批入帳：先全部解析、有錯就整批拒絕，再逐筆套用。
"""
import os

from core.holdings import (
    DEFAULT_OWNER, load_holdings, remove_holding, set_holding,
)


def parse_ops(raw):
    """把 HOLD_OPS 字串解析成 [(op, code, shares, price)]；shares/price 沒有就 None。
    任何一個 token 壞掉就 raise ValueError——帳本操作不做「跳過壞的繼續」。"""
    ops = []
    for token in (raw or "").replace("\n", ",").split(","):
        token = token.strip()
        if not token:
            continue
        parts = token.split(":")
        op = parts[0].strip().lower()
        if op == "sell" and len(parts) == 2:
            ops.append(("sell", parts[1].strip(), None, None))
        elif op == "sell" and len(parts) == 3:
            ops.append(("sell", parts[1].strip(), float(parts[2]), None))
        elif op in ("buy", "set") and len(parts) == 4:
            ops.append((op, parts[1].strip(), float(parts[2]), float(parts[3])))
        else:
            raise ValueError(f"看不懂的操作：{token!r}（格式見 jobs/holdadmin.py 開頭）")
    return ops


def apply_ops(ops, owner=DEFAULT_OWNER, path=None):
    """逐筆套用並回傳人類可讀的結果行。語意對齊 jobs/bot.py 的 /買了 /賣了：
    買進加權平均合併、部分賣出不動均價、賣超過持有＝出清。"""
    kw = {"owner": owner}
    if path:
        kw["path"] = path
    lines = []
    for op, code, shares, price in ops:
        held = (load_holdings(**kw) or {}).get(str(code)) or {}
        old_sh = float(held.get("shares", 0) or 0)
        old_cost = float(held.get("avg_cost", 0) or 0)
        if op == "sell":
            if not held:
                lines.append(f"⚠️ {code} 不在持股清單裡，略過")
            elif shares is None or shares >= old_sh:
                remove_holding(code, **kw)
                lines.append(f"🗑 {code} 出清（原 {old_sh:g} 股 @ {old_cost:g}）")
            else:
                set_holding(code, old_sh - shares, old_cost, **kw)
                lines.append(f"✅ {code} 賣出 {shares:g} 股，剩 {old_sh - shares:g} 股（均價不變）")
        elif op == "buy":
            total = old_sh + shares
            avg = (old_sh * old_cost + shares * price) / total
            set_holding(code, total, round(avg, 4), **kw)
            note = f"（原 {old_sh:g} 股 @ {old_cost:g}，加權合併）" if old_sh else ""
            lines.append(f"✅ {code} 買進 {shares:g} 股 @ {price:g}{note}")
        elif op == "set":
            set_holding(code, shares, price, **kw)
            note = f"（原 {old_sh:g} 股 @ {old_cost:g}）" if held else "（新增）"
            lines.append(f"✏️ {code} 覆寫為 {shares:g} 股 @ {price:g}{note}")
    return lines


def run():
    raw = os.environ.get("HOLD_OPS", "").strip()
    holdings = load_holdings()
    print("===== 目前持股（套用前）=====")
    for code, rec in sorted(holdings.items()):
        print(f"  {code}: {rec.get('shares'):g} 股 @ {rec.get('avg_cost')} ({rec.get('name', '')})")
    if not raw:
        print("HOLD_OPS 未設定——只列出目前持股，未做任何變更。")
        return
    ops = parse_ops(raw)          # 有錯就整批 raise，不半批入帳
    print("===== 套用操作 =====")
    for line in apply_ops(ops):
        print(f"  {line}")
    print("===== 套用後持股 =====")
    for code, rec in sorted(load_holdings().items()):
        print(f"  {code}: {rec.get('shares'):g} 股 @ {rec.get('avg_cost')} ({rec.get('name', '')})")


if __name__ == "__main__":
    run()
