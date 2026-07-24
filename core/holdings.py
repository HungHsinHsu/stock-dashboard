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


def set_holding(code, shares, avg_cost, name=None, mode=None,
                owner=DEFAULT_OWNER, path=HOLDINGS_PATH):
    """新增/更新一檔持股（股數＋成交均價＋名稱＋操作模式）。回更新後的整份 holdings。
    name/mode 給了就存；沒給則沿用舊紀錄既有值（避免重存把名字/模式洗掉）。
    mode ∈ {"長期", "波段"}，預設沿用舊值、再沒有就 "波段"。"""
    holdings = load_holdings(owner, path)
    old = holdings.get(str(code)) or {}
    rec = {
        "shares": float(shares),
        "avg_cost": float(avg_cost),
        "updated": now_tw().strftime("%Y-%m-%d"),
    }
    nm = name or old.get("name")
    if nm:
        rec["name"] = nm
    # 只有『明確指定 長期/波段』時才存 mode key；否則不存 → 交給 effective_mode 聰明預設
    # （避免把預設值寫死成 mode，害之後改不了聰明預設）。舊紀錄有明確 mode 則沿用。
    m = mode if mode in ("長期", "波段") else old.get("mode")
    if m in ("長期", "波段"):
        rec["mode"] = m
    holdings[str(code)] = rec
    save_holdings(holdings, owner, path)
    return holdings


def remove_holding(code, owner=DEFAULT_OWNER, path=HOLDINGS_PATH):
    """移除一檔持股。回先前是否存在。"""
    holdings = load_holdings(owner, path)
    existed = str(code) in holdings
    holdings.pop(str(code), None)
    save_holdings(holdings, owner, path)
    return existed


def effective_mode(code, rec=None):
    """該持股的操作模式：使用者明確設定優先；沒設定時給聰明預設——
    ETF（00 開頭原型）預設『長期』（定期定額），個股預設『波段』（回檔承接法）；
    槓桿/反向 ETF 會耗損、不預設長期。"""
    m = (rec or {}).get("mode")
    if m in ("長期", "波段"):
        return m
    if is_leveraged_etf(code):
        return "波段"
    return "長期" if is_etf(code) else "波段"


def migrate_etf_default_long():
    """一次性清理：把『非槓桿 ETF 被誤存成波段』的 mode 拿掉，交回聰明預設(→長期)。
    （早期版本的頁面切換鈕曾因 Streamlit widget 狀態把 ETF 誤寫成波段。）
    以 app_state 旗標確保只跑一次；無 DB 則 no-op。"""
    if not db.db_enabled() or db.get_state("holdmode_migrate_v1"):
        return
    for key, h in (db.get_states_by_prefix("hold:") or {}).items():
        if not isinstance(h, dict):
            continue
        changed = False
        for code, rec in list(h.items()):
            if (isinstance(rec, dict) and rec.get("mode") == "波段"
                    and is_etf(code) and not is_leveraged_etf(code)):
                rec.pop("mode", None)
                changed = True
        if changed:
            db.set_state(key, h)
    db.set_state("holdmode_migrate_v1", True)


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
                   avg_cost=None, pos_pct=None, mode="波段"):
    """給『已持有部位』的每日操作建議。回 dict：
       {action, reason, alerts:list, levels:{support,resistance,stop}, pnl_pct, pos_pct}
       action ∈ {"出場", "減碼", "加倉", "續抱"}。

    mode 決定用哪套紀律：
      ・"長期"（定期定額/長投）：不套個股季線停損，跌破季線＝短期轉弱／逢低分批加碼參考，
        絕不叫『全數出場』；動作只在 續抱／加倉(逢低) 之間。
      ・"波段"（預設）：走回檔承接法——出場(跌破季線)／減碼(跌破月線)／加倉(四關到位＋外資
        停手＋批數未滿＋位階不過高)／續抱，附停損接近預警、到壓力分批了結、槓桿ETF勿長抱。"""
    close = ind.get("close")
    ma20 = ind.get("ma20")
    ma60 = ind.get("ma60")
    etf = is_etf(code)
    lev = is_leveraged_etf(code)
    sup, res, stop = playbook_levels(ind)
    alerts = []

    pnl_pct = None
    if avg_cost and close is not None:
        pnl_pct = (close - avg_cost) / avg_cost * 100

    # ── 長期（定期定額/長投）：逢低加碼、順勢續抱，不套個股季線停損、不叫全出 ──
    if mode == "長期":
        if close is not None and ma20 is not None and close <= ma20:
            action = "加倉"
            reason = "回檔到月線之下＝相對便宜，長期定額的逢低分批加碼區（不套個股停損）"
        else:
            action = "續抱"
            reason = "站在月線之上、順勢——長期續抱；等回檔到均線再分批加碼"
        if close is not None and ma60 is not None and close < ma60:
            alerts.append(f"跌破季線 {ma60:.1f}（短期偏弱）；長期定額不當停損，反而是逢低分批加碼參考區")
        if lev:
            alerts.append("⚠️ 槓桿/反向 ETF 有每日再平衡耗損，不適合長期抱——建議改短打或換原型")
        return {"action": action, "reason": reason, "alerts": alerts,
                "levels": {"support": sup, "resistance": res, "stop": None},
                "pnl_pct": pnl_pct, "pos_pct": pos_pct}

    # ── 波段：回檔承接法（含硬停損）：出場 / 減碼 / 續抱 ──
    ex = exit_setup(ind, batches)
    action = ex["action"] or "續抱"
    reason = ex["reason"]

    # 加倉判斷：只有在「不必出場/減碼（＝續抱）」時才談是否加碼
    if action == "續抱":
        setup = etf_setup(ind, code) if etf else entry_setup(ind, code, foreign_stopped)
        if setup.get("ceiling") == "進場":
            if etf:
                if not lev:
                    action = "加倉"
                    reason = setup["reason"] + "（ETF 順勢，可加碼）"
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
    if lev:
        alerts.append("⚠️ 槓桿/反向 ETF 有每日再平衡耗損，只宜短打、不宜長抱")

    return {"action": action, "reason": reason, "alerts": alerts,
            "levels": {"support": sup, "resistance": res, "stop": stop},
            "pnl_pct": pnl_pct, "pos_pct": pos_pct}
