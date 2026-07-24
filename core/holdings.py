"""真實持股（我的持股）：記錄使用者實際持有的部位（股數＋成交均價），每個帳號各一份，
並用『回檔承接法』的同一組規則，每天給每一檔『出場／減碼／加倉／續抱』的操作建議。

跟 core/positions.py 的差別：positions 只記『已進第幾批(0~3)』；holdings 記真實的
股數與成交均價，用來算損益、停損金額、以及每日該怎麼操作。兩者互補：holding_action
會讀 positions 的批數來決定「還能不能加下一批」。

設計原則（本專案教訓換來的）：每個建議一定附上「為什麼＋關鍵價位＋外資日期＋位階」，
不做黑箱叫人買賣——使用者要看得到依據、能自己驗。
"""
import json
import os

from core import db
from core.rules import (
    exit_setup, entry_setup, etf_setup, is_etf, is_leveraged_etf, NEAR_PCT,
)
from core.levels import playbook_levels
from core.tz import now_tw

HOLDINGS_PATH = "holdings.json"
DEFAULT_OWNER = "admin"

STOP_NEAR_PCT = 3.0    # 收盤距季線停損 ≤ 此% 且仍在其上 → 接近停損預警
HIGH_POS_PCT = 70.0    # 位階 ≥ 此% 算中上緣（別再加、漲多可分批了結）


def _key(owner):
    return f"hold:{owner or DEFAULT_OWNER}"


def load_holdings(owner=DEFAULT_OWNER, path=HOLDINGS_PATH):
    """回 dict：{code: {"shares": int, "avg_cost": float, "updated": 'YYYY-MM-DD'}}。"""
    if db.db_enabled():
        return db.get_state(_key(owner), {}) or {}
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_holdings(holdings, owner=DEFAULT_OWNER, path=HOLDINGS_PATH):
    if db.db_enabled():
        db.set_state(_key(owner), holdings)
        return
    with open(path, "w", encoding="utf-8") as f:
        json.dump(holdings, f, ensure_ascii=False, indent=2)


def set_holding(code, shares, avg_cost, owner=DEFAULT_OWNER, path=HOLDINGS_PATH):
    """新增/更新一檔持股（股數＋成交均價）。回更新後的整份 holdings。"""
    holdings = load_holdings(owner, path)
    holdings[str(code)] = {
        "shares": float(shares),
        "avg_cost": float(avg_cost),
        "updated": now_tw().strftime("%Y-%m-%d"),
    }
    save_holdings(holdings, owner, path)
    return holdings


def remove_holding(code, owner=DEFAULT_OWNER, path=HOLDINGS_PATH):
    """移除一檔持股。回先前是否存在。"""
    holdings = load_holdings(owner, path)
    existed = str(code) in holdings
    holdings.pop(str(code), None)
    save_holdings(holdings, owner, path)
    return existed


def all_held_codes():
    """所有帳號持股的代號聯集，供每日 job『每檔只算一次、順手存日線』。"""
    codes = set()
    if db.db_enabled():
        for h in (db.get_states_by_prefix("hold:") or {}).values():
            if isinstance(h, dict):
                codes |= {str(c) for c in h.keys()}
    else:
        codes |= {str(c) for c in load_holdings().keys()}
    return codes


def position_pct(df, window=120):
    """位階：現價(收盤)落在近 window 個交易日收盤高低區間的百分位（0=最低、100=最高）。
    純用收盤，抓不到或區間為 0 回 None。"""
    try:
        closes = df["Close"].dropna().tail(window)
    except Exception:
        return None
    if len(closes) < 2:
        return None
    lo, hi = float(closes.min()), float(closes.max())
    cur = float(closes.iloc[-1])
    if hi <= lo:
        return None
    return (cur - lo) / (hi - lo) * 100


def holding_action(ind, code=None, foreign_stopped=None, batches=None,
                   avg_cost=None, pos_pct=None):
    """給『已持有部位』的每日操作建議。回 dict：
       {action, reason, alerts:list, levels:{support,resistance,stop}, pnl_pct, pos_pct}
       action ∈ {"出場", "減碼", "加倉", "續抱"}。

    出場/減碼/續抱沿用 exit_setup（季線停損/月線減碼/站穩續抱）；加倉沿用 entry_setup
    /etf_setup（真的四關到位＋外資停手才建議、且批數未滿、位階不過高）。另附停損接近預警、
    到壓力＋高位階分批了結、槓桿 ETF 勿長抱等提醒。"""
    close = ind.get("close")
    ma60 = ind.get("ma60")
    etf = is_etf(code)
    sup, res, stop = playbook_levels(ind)
    alerts = []

    pnl_pct = None
    if avg_cost and close is not None:
        pnl_pct = (close - avg_cost) / avg_cost * 100

    # 主建議：出場 / 減碼 / 續抱
    ex = exit_setup(ind, batches)
    action = ex["action"] or "續抱"
    reason = ex["reason"]

    # 加倉判斷：只有在「不必出場/減碼（＝續抱）」時才談是否加碼
    if action == "續抱":
        setup = etf_setup(ind, code) if etf else entry_setup(ind, code, foreign_stopped)
        if setup.get("ceiling") == "進場":
            if etf:
                if not is_leveraged_etf(code):
                    action = "加倉"
                    reason = setup["reason"] + "（ETF 順勢，可加碼或定期定額）"
            elif pos_pct is not None and pos_pct >= HIGH_POS_PCT:
                alerts.append(f"到支撐但位階偏高（約 {pos_pct:.0f}%）→ 不建議追加、續抱即可")
            elif batches is None or batches < 3:
                nb = (batches + 1) if isinstance(batches, int) else None
                batch_txt = f"（進第 {nb} 批）" if nb else ""
                action = "加倉"
                reason = setup["reason"] + f"，且外資停手/趨勢健康→可加下一批{batch_txt}"
            else:
                alerts.append("三批已滿 → 不再加碼、續抱即可")

    # 停損接近預警：仍在季線之上、但已很近
    if (close is not None and ma60 is not None
            and ma60 <= close <= ma60 * (1 + STOP_NEAR_PCT / 100)):
        gap = (close - ma60) / ma60 * 100
        alerts.append(f"⚠️ 接近季線停損 {ma60:.1f}（現價僅高 {gap:.1f}%）→ 收盤跌破就出場")

    # 到壓力 ＋ 位階偏高 → 可分批獲利了結
    if (res is not None and close is not None and pos_pct is not None
            and close >= res[1] * (1 - NEAR_PCT / 100) and pos_pct >= HIGH_POS_PCT
            and action in ("續抱", "減碼")):
        alerts.append(f"已達壓力 {res[0]} {res[1]:.1f} ＋位階偏高（約 {pos_pct:.0f}%）"
                      "→ 可分批獲利了結")

    # 槓桿/反向 ETF 長抱警告
    if is_leveraged_etf(code):
        alerts.append("⚠️ 槓桿/反向 ETF 有每日再平衡耗損，只宜短打、不宜長抱")

    return {"action": action, "reason": reason, "alerts": alerts,
            "levels": {"support": sup, "resistance": res, "stop": stop},
            "pnl_pct": pnl_pct, "pos_pct": pos_pct}
