"""我的持股・每日操作：把使用者實際持有的部位（股數＋成交均價）逐檔套『回檔承接法』，
每天給『出場／減碼／加倉／續抱』的建議（含停損接近預警、分批了結、位階），存 DB 供網頁讀、
並推一則精簡總表到 Telegram。

資料全走 GitHub Actions 乾淨 IP 抓（收盤/盤前資料，不被 TWSE 擋）；每檔只抓一次日線與外資，
再對每個帳號用各自的成交均價/批數算建議。跟 jobs/watch 一樣順手把日線存 bars:<code> 供網頁讀。
"""
from core.data import fetch_daily, fetch_foreign_flow, resolve_stocks
from core.indicators import compute_indicators
from core.holdings import (
    load_holdings, all_held_codes, holding_action, position_pct,
)
from core.positions import get_batches
from core.barstore import dump_bars
from core.config import DASHBOARD_URL
from core.tz import now_tw
from core import db
import core.telegram as tg

STATE_PREFIX = "hold_scan:"        # 每帳號一份：hold_scan:<owner>
DEFAULT_OWNER = "admin"

_ACT_EMOJI = {"出場": "🛑", "減碼": "✂️", "加倉": "➕", "續抱": "✋"}


def _name_lookup():
    """代號→名稱：彙整各帳號追蹤清單（wl:*）的名稱；查無回代號。純讀 DB、不打網路。"""
    names = {}
    try:
        for wl in (db.get_states_by_prefix("wl:") or {}).values():
            if isinstance(wl, dict):
                for c, info in wl.items():
                    nm = (info or {}).get("name")
                    if nm:
                        names[str(c)] = nm
    except Exception:
        pass
    return names


def _lvl_line(levels):
    parts = []
    sup = levels.get("support")
    res = levels.get("resistance")
    stop = levels.get("stop")
    if sup:
        parts.append(f"🟩支 {sup[0]} {sup[1]:.1f}")
    if res:
        parts.append(f"🟥壓 {res[0]} {res[1]:.1f}")
    if stop is not None:
        parts.append(f"🛑損 季線 {stop:.1f}")
    return "　".join(parts)


def _item_lines(it):
    emoji = _ACT_EMOJI.get(it["action"], "・")
    tag = f"〔{it.get('mode', '波段')}〕"
    lines = [f"{emoji} {it['name']} ({it['code']}){tag}｜{it['action']}"]
    pnl = it.get("pnl_pct")
    if pnl is not None and it.get("avg_cost") and it.get("close") is not None:
        sign = "+" if pnl >= 0 else ""
        lines.append(f"　損益 {sign}{pnl:.1f}%（均價 {it['avg_cost']:.2f} → 現 {it['close']:.2f}）")
    lvl = _lvl_line(it.get("levels") or {})
    if lvl:
        lines.append("　" + lvl)
    if it.get("pos_pct") is not None:
        lines.append(f"　位階約 {it['pos_pct']:.0f}%")
    lines.append(f"　理由：{it['reason']}")
    for a in it.get("alerts") or []:
        lines.append(f"　{a}")
    fd = it.get("foreign_date")
    if fd:
        lines.append(f"　（外資資料日：{fd}）")
    return lines


def _digest(date, items):
    lines = [f"💼 我的持股・今日操作（{date}）", ""]
    if not items:
        lines.append("（還沒設定持股——用網頁『我的持股』輸入代號/股數/成交均價）")
    for it in items:
        lines += _item_lines(it)
        lines.append("")
    lines += [
        "🕒 收盤/盤前快照、盤中不更新；建議是『規則＋價位』的決策輔助，方向不保證。",
        "🛑 停損一律看『收盤』跌破季線，不看盤中觸價。",
        f"🔗 {DASHBOARD_URL}",
    ]
    return "\n".join(lines)


def _compute_for_owner(owner, code_data, names):
    """用某帳號的成交均價/批數，對其持股逐檔算操作建議。回 list[item]。"""
    holdings = load_holdings(owner)
    items = []
    for code, rec in holdings.items():
        code = str(code)
        cd = code_data.get(code)
        avg_cost = (rec or {}).get("avg_cost")
        shares = (rec or {}).get("shares")
        nm = (rec or {}).get("name") or names.get(code, code)
        mode = (rec or {}).get("mode") or "波段"
        if not cd or cd.get("ind") is None:
            items.append({
                "code": code, "name": nm, "mode": mode,
                "shares": shares, "avg_cost": avg_cost, "close": None,
                "action": "—", "reason": "抓不到日線資料（可能是上櫃股或暫時限流），無法試算",
                "alerts": [], "levels": {}, "pnl_pct": None, "pos_pct": None,
                "foreign_date": None, "date": None,
            })
            continue
        ind = cd["ind"]
        act = holding_action(
            ind, code=code, foreign_stopped=cd.get("foreign_stopped"),
            batches=get_batches(code, owner), avg_cost=avg_cost,
            pos_pct=cd.get("pos_pct"), mode=mode)
        lv = act["levels"]
        items.append({
            "code": code, "name": nm, "mode": mode,
            "shares": shares, "avg_cost": avg_cost, "close": ind.get("close"),
            "action": act["action"], "reason": act["reason"], "alerts": act["alerts"],
            "levels": {
                "support": list(lv["support"]) if lv["support"] else None,
                "resistance": list(lv["resistance"]) if lv["resistance"] else None,
                "stop": lv["stop"],
            },
            "pnl_pct": act["pnl_pct"], "pos_pct": act["pos_pct"],
            "foreign_date": cd.get("foreign_date"), "date": cd.get("date"),
        })
    # 排序：出場 > 減碼 > 加倉 > 續抱 > 其他（要動作的排前面）
    order = {"出場": 0, "減碼": 1, "加倉": 2, "續抱": 3}
    items.sort(key=lambda x: order.get(x["action"], 9))
    return items


def run(notify=True, fetch=None, foreign_lookup=None):
    db.migrate_owner_data()
    date = str(now_tw().date())
    fetch = fetch or (lambda c: fetch_daily(c, months=12, workers=2))
    foreign_lookup = foreign_lookup or fetch_foreign_flow
    names = _name_lookup()

    # 1) 各帳號持股代號聯集 → 每檔只抓一次日線＋外資（順手存日線供網頁讀）
    codes = sorted(all_held_codes())
    for c in codes:                       # 補名稱：追蹤清單沒有的，用證交所股票清單解析（乾淨 IP）
        if c not in names:
            try:
                m = resolve_stocks(c)
                if m and m[0][1]:
                    names[c] = m[0][1]
            except Exception:
                pass
    code_data = {}
    for c in codes:
        try:
            df = fetch(c)
        except Exception as e:
            print(f"[holdings] {c} 抓日線失敗：{e}")
            df = None
        if df is None or getattr(df, "empty", True):
            code_data[c] = {"ind": None}
            continue
        try:
            db.set_state(f"bars:{c}", dump_bars(df))
        except Exception as e:
            print(f"[holdings] 存日線失敗 {c}：{e}")
        try:
            fo = foreign_lookup(c)
        except Exception:
            fo = None
        ind = compute_indicators(df, {})
        code_data[c] = {
            "ind": ind,
            "pos_pct": position_pct(df),
            "foreign_stopped": (fo or {}).get("stopped"),
            "foreign_date": (fo or {}).get("date"),
            "date": str(df.index[-1].date()),
        }

    # 2) 每個有持股的帳號各存一份操作建議
    owners = set()
    if db.db_enabled():
        owners |= {k[len("hold:"):] for k in (db.get_states_by_prefix("hold:") or {})}
    owners.add(DEFAULT_OWNER)
    admin_items = []
    for owner in owners:
        items = _compute_for_owner(owner, code_data, names)
        try:
            db.set_state(STATE_PREFIX + owner, {"date": date, "items": items})
        except Exception as e:
            print(f"[holdings] 存 {owner} 操作建議失敗：{e}")
        if owner == DEFAULT_OWNER:
            admin_items = items

    print(f"[holdings] date={date} 持股代號={len(codes)} 帳號={len(owners)} "
          f"admin持股={len(admin_items)}")
    for it in admin_items:
        print(f"[holdings] {it['name']} ({it['code']}) [{it['action']}] "
              f"損益={it.get('pnl_pct')} 位階={it.get('pos_pct')}")

    if notify:
        tg.send(_digest(date, admin_items))
    return {"date": date, "items": admin_items}


if __name__ == "__main__":
    run()
