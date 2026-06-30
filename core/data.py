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


US_INDICES = {"費半SOX": "^sox", "Nasdaq": "^ndq", "標普500": "^spx", "道瓊": "^dji"}


def _stooq_change(symbol):
    """用 stooq 日線抓某指數最近兩日收盤，回 (close, pct) 或 None。"""
    today = datetime.today()
    d2 = today.strftime("%Y%m%d")
    d1 = (today - relativedelta(days=12)).strftime("%Y%m%d")
    url = f"https://stooq.com/q/d/l/?s={symbol}&d1={d1}&d2={d2}&i=d"
    try:
        lines = requests.get(url, headers=HEADERS, timeout=15).text.strip().splitlines()
    except Exception:
        return None
    closes = []
    for row in lines[1:]:                      # 跳過表頭 Date,Open,High,Low,Close,Volume
        cols = row.split(",")
        try:
            closes.append(float(cols[4]))
        except (IndexError, ValueError):
            continue
    if len(closes) < 2:
        return None
    prev, last = closes[-2], closes[-1]
    return (last, round((last - prev) / prev * 100, 2)) if prev else None


def fetch_us_overnight():
    """美股主要指數隔夜漲跌 {name: pct}（抓不到的略過）。"""
    out = {}
    for name, sym in US_INDICES.items():
        c = _stooq_change(sym)
        if c:
            out[name] = c[1]
    return out


def fetch_taifex_night():
    """台指期夜盤（gap）— 免費穩定資料源確認中，暫回 None（不阻擋大盤預測）。"""
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
