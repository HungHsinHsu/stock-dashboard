"""探針：把 TPEx（上櫃）各候選 API 的真實回應長相印出來。

為什麼需要這支：開發環境（sandbox）跟 Streamlit 都連不到 tpex.org.tw，
只有 GitHub Actions 的乾淨 IP 連得到——跟 TWSE 的情況一樣。所以「這個網址回什麼、
欄位叫什麼名字」沒辦法在本機試，只能丟上來跑一次看真的回應，再照著寫解析器。

憑記憶猜欄位名寫 parser 是這個專案踩過最貴的坑之一，所以先探再寫。
探完之後這支留著，日後 TPEx 改版（他們 2025 年才大改過一次）可以再跑一次比對。
"""
import json

import requests

from core.data import HEADERS
from core.tz import now_tw

TIMEOUT = 25
HEAD = 300          # 非 JSON 時印出前幾個字


def _roc(d, sep="/"):
    """西元 datetime → 民國字串，TPEx 舊版端點吃這個格式（115/08/12）。"""
    return f"{d.year - 1911:03d}{sep}{d.month:02d}{sep}{d.day:02d}"


def _probe(tag, url, params=None):
    print(f"\n===== {tag} =====")
    print(f"  url: {url}")
    if params:
        print(f"  params: {params}")
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=TIMEOUT)
    except Exception as e:
        print(f"  ✗ 連線失敗 {type(e).__name__}: {e}")
        return None
    ct = r.headers.get("content-type", "")
    print(f"  HTTP {r.status_code}｜{ct[:60]}｜{len(r.content)} bytes")
    if r.status_code != 200:
        print(f"  body head: {r.text[:HEAD]!r}")
        return None
    try:
        j = r.json()
    except Exception as e:
        print(f"  ✗ 非 JSON（{type(e).__name__}）；body head: {r.text[:HEAD]!r}")
        return None
    if isinstance(j, list):
        print(f"  JSON list，共 {len(j)} 筆")
        if j:
            print(f"  第一筆 keys: {list(j[0].keys()) if isinstance(j[0], dict) else type(j[0])}")
            print(f"  第一筆: {json.dumps(j[0], ensure_ascii=False)[:400]}")
    elif isinstance(j, dict):
        print(f"  JSON dict，keys: {list(j.keys())[:20]}")
        for k in ("stat", "date", "iTotalRecords", "total", "code"):
            if k in j:
                print(f"    {k} = {j[k]!r}")
        for k in ("fields", "tables", "data", "aaData"):
            v = j.get(k)
            if isinstance(v, list) and v:
                print(f"    {k}: {len(v)} 筆")
                if k in ("fields",):
                    print(f"      {v}")
                elif isinstance(v[0], dict):
                    print(f"      第一筆 keys: {list(v[0].keys())[:20]}")
                    print(f"      第一筆: {json.dumps(v[0], ensure_ascii=False)[:400]}")
                else:
                    print(f"      第一筆: {json.dumps(v[0], ensure_ascii=False)[:400]}")
    return j


def run():
    now = now_tw()
    ymd_slash = now.strftime("%Y/%m/%d")
    roc_slash = _roc(now)
    roc_month = f"{now.year - 1911:03d}/{now.month:02d}"
    code = "5347"        # 世界先進，拿來驗單檔日線
    print(f"[tpexprobe] 台灣時間 {now:%Y-%m-%d %H:%M}｜西元 {ymd_slash}｜民國 {roc_slash}")

    # ── 1) OpenAPI：一次拿全市場，最適合當母體與估值來源 ──────────────
    _probe("A1 OpenAPI 上櫃每日收盤行情",
           "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes")
    _probe("A2 OpenAPI 上櫃本益比/殖利率/淨值比",
           "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis")
    _probe("A3 OpenAPI 上櫃三大法人買賣超",
           "https://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading")

    # ── 2) 單檔歷史日線（要月為單位，才拼得出 MA60/位階）──────────────
    _probe("B1 新站 afterTrading/tradingStock（單檔月線）",
           "https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock",
           {"code": code, "date": ymd_slash, "response": "json"})
    _probe("B2 舊站 st43_result.php（單檔月線）",
           "https://www.tpex.org.tw/web/stock/aftertrading/daily_trading_info/st43_result.php",
           {"l": "zh-tw", "d": roc_month, "stkno": code, "o": "json"})

    # ── 3) 三大法人（承接法第四關：外資有沒有停止倒貨）────────────────
    _probe("C1 新站 insti/dailyTrade",
           "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade",
           {"type": "Daily", "sect": "EW", "date": ymd_slash, "response": "json"})
    _probe("C2 舊站 3itrade_hedge_result.php",
           "https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php",
           {"l": "zh-tw", "se": "EW", "t": "D", "d": roc_slash, "o": "json"})

    # ── 4) 估值（單日全市場本益比）───────────────────────────────
    _probe("D1 新站 afterTrading/peQryDate",
           "https://www.tpex.org.tw/www/zh-tw/afterTrading/peQryDate",
           {"date": ymd_slash, "response": "json"})
    _probe("D2 舊站 pera_result.php",
           "https://www.tpex.org.tw/web/stock/aftertrading/peratio_analysis/pera_result.php",
           {"l": "zh-tw", "d": roc_slash, "c": "", "o": "json"})

    print("\n[tpexprobe] 完成。挑回得動、欄位齊的那組寫進 core/tpex.py。")


if __name__ == "__main__":
    run()
