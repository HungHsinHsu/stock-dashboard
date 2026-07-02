import time
import pandas as pd
import requests
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta

HEADERS = {"User-Agent": "Mozilla/5.0 (stock-dashboard)"}

# 每次打 TWSE 之間的禮貌間隔（秒），避免多檔時瞬間連發被限流/封 IP。
TWSE_DELAY = 0.3

# 追蹤清單一律由使用者自行管理（網頁「⭐ 管理追蹤」或機器人 /add /remove），
# 不再寫死任何『預設股』——故此處為空。舊的預設種子股(華邦電)已改由 db 遷移
# 一次性種進 admin 清單，之後可自由增刪。
# 三段支撐一律用均線（支撐1＝MA5、支撐2＝MA20、支撐3＝MA60 季線），每日自動重算。
STOCKS = {}


def fetch_stock_name(code, today=None):
    """用代號查中文股名（例 2330 -> 台積電、0050 -> 元大台灣50）；查不到回 None。

    STOCK_DAY 個股/ETF 皆可查；但當月月初盤前可能還沒有資料，
    故往前找最多 3 個月，直到某月標題含有股名。ETF 也走這條路解析。
    """
    today = today or datetime.today()
    for i in range(3):
        d = today - relativedelta(months=i)
        ym = d.strftime("%Y%m01")
        url = (
            "https://www.twse.com.tw/exchangeReport/STOCK_DAY"
            f"?response=json&date={ym}&stockNo={code}"
        )
        try:
            title = requests.get(url, headers=HEADERS, timeout=10).json().get("title", "")
        except Exception:
            continue
        # 例： "115年06月 2330 台積電 各日成交資訊"
        parts = title.split()
        if code in parts:
            j = parts.index(code)
            if j + 1 < len(parts):
                return parts[j + 1]
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


def _taifex_pick_row(data):
    """從 TAIFEX 期貨每日行情挑出台指期(TX)近月(成交量最大)那一列；無則 None。"""
    tx = [
        r for r in data
        if str(_pick(r, ("Contract", "契約", "商品代號", "FUTURES_ID")) or "")
        .strip().upper() == "TX"
    ]
    if not tx:
        return None
    # 同商品有多個到期月，取成交量最大者(近月最活躍)
    tx.sort(key=lambda r: _num(_pick(r, ("Volume", "成交量"))) or 0, reverse=True)
    return tx[0]


def _row_change_pct(row):
    """從單列取漲跌%：優先 %Change 欄，否則用漲跌價/收盤回推；失敗回 None。"""
    pct = _num(_pick(row, ("%Change", "Change%", "漲跌%", "漲跌百分比", "漲跌百分比(%)")))
    if pct is None:
        last = _num(_pick(row, ("Last", "收盤價", "最後成交價", "SettlementPrice", "結算價")))
        chg = _num(_pick(row, ("Change", "漲跌價", "漲跌")))
        if last is not None and chg is not None and (last - chg):
            pct = chg / (last - chg) * 100
    return round(pct, 2) if pct is not None else None


def _row_date(row):
    """把 TAIFEX 一列的交易日期正規化成 'YYYY-MM-DD'；無法解析回 None。"""
    raw = _pick(row, ("Date", "日期", "交易日期", "TradeDate"))
    if raw is None:
        return None
    s = str(raw).strip().replace("/", "-")
    digits = s.replace("-", "")
    if len(digits) == 8 and digits.isdigit():           # 20260701
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    parts = s.split("-")                                # 2026-7-1 → 補零
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        y, m, d = parts
        return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
    return None


def _taifex_change(data):
    """從 TAIFEX 期貨每日行情(list[dict])解析台指期(TX)近月漲跌%；失敗回 None。"""
    row = _taifex_pick_row(data)
    if row is None:
        return None
    pct = _row_change_pct(row)
    if pct is None:
        print("台指期解析失敗，樣本欄位：", list(row.keys()))
    return pct


def fetch_taifex_detail(min_date=None):
    """台指期(TX)近月：優先夜盤(盤後，已含美股隔夜)，其次一般盤。
    回 {'pct': float, 'date': 'YYYY-MM-DD'|None}；抓不到回 None。

    新鮮度防呆：給了 min_date 時，若報表日期可解析且『早於 min_date』(抓到的是
    上一場舊資料，今早那場還沒發布)，視為過時 → 丟棄不用。寧可回報無資料，
    也不要拿一個過時的漲/跌方向去誤導今天的預測。"""
    for url in TAIFEX_URLS:
        try:
            data = requests.get(url, headers=HEADERS, timeout=15).json()
        except Exception:
            continue
        if not isinstance(data, list) or not data:
            continue
        row = _taifex_pick_row(data)
        if row is None:
            continue
        pct = _row_change_pct(row)
        if pct is None:
            print("台指期解析失敗，樣本欄位：", list(row.keys()))
            continue
        d = _row_date(row)
        if min_date and d and d < str(min_date):
            print(f"台指期資料過時({d} < {min_date})，丟棄不用：{url.split('/')[-1]}")
            continue
        return {"pct": pct, "date": d}
    return None


def fetch_taifex(min_date=None):
    """台指期(TX)近月漲跌%（含新鮮度防呆）；抓不到或過時回 None。"""
    d = fetch_taifex_detail(min_date=min_date)
    return d["pct"] if d else None


T86_URL = "https://www.twse.com.tw/fund/T86"


def _foreign_net_from_t86(j, code):
    """從 T86 回應取某股『外資買賣超股數』；非 OK 回 None，當日該股無紀錄回 0。"""
    if j.get("stat") != "OK":
        return None
    fields = j.get("fields") or []
    idx = None
    for i, f in enumerate(fields):      # 優先：外陸資買賣超股數(不含外資自營商)
        if "外" in f and "買賣超" in f and "自營商" not in f:
            idx = i
            break
    if idx is None:
        for i, f in enumerate(fields):  # 後備：任何含「外…買賣超」
            if "外" in f and "買賣超" in f:
                idx = i
                break
    if idx is None:
        return None
    for row in j.get("data", []):
        try:
            if str(row[0]).strip() == str(code):
                return int(str(row[idx]).replace(",", "").strip())
        except (ValueError, IndexError):
            return None
    return 0  # 當日有開市但該股不在三大法人買賣超名單 → 視為外資沒動


def _t86_col(fields, row, match_all, match_none=()):
    """依欄名關鍵字取 row 的整數值（股數）；找不到回 None。"""
    for i, f in enumerate(fields):
        if all(m in f for m in match_all) and not any(x in f for x in match_none):
            try:
                return int(str(row[i]).replace(",", "").strip())
            except (ValueError, IndexError, TypeError):
                return None
    return None


def _legal_extra_from_t86(j, code):
    """取某股 投信/自營商/三大法人合計 買賣超股數（外資另由 _foreign_net_from_t86 取）。
    回 dict（鍵可能 None）；非 OK/找不到回 {}。"""
    if not j or j.get("stat") != "OK":
        return {}
    fields = j.get("fields") or []
    for row in j.get("data", []):
        try:
            if str(row[0]).strip() != str(code):
                continue
        except (IndexError, TypeError):
            continue
        return {
            "trust": _t86_col(fields, row, ["投信", "買賣超"]),
            # 自營商合計：排除「自行/避險」分項，且排除「外資自營商」
            "dealer": _t86_col(fields, row, ["自營商", "買賣超"], ["自行", "避險", "外"]),
            "total": _t86_col(fields, row, ["三大法人", "買賣超"]),
        }
    return {}


def fetch_foreign_flow(code, today=None, max_back=8):
    """近幾個交易日法人對該股買賣超(股數，新到舊)。

    回 {"net": 外資最近一日, "sold_streak": 外資連續賣超天數, "stopped": bool|None,
        "date": 'YYYY-MM-DD'|None,
        "trust_net": 投信最近一日, "dealer_net": 自營商, "total_net": 三大法人合計}。
    stopped=外資最近一日是否未賣超(>=0)。抓不到回相關值 None。
    """
    today = today or datetime.today()
    nets, last_date, dbg, extra = [], None, None, {}
    d, checked = today, 0
    while len(nets) < 3 and checked < max_back:
        ymd = d.strftime("%Y%m%d")
        # T86＝三大法人買賣超日報；全部個股的 selectType 是 ALLBUT0999（非 ALL）
        url = f"{T86_URL}?response=json&date={ymd}&selectType=ALLBUT0999"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            j = resp.json()
        except Exception as e:
            dbg = f"{ymd} 例外 {type(e).__name__}: {e}"
            j = {}
        else:
            if j.get("stat") != "OK":
                body = (resp.text or "")[:160].replace("\n", " ")
                dbg = f"{ymd} http={getattr(resp, 'status_code', '?')} stat={j.get('stat')!r} body={body!r}"
        if j.get("stat") == "OK":
            net = _foreign_net_from_t86(j, code)
            if net is not None:
                nets.append(net)
                if last_date is None:
                    last_date = d.strftime("%Y-%m-%d")
                    extra = _legal_extra_from_t86(j, code)   # 同一日抓投信/自營/合計
        checked += 1
        d -= timedelta(days=1)
        time.sleep(TWSE_DELAY)
    if not nets:
        print(f"法人資料抓不到({code})：{dbg}")
        return {"net": None, "sold_streak": 0, "stopped": None, "date": None,
                "trust_net": None, "dealer_net": None, "total_net": None}
    streak = 0
    for n in nets:
        if n < 0:
            streak += 1
        else:
            break
    return {"net": nets[0], "sold_streak": streak,
            "stopped": nets[0] >= 0, "date": last_date,
            "trust_net": extra.get("trust"), "dealer_net": extra.get("dealer"),
            "total_net": extra.get("total")}


MARGIN_URL = "https://www.twse.com.tw/exchangeReport/MI_MARGN"


def _margin_row(j, code):
    """從 MI_MARGN 找某股 row(list)；相容 tables / 舊 data 格式；找不到回 None。"""
    blocks = j.get("tables") if isinstance(j.get("tables"), list) else [j]
    for t in blocks:
        for row in (t.get("data") or []):
            try:
                if str(row[0]).strip() == str(code):
                    return row
            except (IndexError, TypeError):
                continue
    return None


def fetch_margin(code, today=None, max_back=8):
    """該股最新一日融資融券。回
    {"margin_bal": 融資今日餘額, "margin_chg": 融資餘額增減(今-昨),
     "short_bal": 融券今日餘額, "date": 'YYYY-MM-DD'|None}；抓不到各值 None。

    MI_MARGN 個股表欄位固定順序：0代號 1名稱 2融資買進 3融資賣出 4現金償還
    5融資前日餘額 6融資今日餘額 7融資限額 8融券買進 9融券賣出 10現券償還
    11融券前日餘額 12融券今日餘額 ...（單位：仟股/張）
    """
    def _int(row, i):
        try:
            return int(str(row[i]).replace(",", "").strip())
        except (ValueError, IndexError, TypeError):
            return None

    today = today or datetime.today()
    d, checked = today, 0
    while checked < max_back:
        ymd = d.strftime("%Y%m%d")
        url = f"{MARGIN_URL}?response=json&date={ymd}&selectType=ALL"
        try:
            j = requests.get(url, headers=HEADERS, timeout=15).json()
        except Exception:
            j = {}
        if j.get("stat") == "OK":
            row = _margin_row(j, code)
            if row is not None and len(row) >= 13:
                bal, prev, short_bal = _int(row, 6), _int(row, 5), _int(row, 12)
                chg = bal - prev if (bal is not None and prev is not None) else None
                return {"margin_bal": bal, "margin_chg": chg,
                        "short_bal": short_bal, "date": d.strftime("%Y-%m-%d")}
        checked += 1
        d -= timedelta(days=1)
        time.sleep(TWSE_DELAY)
    return {"margin_bal": None, "margin_chg": None, "short_bal": None, "date": None}


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


def fetch_top_turnover(n=150):
    """當日成交金額前 n 檔（一般個股 4 碼 + ETF 00 開頭），回 [(code, name), ...]。
    用 STOCK_DAY_ALL 單次抓全市場，便宜；抓不到回 []。"""
    try:
        data = requests.get(
            "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
            headers=HEADERS, timeout=20,
        ).json()
    except Exception:
        return []
    rows = []
    for r in data if isinstance(data, list) else []:
        code = str(r.get("Code") or "").strip()
        name = str(r.get("Name") or "").strip()
        tv = _num(r.get("TradeValue"))
        if not code or not name or tv is None:
            continue
        if not ((code.isdigit() and len(code) == 4) or code.startswith("00")):
            continue                       # 排除權證等非個股/ETF
        rows.append((tv, code, name))
    rows.sort(reverse=True)
    return [(c, nm) for _, c, nm in rows[:n]]


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


def _fetch_stock_month(code, d):
    """抓某代號某月的日線，回 list[dict]；失敗回 []。"""
    ym = d.strftime("%Y%m01")
    url = (
        "https://www.twse.com.tw/exchangeReport/STOCK_DAY"
        f"?response=json&date={ym}&stockNo={code}"
    )
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        return parse_twse_json(r.json())
    except Exception:
        return []


def fetch_daily(code, months=6, today=None, workers=6):
    """抓最近 months 個月日線；回 DataFrame(index=Date, 含 MA20)；抓不到回空。

    各月為獨立請求，預設用小型執行緒池平行抓，首次載入由數十秒縮到數秒；
    workers<=1 則退回循序（含 TWSE_DELAY 間隔）以維持保守行為。
    """
    today = today or datetime.today()
    dates = [today - relativedelta(months=i) for i in range(months)]
    frames = []
    if workers and workers > 1 and months > 1:
        with ThreadPoolExecutor(max_workers=min(workers, months)) as ex:
            for fr in ex.map(lambda d: _fetch_stock_month(code, d), dates):
                frames.extend(fr)
    else:
        for i, d in enumerate(dates):
            if i:
                time.sleep(TWSE_DELAY)
            frames.extend(_fetch_stock_month(code, d))
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


def _fetch_index_month(d):
    """抓加權指數某月日線，回 list[dict]；失敗回 []。"""
    ym = d.strftime("%Y%m01")
    url = (
        "https://www.twse.com.tw/indicesReport/MI_5MINS_HIST"
        f"?response=json&date={ym}"
    )
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        return parse_index_json(r.json())
    except Exception:
        return []


def fetch_index(months=6, today=None, workers=6):
    """抓加權指數近 months 個月日線；回 DataFrame(含 MA20)；抓不到回空(帶 schema)。

    與 fetch_daily 相同：各月獨立請求，預設平行抓以加快載入。
    """
    today = today or datetime.today()
    dates = [today - relativedelta(months=i) for i in range(months)]
    frames = []
    if workers and workers > 1 and months > 1:
        with ThreadPoolExecutor(max_workers=min(workers, months)) as ex:
            for fr in ex.map(_fetch_index_month, dates):
                frames.extend(fr)
    else:
        for i, d in enumerate(dates):
            if i:
                time.sleep(TWSE_DELAY)
            frames.extend(_fetch_index_month(d))
    if not frames:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "MA20"])
    df = (
        pd.DataFrame(frames)
        .drop_duplicates("Date").sort_values("Date").set_index("Date")
    )
    df["MA20"] = df["Close"].rolling(20).mean()
    return df
