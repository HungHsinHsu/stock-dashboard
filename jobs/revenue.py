"""每月營收查詢（跑在 Actions；沙盒連不到 TWSE/TPEx）。

用法（workflow_dispatch，job=revenue，quote_codes=逗號分隔代號；留空＝目前持股）：
印出該代號在「上市 t187ap05_L」與「上櫃 mopsfin_t187ap05_O」兩份公開資料裡的
最新一筆月營收：資料年月、當月營收、月增、年增、累計年增。

為什麼要有這個：9/10 營收週是持股的判決日，判「指引有沒有兌現」靠的是這個數字，
不是線圖。教訓（CLAUDE.md）：「系統沒接這個資料源」≠「查不到」——接上就有。
欄位名照 MOPS 原樣（含全形括號），抓不到指定欄位就整列原樣印出，不猜。
"""
import os
import requests

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


if __name__ == "__main__":
    run()
