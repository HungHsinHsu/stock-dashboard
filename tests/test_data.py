from core.data import parse_twse_json, STOCKS


def test_parse_twse_json_ok():
    j = {
        "stat": "OK",
        "data": [
            ["115/06/27", "1,000", "2,000", "200.0", "210.0", "199.0", "205.0", "+1", "50"],
        ],
    }
    rows = parse_twse_json(j)
    assert len(rows) == 1
    r = rows[0]
    assert r["Open"] == 200.0 and r["High"] == 210.0
    assert r["Low"] == 199.0 and r["Close"] == 205.0
    assert r["Volume"] == 1000.0
    assert str(r["Date"].date()) == "2026-06-27"  # 民國115 -> 西元2026


def test_parse_twse_json_not_ok():
    assert parse_twse_json({"stat": "很抱歉，沒有符合條件的資料!"}) == []
    assert parse_twse_json({"stat": "OK"}) == []


def test_stocks_shape():
    assert "華邦電 (2344)" in STOCKS
    assert STOCKS["華邦電 (2344)"]["code"] == "2344"


def test_yahoo_change_parses(monkeypatch):
    import core.data as data
    fake = {"chart": {"result": [
        {"indicators": {"quote": [{"close": [100.0, 105.0]}]}}]}}

    class _R:
        @staticmethod
        def json():
            return fake

    monkeypatch.setattr(data.requests, "get", lambda *a, **k: _R())
    assert data._yahoo_change("^GSPC") == 5.0  # 105 vs 100 = +5%


def test_fetch_us_overnight(monkeypatch):
    import core.data as data
    monkeypatch.setattr(data, "_yahoo_change", lambda sym: 1.5)
    out = data.fetch_us_overnight()
    assert out["費半SOX"] == 1.5 and len(out) == 4


def test_taifex_change_picks_tx_front_month():
    from core.data import _taifex_change
    rows = [
        {"Contract": "MTX", "%Change": "0.10", "Volume": "999999"},   # 小台，須忽略
        {"Contract": "TX", "%Change": "-0.30", "Volume": "1000"},      # 遠月，量小
        {"Contract": "TX", "%Change": "0.85", "Volume": "120000"},     # 近月，量最大
    ]
    assert _taifex_change(rows) == 0.85


def test_taifex_change_computes_from_change_price():
    from core.data import _taifex_change
    # 沒有 %Change 欄位時，用 漲跌價/收盤回推：(100/(20050-100))*100 ≈ 0.5
    rows = [{"Contract": "TX", "Last": "20050", "Change": "100", "Volume": "5"}]
    assert _taifex_change(rows) == 0.5


def test_taifex_change_chinese_keys():
    from core.data import _taifex_change
    rows = [{"契約": "TX", "漲跌%": "-0.42", "成交量": "8000"}]
    assert _taifex_change(rows) == -0.42


def test_taifex_change_no_tx():
    from core.data import _taifex_change
    assert _taifex_change([{"Contract": "MTX", "%Change": "1.0"}]) is None


def test_foreign_net_from_t86_picks_foreign_col():
    from core.data import _foreign_net_from_t86
    j = {"stat": "OK",
         "fields": ["證券代號", "證券名稱",
                    "外陸資買賣超股數(不含外資自營商)", "投信買賣超股數",
                    "三大法人買賣超股數"],
         "data": [["2344", "華邦電", "-1,234,000", "5,000", "-1,000,000"],
                  ["2330", "台積電", "2,000,000", "0", "2,000,000"]]}
    assert _foreign_net_from_t86(j, "2344") == -1234000
    assert _foreign_net_from_t86(j, "2330") == 2000000
    assert _foreign_net_from_t86(j, "9999") == 0      # 開市但該股無紀錄→0
    assert _foreign_net_from_t86({"stat": "no"}, "2344") is None


def test_fetch_foreign_flow_streak_and_stopped(monkeypatch):
    import core.data as data
    # 最近三個交易日(新到舊)：+500、-300、-200 → 最近未賣超→stopped，連賣0
    seq = [
        {"stat": "OK", "fields": ["證券代號", "外陸資買賣超股數(不含外資自營商)"],
         "data": [["2344", "500,000"]]},
        {"stat": "OK", "fields": ["證券代號", "外陸資買賣超股數(不含外資自營商)"],
         "data": [["2344", "-300,000"]]},
        {"stat": "OK", "fields": ["證券代號", "外陸資買賣超股數(不含外資自營商)"],
         "data": [["2344", "-200,000"]]},
    ]
    calls = {"i": 0}

    class _R:
        def __init__(self, p):
            self._p = p

        def json(self):
            return self._p

    def fake_get(url, **k):
        p = seq[min(calls["i"], len(seq) - 1)]
        calls["i"] += 1
        return _R(p)

    monkeypatch.setattr(data, "TWSE_DELAY", 0)
    monkeypatch.setattr(data.requests, "get", fake_get)
    out = data.fetch_foreign_flow("2344", today=__import__("datetime").datetime(2026, 6, 30))
    assert out["net"] == 500000 and out["stopped"] is True and out["sold_streak"] == 0


def test_fetch_taifex_prefers_night_session(monkeypatch):
    import core.data as data

    class _R:
        def __init__(self, payload):
            self._p = payload

        def json(self):
            return self._p

    calls = []

    def fake_get(url, **k):
        calls.append(url)
        if url.endswith("FutAH"):  # 夜盤先回有效資料
            return _R([{"Contract": "TX", "%Change": "1.23", "Volume": "100"}])
        return _R([{"Contract": "TX", "%Change": "9.99", "Volume": "100"}])

    monkeypatch.setattr(data.requests, "get", fake_get)
    assert data.fetch_taifex() == 1.23           # 用夜盤，不退到一般盤
    assert calls and calls[0].endswith("FutAH")  # 夜盤優先


def test_fetch_stock_name_etf_falls_back_to_prev_month(monkeypatch):
    import pandas as pd
    import core.data as data

    calls = []

    class _R:
        def __init__(self, title):
            self._t = title

        def json(self):
            return {"title": self._t}

    def fake_get(url, **kw):
        calls.append(url)
        # 當月(第一次)盤前無資料 → 空標題；往前一個月才有 0050 名稱
        if len(calls) == 1:
            return _R("")
        return _R("115年06月 0050 元大台灣50 各日成交資訊")

    monkeypatch.setattr(data.requests, "get", fake_get)
    name = data.fetch_stock_name("0050", today=pd.Timestamp("2026-07-01"))
    assert name == "元大台灣50"
    assert len(calls) >= 2   # 當月抓不到有往前找


def test_resolve_stocks_etf_by_code(monkeypatch):
    import core.data as data
    # 全市場清單(STOCK_DAY_ALL)不含 ETF → 靠 fetch_stock_name 解析代號
    monkeypatch.setattr(data, "fetch_stock_name", lambda c, today=None: "元大台灣50")
    out = data.resolve_stocks("0050", listing={})
    assert out == [("0050", "元大台灣50")]


def test_fetch_daily_parallel_matches_sequential(monkeypatch):
    import pandas as pd
    import core.data as data

    def make_resp(url):
        if "202605" in url:  # 5 月
            row = ["115/05/30", "1,000", "0", "10.0", "11.0", "9.0", "10.5", "+0.1", "5"]
        else:                # 6 月
            row = ["115/06/27", "2,000", "0", "20.0", "21.0", "19.0", "20.5", "+0.1", "5"]

        class _R:
            def json(self):
                return {"stat": "OK", "data": [row]}
        return _R()

    monkeypatch.setattr(data, "TWSE_DELAY", 0)
    monkeypatch.setattr(data.requests, "get", lambda url, **k: make_resp(url))
    par = data.fetch_daily("2330", months=2, today=pd.Timestamp("2026-06-15"), workers=6)
    seq = data.fetch_daily("2330", months=2, today=pd.Timestamp("2026-06-15"), workers=1)
    assert len(par) == 2
    assert list(par.index) == list(seq.index)            # 平行與循序結果一致
    assert list(par["Close"]) == [10.5, 20.5]            # 依日期由舊到新排序
