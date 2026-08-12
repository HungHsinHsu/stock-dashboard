"""上櫃（TPEx／櫃買中心）資料源。

存在的理由：本專案原本四個資料源全部只涵蓋『上市』——日線 STOCK_DAY、法人 T86、
估值 BWIBBU_d、母體 STOCK_DAY_ALL。於是上櫃股（世界先進 5347、波若威 3163…）
永遠不會進選股清單，個股查詢也只有 MIS 盤中價、沒有均線可算支撐，承接法整套失效。

⚠️ 單位差異（踩過會很痛），而且 TPEx **自己兩個端點就不一致**：
   ・單檔日線 tradingStock：'成交張數'（張）、'成交仟元'（仟元）
   ・全市場 daily_close_quotes：TradingShares（股）、TransactionAmount（元）
   TWSE STOCK_DAY 給的是股。量比是比值、錯了也看不出來，但籌碼分布印絕對量，
   而且上市/上櫃混在同一份清單裡按成交金額排序時會直接排錯。
   故這裡一律換算成「股／元」對齊 TWSE 語意，換算只做在解析層、外面看到的都同單位。

⚠️ 欄名不可靠：法人 OpenAPI 的鍵名有前導空白與不一致的空格
   （' Foreign Investors ...-Total Sell'、'Dealers -TotalSell'），所以比對前一律
   正規化（去空白、轉小寫）。直接用字面鍵取值會在某些欄位上靜默拿到 None。

欄位長相是丟 jobs/tpexprobe.py 上 GitHub Actions 跑真實回應確認的，不是憑記憶猜的
（sandbox 與 Streamlit 連不到 tpex.org.tw，proxy 回 403）。TPEx 2025 年改版過一次，
日後若又改版，重跑那支探針比對即可。
"""
import json
import time

import pandas as pd
import requests
from dateutil.relativedelta import relativedelta

from core.data import HEADERS
from core.tz import now_tw

TPEX_DELAY = 0.3
OPENAPI = "https://www.tpex.org.tw/openapi/v1"
WWW = "https://www.tpex.org.tw/www/zh-tw"

# 成交量單位換算：TPEx 仟股 → 股（對齊 TWSE STOCK_DAY）
SHARES_PER_LOT = 1000


def _num(v):
    try:
        return float(str(v).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError, AttributeError):
        return None


def _int(v):
    n = _num(v)
    return int(n) if n is not None else None


def _norm(k):
    """欄名正規化：去掉所有空白、轉小寫。TPEx 的鍵名空格不一致，不正規化會漏抓。"""
    return "".join(str(k).split()).lower()


def _roc_to_ts(s):
    """民國日期字串 → pandas Timestamp。吃 '115/08/03' 與 '1150812' 兩種格式。"""
    s = str(s).strip()
    try:
        if "/" in s:
            y, m, d = s.split("/")
        elif len(s) == 7:                 # 1150812
            y, m, d = s[:3], s[3:5], s[5:]
        else:
            return None
        return pd.Timestamp(f"{int(y) + 1911}-{int(m):02d}-{int(d):02d}")
    except (ValueError, TypeError):
        return None


# 全市場快照的行程內快取。這幾支是「當日一份」的資料，但呼叫端是逐檔問的
# （fetch_foreign_flow 每檔叫一次），不快取的話選股掃 150 檔＝下載 150 次同一份
# 860KB 法人表（約 129MB），4MB 的行情表還被母體與名稱表各叫一次。
# 給 TTL 而不是永久快取，是因為 Streamlit 是長駐行程，永久快取會讓它整天顯示同一天的資料。
_CACHE_TTL = 600.0        # 秒
_cache = {}


def _cached(key, fn):
    hit = _cache.get(key)
    now = time.monotonic()
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    val = fn()
    _cache[key] = (now, val)
    return val


def _get_json(url, params=None, timeout=60, retries=2, stream=False):
    """抓 JSON。大回應（全市場行情約 4MB）會 ChunkedEncodingError 讀一半斷，
    所以用分塊讀＋重試；小回應也走同一條路，行為一致好debug。"""
    last = None
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers=HEADERS, params=params,
                             timeout=timeout, stream=stream)
            if r.status_code != 200:
                last = f"HTTP {r.status_code}"
            else:
                body = b"".join(r.iter_content(65536)) if stream else r.content
                return json.loads(body.decode("utf-8"))
        except Exception as e:
            last = f"{type(e).__name__}: {str(e)[:120]}"
        if attempt < retries:
            time.sleep(TPEX_DELAY * (attempt + 1))
    print(f"[tpex] 取得失敗 {url}：{last}")
    return None


# ── 估值：本益比／殖利率／股價淨值比 ──────────────────────────────────
def fetch_tpex_valuation():
    """回 {code: {'pe','yield','pb'}}，對齊 core.fundamentals.fetch_valuation 的形狀。

    來源是 OpenAPI 每日快照，沒有 date 參數——它就是「最新一日」。所以呼叫端不能
    假設它一定等於今天（非交易日會是上一個交易日），要顯示日期就自己記。
    """
    data = _cached("valuation",
                   lambda: _get_json(f"{OPENAPI}/tpex_mainboard_peratio_analysis"))
    if not isinstance(data, list):
        return {}
    out = {}
    for r in data:
        if not isinstance(r, dict):
            continue
        code = str(r.get("SecuritiesCompanyCode") or "").strip()
        if not code:
            continue
        out[code] = {
            "pe": _num(r.get("PriceEarningRatio")),
            "yield": _num(r.get("YieldRatio")),
            "pb": _num(r.get("PriceBookRatio")),
        }
    print(f"[tpex] 估值取得 {len(out)} 檔")
    return out


# ── 三大法人買賣超 ──────────────────────────────────────────────
# 正規化後的鍵名。外資有兩個版本，取「不含外資自營商」那個，與 TWSE T86 的
# 首選欄位（外陸資買賣超股數(不含外資自營商)）語意一致。
_K_FOREIGN = ("foreigninvestorsincludemainlandareainvestors"
              "(foreigndealersexcluded)-difference")
_K_TRUST = "securitiesinvestmenttrustcompanies-difference"
_K_DEALER = "dealers-difference"          # 注意：foreigndealers-difference 也含這串
_K_TOTAL = "totaldifference"


def _insti_row_values(row):
    """把一列法人資料轉成 {'foreign','trust','dealer','total'}（股數）。"""
    norm = {_norm(k): v for k, v in row.items()}
    dealer = None
    for k, v in norm.items():
        # 自營商：要剛好是 dealers-difference，不能是 foreigndealers-difference
        if k == _K_DEALER:
            dealer = _int(v)
            break
    return {
        "foreign": _int(norm.get(_K_FOREIGN)),
        "trust": _int(norm.get(_K_TRUST)),
        "dealer": dealer,
        "total": _int(norm.get(_K_TOTAL)),
    }


def fetch_tpex_insti(code):
    """單日三大法人買賣超（OpenAPI 只有最新一日）。

    回 {'net','sold_streak','stopped','date','trust_net','dealer_net','total_net'}，
    形狀對齊 core.data.fetch_foreign_flow，讓呼叫端不必分上市/上櫃。

    ⚠️ 找不到該股一律回 **None**，不回「net=0、stopped=True」。
    這個區別是這支最重要的一件事：0 的意思是「法人今天沒買賣它」，None 的意思是
    「這檔不在上櫃名單、我不知道」。混為一談會把『不知道』當成『外資已停止倒貨』，
    直接放行承接法第四關——那是用無知去產生進場訊號。
    TPEx 連不到時同樣回 None（讓呼叫端去走上市那條路），不要假裝有答案。

    ⚠️ sold_streak 永遠是 0 或 1：OpenAPI 只給最新一日，拿不到歷史，
    所以「連續賣超幾天」在上櫃股上算不出來。承接法第四關只看 stopped
    （最近一日有沒有停止賣超），那一關仍然成立；但別把 sold_streak
    當成跟上市股同等意義的數字。
    """
    # 快取：呼叫端是逐檔問的，這份卻是全市場一份 860KB
    data = _cached("insti",
                   lambda: _get_json(f"{OPENAPI}/tpex_3insti_daily_trading"))
    if not isinstance(data, list):
        return None
    for r in data:
        if not isinstance(r, dict):
            continue
        if str(r.get("SecuritiesCompanyCode") or "").strip() != str(code):
            continue
        v = _insti_row_values(r)
        net = v["foreign"]
        ts = _roc_to_ts(r.get("Date"))
        return {
            "net": net,
            "sold_streak": 1 if (net is not None and net < 0) else 0,
            "stopped": (net >= 0) if net is not None else None,
            "date": str(ts.date()) if ts is not None else None,
            "trust_net": v["trust"], "dealer_net": v["dealer"],
            "total_net": v["total"],
        }
    return None      # 不在上櫃名單＝這不是上櫃股，交給上市那條路


# ── 單檔歷史日線 ────────────────────────────────────────────────
# 欄位順序（跑探針拿到的真實 fields，不是猜的）：
#   ['日 期', '成交張數', '成交仟元', '開盤', '最高', '最低', '收盤', '漲跌', '筆數']
# 跟 TWSE STOCK_DAY 的順序一樣，但第 1、2 欄的單位不同（張／仟元 vs 股／元）。
TPEX_DAILY_FIELDS = ["日 期", "成交張數", "成交仟元", "開盤", "最高", "最低",
                     "收盤", "漲跌", "筆數"]
_I_DATE, _I_LOTS, _I_AMT, _I_OPEN, _I_HIGH, _I_LOW, _I_CLOSE = 0, 1, 2, 3, 4, 5, 6


def parse_tpex_daily(j):
    """tradingStock 回應 → list[dict]，欄位/單位對齊 core.data.parse_twse_json。

    價格欄位遇到 '--'（當日無成交）會解析失敗 → 整列跳過，不要塞 0 進去；
    0 元的 K 棒會讓均線與位階整條算錯，比少一根嚴重得多。
    """
    if not isinstance(j, dict) or str(j.get("stat", "")).lower() != "ok":
        return []
    tables = j.get("tables") or []
    if not tables or not isinstance(tables[0], dict):
        return []
    rows = []
    for row in tables[0].get("data") or []:
        try:
            ts = _roc_to_ts(row[_I_DATE])
            o, h, l, c = (_num(row[i]) for i in (_I_OPEN, _I_HIGH, _I_LOW, _I_CLOSE))
            lots = _num(row[_I_LOTS])
        except (IndexError, TypeError):
            continue
        if ts is None or None in (o, h, l, c) or c <= 0:
            continue
        rows.append({
            "Date": ts,
            "Open": o, "High": h, "Low": l, "Close": c,
            "Volume": (lots or 0) * SHARES_PER_LOT,   # 張 → 股
        })
    return rows


def _fetch_tpex_month(code, d):
    j = _get_json(f"{WWW}/afterTrading/tradingStock",
                  params={"code": str(code), "date": d.strftime("%Y/%m/%d"),
                          "response": "json"}, timeout=25)
    return parse_tpex_daily(j) if j else []


def fetch_tpex_daily(code, months=6, today=None, workers=6):
    """上櫃單檔日線，回 DataFrame(index=Date, 含 MA20)；抓不到回空（帶 schema）。
    形狀與 core.data.fetch_daily 完全一致，讓上層不必分上市/上櫃。"""
    from concurrent.futures import ThreadPoolExecutor
    today = today or now_tw()
    dates = [today - relativedelta(months=i) for i in range(months)]
    empty = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume", "MA20"])
    # 快速失敗：這支的主要呼叫情境是「TWSE 抓不到，來試試是不是上櫃」，而多數時候
    # 答案是「不是」。若不先探就直接跑滿 months 個月 × 每月 3 次重試，一檔上市股
    # 只是暫時限流就要卡上幾十秒，選股掃 150 檔會直接爆掉。
    # 探兩個月而不是一個月，是為了分辨「TPEx 連不到／不是上櫃股」與「月初當月還沒
    # 有交易日」——後者只探當月會誤判成前者。
    frames = _fetch_tpex_month(code, dates[0])
    probed = 1
    if not frames and len(dates) > 1:
        frames = _fetch_tpex_month(code, dates[1])
        probed = 2
    if not frames:
        return empty
    rest = dates[probed:]        # 探過的月份不重抓
    if workers and workers > 1 and len(rest) > 1:
        with ThreadPoolExecutor(max_workers=min(workers, len(rest))) as ex:
            for fr in ex.map(lambda d: _fetch_tpex_month(code, d), rest):
                frames.extend(fr)
    else:
        for i, d in enumerate(rest):
            if i:
                time.sleep(TPEX_DELAY)
            frames.extend(_fetch_tpex_month(code, d))
    df = (pd.DataFrame(frames).drop_duplicates("Date")
          .sort_values("Date").set_index("Date"))
    df["MA20"] = df["Close"].rolling(20).mean()
    return df


# ── 全市場：母體排名用 ───────────────────────────────────────────
def fetch_tpex_quotes():
    """上櫃全市場最新收盤快照，回 list[dict]（原始鍵）。

    這支回應約 4MB，非 stream 讀會 ChunkedEncodingError 讀到一半斷
    （探針第一輪就是這樣掛的），所以固定走分塊讀。
    """
    data = _cached("quotes", lambda: _get_json(
        f"{OPENAPI}/tpex_mainboard_daily_close_quotes", timeout=90, stream=True))
    return data if isinstance(data, list) else []


def fetch_tpex_top_turnover(n=150):
    """上櫃當日成交金額前 n 檔，回 [(code, name, 成交金額元), ...]。

    回傳帶金額是刻意的：呼叫端要把上市與上櫃兩份合併後**重新排序**，
    只回 (code, name) 的話就沒東西可比，會變成「上市前150 ＋ 上櫃前150」
    兩份各自的清單硬接起來，那不是前 150 大。
    """
    rows = []
    for r in fetch_tpex_quotes():
        if not isinstance(r, dict):
            continue
        code = str(r.get("SecuritiesCompanyCode") or "").strip()
        name = str(r.get("CompanyName") or "").strip()
        amt = _num(r.get("TransactionAmount"))          # 已是元
        if not code or not name or amt is None:
            continue
        if not ((code.isdigit() and len(code) == 4) or code.startswith("00")):
            continue                                     # 排除權證等非個股/ETF
        rows.append((amt, code, name))
    rows.sort(reverse=True)
    return [(c, nm, amt) for amt, c, nm in rows[:n]]
