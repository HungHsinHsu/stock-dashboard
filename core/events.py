"""事件行事曆：讓機器人「知道時事」的第一層——已排定、機械可知的事件。

背景教訓（CLAUDE.md）：亞航漲停當天就是總預算三讀日，歸因卻寫成「題材發酵」；
南亞科分析漏了 MSCI 納入生效。這類事件『事前就查得到』，缺的不是資料而是
「有人在看行事曆」。此模組把行事曆變成程式的一部分，餵進早盤預測的 prompt
與推播，讓判斷方向時至少知道「今天有什麼已排定的事」。

四個來源：
1. STATIC_EVENTS：手動維護的總經大事（FOMC 等，日期見 federalreserve.gov）。
2. 規則事件：台股上市櫃每月 10 日前公布上月營收 → 每月 8~10 日標「營收窗」。
3. 手動事件：/行事曆 指令寫進 DB(app_state key=calendar:manual)；法說會、
   政策時程（立法院表決日）這類抓不到 API 的就靠這裡。
4. 除權息（最佳努力）：TWSE openapi 預告表比對追蹤清單；抓不到就靜默略過
   （sandbox/本機沒網路很正常，Actions 上才有）。

只回「今天(台灣日)有什麼」；時間一律台灣 UTC+8（core/tz.py）。
"""
import json
import os
import re
from datetime import date as _date, timedelta

import requests

from core import db
from core.tz import today_tw

# ── 1. 手動維護的總經大事 ─────────────────────────────────────────
# 日期＝台灣日。FOMC 決議在美東下午 2 點＝台灣『隔日』凌晨，所以決議「揭曉日」
# 的台灣日要 +1。2026 剩餘場次：9/15-16、10/27-28、12/8-9（9、12 月附點陣圖）。
STATIC_EVENTS = {
    "2026-09-15": ["FOMC 會議第 1 天（決議台灣 9/17 凌晨公布，附點陣圖）——會前市場常觀望"],
    "2026-09-16": ["FOMC 會議第 2 天（決議台灣 9/17 凌晨 2 點公布，附點陣圖）"],
    "2026-09-17": ["FOMC 決議今晨（台灣凌晨 2 點）已公布——今日美股隔夜已反映決議，權重加重"],
    "2026-10-27": ["FOMC 會議第 1 天（決議台灣 10/29 凌晨公布）"],
    "2026-10-28": ["FOMC 會議第 2 天（決議台灣 10/29 凌晨 2 點公布）"],
    "2026-10-29": ["FOMC 決議今晨（台灣凌晨 2 點）已公布——今日美股隔夜已反映決議，權重加重"],
    "2026-12-08": ["FOMC 會議第 1 天（決議台灣 12/10 凌晨公布，附點陣圖）"],
    "2026-12-09": ["FOMC 會議第 2 天（決議台灣 12/10 凌晨 3 點公布，附點陣圖）"],
    "2026-12-10": ["FOMC 決議今晨（台灣凌晨 3 點）已公布——今日美股隔夜已反映決議，權重加重"],
}

# ── 2. 規則事件：月營收公布窗 ────────────────────────────────────


def rule_events(d):
    """d=ISO 日期字串。台股上市櫃須於每月 10 日前公布上月營收。"""
    day = int(d[8:10])
    if 8 <= day <= 10:
        tail = "今天是截止日" if day == 10 else f"10 日截止（剩 {10 - day} 天）"
        return [f"月營收公布窗：上市櫃 10 日前公布上月營收，{tail}——"
                "持股若已公布，先看數字再談方向"]
    return []


# ── 3. 手動事件（/行事曆 指令）──────────────────────────────────
_MANUAL_KEY = "calendar:manual"
_MANUAL_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                            "calendar_manual.json")


def load_manual():
    """回 [{"date": "YYYY-MM-DD", "text": "..."}]，依日期排序。"""
    if db.db_enabled():
        items = db.get_state(_MANUAL_KEY, []) or []
    else:
        try:
            with open(_MANUAL_PATH, encoding="utf-8") as f:
                items = json.load(f)
        except (OSError, ValueError):
            items = []
    return sorted(items, key=lambda x: x.get("date", ""))


def _save_manual(items):
    if db.db_enabled():
        db.set_state(_MANUAL_KEY, items)
    else:
        with open(_MANUAL_PATH, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=1)


def parse_date(txt, today=None):
    """接受 YYYY-MM-DD / M/D / MM-DD → ISO 日期字串；解析失敗回 None。
    只給月日時補當年；若那天已過就當成明年（行事曆只加未來的事）。"""
    txt = (txt or "").strip()
    m = re.fullmatch(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", txt)
    if m:
        y, mo, dd = map(int, m.groups())
    else:
        m = re.fullmatch(r"(\d{1,2})[-/](\d{1,2})", txt)
        if not m:
            return None
        today = today or today_tw()
        y, (mo, dd) = today.year, map(int, m.groups())
    try:
        d = _date(y, mo, dd)
    except ValueError:
        return None
    if len(txt) <= 5:                       # 只給月日且已過 → 明年
        today = today or today_tw()
        if d < today:
            d = _date(y + 1, mo, dd)
    return d.isoformat()


def add_manual_event(date_iso, text):
    items = load_manual()
    items.append({"date": date_iso, "text": text})
    _save_manual(items)
    return len(items)


def remove_manual_events(date_iso):
    """刪掉某天的全部手動事件，回刪除筆數。"""
    items = load_manual()
    kept = [x for x in items if x.get("date") != date_iso]
    _save_manual(kept)
    return len(items) - len(kept)


# ── 4. 除權息預告（最佳努力）────────────────────────────────────
_TWSE_EXDIV_URL = "https://openapi.twse.com.tw/v1/exchangeReport/TWT48U"


def _roc_to_iso(s):
    """民國年日期（1150915 或 115/09/15）→ ISO；認不得回 None。"""
    s = str(s or "").replace("/", "")
    if not s.isdigit() or len(s) < 6:
        return None
    y, mo, dd = int(s[:-4]) + 1911, int(s[-4:-2]), int(s[-2:])
    try:
        return _date(y, mo, dd).isoformat()
    except ValueError:
        return None


def exdiv_events(d, codes, fetcher=None):
    """追蹤清單裡今天除權息的標的。抓不到（無網路/欄位變動）回 []，不丟例外。
    教訓來源：緯穎 9/2 除權沒人提醒，差點在參考價重設日進場。"""
    if not codes:
        return []
    try:
        fetcher = fetcher or (lambda: requests.get(
            _TWSE_EXDIV_URL, timeout=15,
            headers={"accept": "application/json"}).json())
        rows = fetcher() or []
        out = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            code = str(r.get("Code") or r.get("股票代號") or "").strip()
            if code not in set(map(str, codes)):
                continue
            rd = _roc_to_iso(r.get("Date") or r.get("資料日期"))
            if rd != d:
                continue
            name = str(r.get("Name") or r.get("名稱") or "").strip()
            kind = str(r.get("DividendType") or "權息").strip()
            out.append(f"{name}({code}) 今日除{kind}——參考價重設，"
                       "均線/漲跌幅全數失真，當日不適用一般進出訊號")
        return out
    except Exception:
        return []


# ── 組裝 ─────────────────────────────────────────────────────────


def events_for(d, codes=None, fetcher=None):
    """某台灣日的全部已知事件（字串清單）。d=ISO 字串。"""
    evts = list(STATIC_EVENTS.get(d, []))
    evts += rule_events(d)
    evts += [x["text"] for x in load_manual() if x.get("date") == d]
    evts += exdiv_events(d, codes or [], fetcher=fetcher)
    return evts


def upcoming(days=14, today=None):
    """未來 N 天（含今天）的事件總表，給 /行事曆 列表用。回 [(date, text)]。"""
    today = today or today_tw()
    out = []
    for i in range(days):
        d = (today + timedelta(days=i)).isoformat()
        for t in STATIC_EVENTS.get(d, []) + rule_events(d):
            out.append((d, t))
    for x in load_manual():
        if today.isoformat() <= x.get("date", "") < (today + timedelta(days=days)).isoformat():
            out.append((x["date"], x["text"] + "（手動）"))
    return sorted(out)
