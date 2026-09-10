"""每月營收查詢（跑在 Actions；沙盒連不到 TWSE/TPEx）。

用法（workflow_dispatch，job=revenue，quote_codes=逗號分隔代號；留空＝目前持股）：
印出該代號在「上市 t187ap05_L」與「上櫃 mopsfin_t187ap05_O」兩份公開資料裡的
最新一筆月營收：資料年月、當月營收、月增、年增、累計年增。

為什麼要有這個：9/10 營收週是持股的判決日，判「指引有沒有兌現」靠的是這個數字，
不是線圖。教訓（CLAUDE.md）：「系統沒接這個資料源」≠「查不到」——接上就有。
欄位名照 MOPS 原樣（含全形括號），抓不到指定欄位就整列原樣印出，不猜。
"""
import os
import re
import requests

from core.tz import today_tw

HEADERS = {"accept": "application/json", "user-agent": "Mozilla/5.0"}
SOURCES = [
    ("上市", "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"),
    ("上櫃", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O"),
]
_CODE_KEYS = ("公司代號", "SecuritiesCompanyCode", "Code")


def _code(row):
    for k in _CODE_KEYS:
        if k in row:
            return str(row[k]).strip()
    return ""


def _num(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _fmt(row):
    """有標準欄位就排版；沒有就整列印出（欄位名變了要看得到）。"""
    ym = row.get("資料年月") or row.get("出表日期")
    cur = _num(row.get("營業收入-當月營收"))
    prev = _num(row.get("營業收入-上月營收"))
    ly = _num(row.get("營業收入-去年當月營收"))
    mom = row.get("營業收入-上月比較增減(%)")
    yoy = row.get("營業收入-去年同月增減(%)")
    cum_yoy = row.get("累計營業收入-前期比較增減(%)")
    if cur is None:
        return "    " + str(row)
    def yi(x):
        return f"{x / 1e5:,.2f} 億" if x is not None else "?"
    return (f"    資料年月 {ym}｜當月 {yi(cur)}（上月 {yi(prev)}、去年同月 {yi(ly)}）"
            f"｜月增 {mom}%｜年增 {yoy}%｜累計年增 {cum_yoy}%")


# MOPS 月營收彙總頁：公司一申報就會出現在當月頁面，比 openapi 的月彙總早很多。
# 頁面是 big5 的巢狀 table，沒裝 lxml/bs4，用正則拆 <tr>/<td> 就夠（欄位順序固定：
# 代號、名稱、當月、上月、去年當月、月增%、年增%、累計、去年累計、累計增減%、備註）。
# MOPS 2025 改版後舊站搬到 mopsov；兩個主機都試，哪個活用哪個。
_MOPS_HOSTS = ("mops.twse.com.tw", "mopsov.twse.com.tw")
_MOPS_URL = "https://{host}/nas/t21/{board}/t21sc03_{y}_{m}_0.html"
_MOPS_AJAX = "https://{host}/mops/web/ajax_t05st10_ifrs"
_MOPS_LABELS = ("當月", "上月", "去年當月", "月增%", "年增%", "累計", "去年累計", "累計增減%")


def _target_ym(today=None):
    """上一個月（民國年, 月）：9 月查 8 月營收。"""
    d = today or today_tw()
    y, m = (d.year, d.month - 1) if d.month > 1 else (d.year - 1, 12)
    return y - 1911, m


def _strip(html):
    return re.sub(r"<[^>]+>", "", html).replace("&nbsp;", " ").strip()


def mops_rows(board, y, m, codes, fetcher=None):
    """回 {code: [cells...]}；抓不到回 {} 並印原因。board=sii(上市)/otc(上櫃)。"""
    html = None
    for host in (_MOPS_HOSTS if fetcher is None else ("test",)):
        url = _MOPS_URL.format(host=host, board=board, y=y, m=m)
        try:
            if fetcher is None:
                r = requests.get(url, headers={"user-agent": HEADERS["user-agent"]}, timeout=30)
                if r.status_code != 200:
                    print(f"[MOPS {host} {board} {y}/{m}] HTTP {r.status_code}")
                    continue
                html = r.content.decode("big5", errors="replace")
            else:
                html = fetcher(url)
            break
        except Exception as e:
            print(f"[MOPS {host} {board} {y}/{m}] 抓取失敗：{type(e).__name__}: {e}")
    if html is None:
        return {}
    out = {}
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.S | re.I):
        cells = [_strip(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, flags=re.S | re.I)]
        if cells and cells[0] in codes:
            out[cells[0]] = cells
    if not out:
        print(f"[MOPS {board} {y}/{m}] 頁面 {len(html)} 字，未找到指定代號"
              f"（{'頁面可能尚未有資料' if '公司代號' in html else '格式不符或被擋'}）")
    return out


def mops_single(code, y, m):
    """備援：MOPS 單一公司月營收查詢（ajax_t05st10_ifrs）。回應格式未驗證，
    先把找得到的數字列與前 400 字印出來當診斷，抓到再收斂成正式解析。"""
    for host in _MOPS_HOSTS:
        url = _MOPS_AJAX.format(host=host)
        data = {"encodeURIComponent": "1", "step": "1", "firstin": "1", "off": "1",
                "queryName": "co_id", "inpuType": "co_id", "TYPEK": "all",
                "co_id": code, "year": str(y), "month": str(m)}
        try:
            r = requests.post(url, data=data, timeout=30,
                              headers={"user-agent": HEADERS["user-agent"]})
        except Exception as e:
            print(f"[MOPS ajax {host} {code}] 失敗：{type(e).__name__}: {e}")
            continue
        txt = r.text
        print(f"[MOPS ajax {host} {code} {y}/{m}] HTTP {r.status_code}、{len(txt)} 字")
        rows = [_strip(tr) for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", txt, flags=re.S | re.I)]
        hits = [x for x in rows if re.search(r"\d{1,3}(,\d{3})+", x)]
        if hits:
            for x in hits[:12]:
                print("    " + re.sub(r"\s+", " ", x)[:200])
            return True
        print("    前 400 字：" + re.sub(r"\s+", " ", _strip(txt))[:400])
    return False


def _fmt_mops(cells):
    name, vals = cells[1], cells[2:10]
    parts = []
    for label, v in zip(_MOPS_LABELS, vals):
        n = _num(v)
        if label.endswith("%") or n is None:
            parts.append(f"{label} {v}")
        else:
            parts.append(f"{label} {n / 1e5:,.2f} 億")
    return f"    {name}｜" + "｜".join(parts)


def run():
    raw = os.environ.get("QUOTE_CODES", "").strip()
    if raw:
        codes = [c.strip() for c in raw.split(",") if c.strip()]
    else:
        from core.holdings import all_held_codes
        codes = [str(c) for c in all_held_codes()]
    codes = set(codes)
    print(f"查詢代號：{sorted(codes)}")
    found = set()
    for board, url in SOURCES:
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            rows = r.json()
        except Exception as e:
            print(f"[{board}] 抓取失敗：{type(e).__name__}: {e}")
            continue
        if not isinstance(rows, list):
            print(f"[{board}] 回應非清單：{str(rows)[:200]}")
            continue
        ym_all = sorted({str(x.get("資料年月")) for x in rows if isinstance(x, dict)})
        print(f"[{board}] {len(rows)} 列，資料年月={ym_all[-3:]}")
        for row in rows:
            if isinstance(row, dict) and _code(row) in codes:
                found.add(_code(row))
                print(f"  {_code(row)} {row.get('公司名稱', '')}（{board}）")
                print(_fmt(row))
    missing = codes - found
    if missing:
        print(f"未找到：{sorted(missing)}（可能尚未公布、或代號不在這兩份資料）")

    # openapi 只有月彙總（常落後一個月）；目標月份直接讀 MOPS 當月頁補上最新一筆。
    y, m = _target_ym()
    print(f"\n===== MOPS 民國 {y} 年 {m} 月營收（公司申報即出現）=====")
    got = set()
    for board in ("sii", "otc"):
        rows = mops_rows(board, y, m, codes)
        for code in sorted(rows):
            got.add(code)
            print(f"  {code}（{'上市' if board == 'sii' else '上櫃'}）")
            print(_fmt_mops(rows[code]))
    if codes - got:
        print(f"MOPS {y}/{m} 彙總頁尚無：{sorted(codes - got)}——改走單檔查詢備援")
        for code in sorted(codes - got):
            mops_single(code, y, m)


if __name__ == "__main__":
    run()
