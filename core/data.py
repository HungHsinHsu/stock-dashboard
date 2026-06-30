import pandas as pd
import requests
from datetime import datetime
from dateutil.relativedelta import relativedelta

HEADERS = {"User-Agent": "Mozilla/5.0 (stock-dashboard)"}

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
