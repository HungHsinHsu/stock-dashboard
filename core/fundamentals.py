"""估值面資料（本益比／殖利率／股價淨值比）。

來源：TWSE 每日「個股日本益比、殖利率及股價淨值比」(BWIBBU_d)。一次呼叫拿到全市場，
不必逐檔打，對限流友善。跑在 GitHub Actions 的乾淨 IP（Streamlit/本機 sandbox 會被擋）。

刻意只做這三個數字，因為它們是每天公布、格式穩定、不必解財報的。營收年增率、毛利率、
訂單能見度那些要另外的來源（MOPS/法說），目前沒有乾淨的免費 API，不在這裡假裝有——
寧可少給，也不要給一個看起來像基本面、其實是猜的數字。

⚠️ 本益比對循環股會反著看：獲利在循環頂端時 EPS 最高、本益比看起來最低，那往往是
最危險的時候（南亞科 2026Q2 毛利率 79.5%、本益比不到 10 倍就是這種情況）。所以這裡
只把數字與『偏離常見區間』的提示擺出來，不拿它當自動排序的主軸。
"""
import requests

from core.data import HEADERS
from core.tz import now_tw

BWIBBU_URLS = (
    "https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d",
    "https://www.twse.com.tw/exchangeReport/BWIBBU_d",
)

PE_RICH = 40.0     # 本益比高於此 → 估值偏貴，標記
PE_CHEAP = 8.0     # 低於此 → 循環股常見的「頂部假便宜」，標記提醒而非加分
YIELD_GOOD = 4.0   # 殖利率高於此 → 值得一提


def _f(v):
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _idx(fields, *keys):
    """在欄位名清單裡找第一個同時含有全部關鍵字的位置；找不到回 None。"""
    for i, name in enumerate(fields):
        s = str(name)
        if all(k in s for k in keys):
            return i
    return None


def fetch_valuation(date=None):
    """回 {code: {'pe': float|None, 'yield': float|None, 'pb': float|None}}；抓不到回 {}。

    date 給 'YYYYMMDD'（預設今天，台灣時間）。當日尚未發布時 TWSE 會回非 OK，
    此時回空 dict——由呼叫端決定要不要降級（本專案的做法是照常推薦、但標明無估值資料）。
    """
    ymd = date or now_tw().strftime("%Y%m%d")
    for url in BWIBBU_URLS:
        try:
            j = requests.get(url, params={"response": "json", "date": ymd},
                             headers=HEADERS, timeout=20).json()
        except Exception as e:
            print(f"[valuation] {url.split('/')[-1]} 抓取失敗：{type(e).__name__} {e}")
            continue
        if str(j.get("stat")) != "OK":
            print(f"[valuation] {url.split('/')[-1]} 非 OK：{j.get('stat')}（date={ymd}）")
            continue
        fields = j.get("fields") or []
        rows = j.get("data") or []
        i_code = _idx(fields, "代號")
        i_pe = _idx(fields, "本益比")
        i_yd = _idx(fields, "殖利率")
        i_pb = _idx(fields, "淨值比")
        if i_code is None:
            print(f"[valuation] 找不到代號欄，欄位={fields}")
            continue
        out = {}
        for r in rows:
            try:
                code = str(r[i_code]).strip()
            except (IndexError, TypeError):
                continue
            if not code:
                continue
            out[code] = {
                "pe": _f(r[i_pe]) if i_pe is not None and i_pe < len(r) else None,
                "yield": _f(r[i_yd]) if i_yd is not None and i_yd < len(r) else None,
                "pb": _f(r[i_pb]) if i_pb is not None and i_pb < len(r) else None,
            }
        print(f"[valuation] {ymd} 取得 {len(out)} 檔估值")
        return out
    return {}


def valuation_notes(val):
    """把估值數字翻成人看得懂的提示（list[str]）。無資料回空 list。

    只描述、不打分——本益比高低在不同產業／循環位置的意義完全相反，
    自動打分會製造出「看起來客觀」的錯誤結論。"""
    if not val:
        return []
    notes = []
    pe, yd, pb = val.get("pe"), val.get("yield"), val.get("pb")
    if pe is not None:
        if pe >= PE_RICH:
            notes.append(f"本益比 {pe:.1f} 偏高（≥{PE_RICH:.0f}）：獲利要跟上才撐得住")
        elif pe <= PE_CHEAP:
            notes.append(f"本益比 {pe:.1f} 偏低（≤{PE_CHEAP:.0f}）：若為循環股要當心"
                         "『獲利高峰＝本益比假便宜』")
        else:
            notes.append(f"本益比 {pe:.1f}")
    if yd is not None and yd >= YIELD_GOOD:
        notes.append(f"殖利率 {yd:.2f}%（≥{YIELD_GOOD:.0f}%）")
    if pb is not None:
        notes.append(f"股價淨值比 {pb:.2f}")
    return notes
