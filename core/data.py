import time
import pandas as pd
import requests
from datetime import datetime
from dateutil.relativedelta import relativedelta

HEADERS = {"User-Agent": "Mozilla/5.0 (stock-dashboard)"}

# 每次打 TWSE 之間的禮貌間隔（秒），避免多檔時瞬間連發被限流/封 IP。
TWSE_DELAY = 0.3

# 要預測/復盤的股票清單。新增一檔 = 在這裡多加一個項目即可。
#   key   = 顯示名稱（自取，建議「名稱 (代號)」）
#   code  = 證交所股票代號（必填）
#   supports = 手畫的水平支撐（選填）。有填才會做「守住支撐1」判斷；
#              省略則該股只看方向與 MA20。
# 範例：
#   "台積電 (2330)": {"code": "2330"},                       # 只填代號
#   "聯發科 (2454)": {"code": "2454",
#       "supports": {"支撐1 (短期)": 1000, "支撐3 (長期)": 850}},
STOCKS = {
    "華邦電 (2344)": {
        "code": "2344",
        "supports": {"支撐1 (短期)": 222, "支撐3 (長期)": 142},
    },
}


def fetch_stock_name(code, today=None):
    """用代號查中文股名（例 2330 -> 台積電）；查不到回 None。"""
    today = today or datetime.today()
    ym = today.strftime("%Y%m01")
    url = (
        "https://www.twse.com.tw/exchangeReport/STOCK_DAY"
        f"?response=json&date={ym}&stockNo={code}"
    )
    try:
        title = requests.get(url, headers=HEADERS, timeout=10).json().get("title", "")
    except Exception:
        return None
    # 例： "115年06月 2330 台積電 各日成交資訊"
    parts = title.split()
    if code in parts:
        i = parts.index(code)
        if i + 1 < len(parts):
            return parts[i + 1]
    return None


US_INDICES = {"費半SOX": "^SOX", "Nasdaq": "^IXIC", "標普500": "^GSPC", "道瓊": "^DJI"}


def _yahoo_change(symbol):
    """用 Yahoo Finance 日線抓某標的最近兩日收盤，回漲跌 % 或 None。"""
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{symbol.replace('^', '%5E')}?range=7d&interval=1d"
    )
    try:
        res = requests.get(url, headers=HEADERS, timeout=15).json()
        closes = [
            c for c in res["chart"]["result"][0]["indicators"]["quote"][0]["close"]
            if c is not None
        ]
    except Exception:
        return None
    if len(closes) < 2:
        return None
    prev, last = closes[-2], closes[-1]
    return round((last - prev) / prev * 100, 2) if prev else None


def fetch_us_overnight():
    """美股主要指數隔夜漲跌 {name: pct}（Yahoo Finance；抓不到的略過）。"""
    out = {}
    for name, sym in US_INDICES.items():
        c = _yahoo_change(sym)
        if c is not None:
            out[name] = c
    return out


TAIFEX_URLS = (
    "https://openapi.taifex.com.tw/v1/DailyMarketReportFutAH",  # 盤後(夜盤)，已反映美股隔夜
    "https://openapi.taifex.com.tw/v1/DailyMarketReportFut",    # 一般交易時段(備援)
)


def _num(v):
    try:
        return float(str(v).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _pick(row, names):
    """從一列(dict)取第一個有值的欄位；找不到回 None。"""
    for n in names:
        if n in row and str(row[n]).strip() not in ("", "-", "--"):
            return row[n]
    return None


def _taifex_change(data):
    """從 TAIFEX 期貨每日行情(list[dict])解析台指期(TX)近月漲跌%；失敗回 None。"""
    tx = [
        r for r in data
        if str(_pick(r, ("Contract", "契約", "商品代號", "FUTURES_ID")) or "")
        .strip().upper() == "TX"
    ]
    if not tx:
        return None
    # 同商品有多個到期月，取成交量最大者(近月最活躍)
    tx.sort(key=lambda r: _num(_pick(r, ("Volume", "成交量"))) or 0, reverse=True)
    row = tx[0]
    pct = _num(_pick(row, ("%Change", "Change%", "漲跌%", "漲跌百分比", "漲跌百分比(%)")))
    if pct is None:
        last = _num(_pick(row, ("Last", "收盤價", "最後成交價", "SettlementPrice", "結算價")))
        chg = _num(_pick(row, ("Change", "漲跌價", "漲跌")))
        if last is not None and chg is not None and (last - chg):
            pct = chg / (last - chg) * 100
    if pct is None:
        print("台指期解析失敗，樣本欄位：", list(row.keys()))
        return None
    return round(pct, 2)


def fetch_taifex():
    """台指期(TX)最近交易日漲跌%：優先夜盤(盤後，已含美股隔夜)，其次一般盤；抓不到回 None。"""
    for url in TAIFEX_URLS:
        try:
            data = requests.get(url, headers=HEADERS, timeout=15).json()
        except Exception:
            continue
        if not isinstance(data, list) or not data:
            continue
        pct = _taifex_change(data)
        if pct is not None:
            return pct
    return None


def fetch_stock_list():
    """全部上市股票對照 {code: name}；雙來源備援，失敗回 {}。"""
    out = {}
    # 1) OpenAPI 每日快照（節假日通常仍回最後交易日）
    try:
        data = requests.get(
            "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
            headers=HEADERS, timeout=15,
        ).json()
        for row in data:
            c, n = row.get("Code"), row.get("Name")
            if c and n:
                out[str(c).strip()] = str(n).strip()
    except Exception:
        pass
    if out:
        return out
    # 2) 後備：www data-rows（row[0]=代號, row[1]=名稱）
    try:
        j = requests.get(
            "https://www.twse.com.tw/exchangeReport/STOCK_DAY_ALL?response=json",
            headers=HEADERS, timeout=15,
        ).json()
        for row in j.get("data", []):
            try:
                c, n = str(row[0]).strip(), str(row[1]).strip()
            except (IndexError, TypeError):
                continue
            if c and n:
                out[c] = n
    except Exception:
        pass
    return out


def resolve_stocks(query, listing=None):
    """以代號或中文名稱解析股票，回 [(code, name), ...]（0=找不到 / 1=唯一 / 多=需釐清）。"""
    q = (query or "").strip()
    if not q:
        return []
    listing = fetch_stock_list() if listing is None else listing
    if q.isdigit():
        name = listing.get(q) or fetch_stock_name(q)
        return [(q, name)] if name else []
    exact = [(c, n) for c, n in listing.items() if n == q]
    if exact:
        return exact
    return [(c, n) for c, n in listing.items() if q in n]


def parse_twse_json(j):
    """把 TWSE STOCK_DAY 回應轉成 list[dict]；非 OK 或無 data 回 []。"""
    if j.get("stat") != "OK" or "data" not in j:
        return []
    rows = []
    for row in j["data"]:
        # row: 日期, 成交股數, 成交金額, 開盤, 最高, 最低, 收盤, 漲跌, 成交筆數
        try:
            parts = row[0].split("/")
            y = int(parts[0]) + 1911
            date = pd.Timestamp(f"{y}-{parts[1]}-{parts[2]}")
            rows.append({
                "Date": date,
                "Open": float(row[3].replace(",", "")),
                "High": float(row[4].replace(",", "")),
                "Low": float(row[5].replace(",", "")),
                "Close": float(row[6].replace(",", "")),
                "Volume": float(row[1].replace(",", "")),
            })
        except (ValueError, IndexError):
            continue
    return rows


def fetch_daily(code, months=6, today=None):
    """抓最近 months 個月日線；回 DataFrame(index=Date, 含 MA20)；抓不到回空。"""
    today = today or datetime.today()
    frames = []
    for i in range(months):
        if i:
            time.sleep(TWSE_DELAY)
        d = today - relativedelta(months=i)
        ym = d.strftime("%Y%m01")
        url = (
            "https://www.twse.com.tw/exchangeReport/STOCK_DAY"
            f"?response=json&date={ym}&stockNo={code}"
        )
        try:
            r = requests.get(url, headers=HEADERS, timeout=10)
            frames.extend(parse_twse_json(r.json()))
        except Exception:
            continue
    if not frames:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume", "MA20"])
    df = (
        pd.DataFrame(frames)
        .drop_duplicates("Date")
        .sort_values("Date")
        .set_index("Date")
    )
    df["MA20"] = df["Close"].rolling(20).mean()
    return df


def parse_index_json(j):
    """TWSE MI_5MINS_HIST 回應 -> list[dict]；非 OK/無 data 回 []。"""
    if j.get("stat") != "OK" or "data" not in j:
        return []
    rows = []
    for row in j["data"]:
        # row: 日期, 開盤指數, 最高指數, 最低指數, 收盤指數
        try:
            parts = row[0].split("/")
            y = int(parts[0]) + 1911
            date = pd.Timestamp(f"{y}-{parts[1]}-{parts[2]}")
            rows.append({
                "Date": date,
                "Open": float(row[1].replace(",", "")),
                "High": float(row[2].replace(",", "")),
                "Low": float(row[3].replace(",", "")),
                "Close": float(row[4].replace(",", "")),
            })
        except (ValueError, IndexError):
            continue
    return rows


def fetch_index(months=6, today=None):
    """抓加權指數近 months 個月日線；回 DataFrame(含 MA20)；抓不到回空(帶 schema)。"""
    today = today or datetime.today()
    frames = []
    for i in range(months):
        if i:
            time.sleep(TWSE_DELAY)
        d = today - relativedelta(months=i)
        ym = d.strftime("%Y%m01")
        url = (
            "https://www.twse.com.tw/indicesReport/MI_5MINS_HIST"
            f"?response=json&date={ym}"
        )
        try:
            r = requests.get(url, headers=HEADERS, timeout=10)
            frames.extend(parse_index_json(r.json()))
        except Exception:
            continue
    if not frames:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "MA20"])
    df = (
        pd.DataFrame(frames)
        .drop_duplicates("Date").sort_values("Date").set_index("Date")
    )
    df["MA20"] = df["Close"].rolling(20).mean()
    return df
