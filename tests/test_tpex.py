"""上櫃資料源：欄位順序、單位換算、髒資料。

樣本全部取自 jobs/tpexprobe.py 在 GitHub Actions 上跑到的真實回應（世界先進 5347，
2026-08-12），不是自己編的形狀——編的樣本只能證明程式跟自己一致。
"""
import pandas as pd
import pytest

from core.tpex import (
    SHARES_PER_LOT, _insti_row_values, _norm, _roc_to_ts, fetch_tpex_top_turnover,
    parse_tpex_daily,
)

# 真實回應（截自探針 E1）
REAL_DAILY = {
    "stat": "ok", "date": "20260801", "code": "5347", "name": "世界",
    "tables": [{
        "title": "個股日成交資訊", "subtitle": "5347 世界 115年08月",
        "fields": ["日 期", "成交張數", "成交仟元", "開盤", "最高", "最低",
                   "收盤", "漲跌", "筆數"],
        "data": [
            ["115/08/11", "26,689", "4,191,211", "159.00", "159.50", "153.00",
             "158.00", "0.50", "18,519"],
            ["115/08/12", "15,395", "2,441,304", "160.50", "160.50", "156.50",
             "159.50", "1.50", "13,710"],
        ],
    }],
}


def test_parses_real_payload_with_correct_column_order():
    rows = parse_tpex_daily(REAL_DAILY)
    assert len(rows) == 2
    last = rows[-1]
    assert last["Date"] == pd.Timestamp("2026-08-12")
    # 開高低收——這四個對得上 MIS 盤中報價（開160.5 高160.5 低156.5 收159.5）
    assert (last["Open"], last["High"], last["Low"], last["Close"]) == (
        160.5, 160.5, 156.5, 159.5)


def test_volume_is_converted_from_lots_to_shares():
    """TPEx 給張、TWSE 給股。不換算會差 1000 倍，而量比是比值、看不出來。"""
    rows = parse_tpex_daily(REAL_DAILY)
    assert rows[-1]["Volume"] == 15395 * SHARES_PER_LOT == 15_395_000


def test_high_is_never_below_low_in_real_sample():
    for r in parse_tpex_daily(REAL_DAILY):
        assert r["Low"] <= r["Open"] <= r["High"]
        assert r["Low"] <= r["Close"] <= r["High"]


def test_rows_with_unparseable_price_are_dropped_not_zeroed():
    """無成交那天價格是 '--'。塞 0 進去會讓均線與位階整條算錯，比少一根嚴重。"""
    j = {"stat": "ok", "tables": [{"data": [
        ["115/08/11", "26,689", "4,191,211", "159.00", "159.50", "153.00",
         "158.00", "0.50", "18,519"],
        ["115/08/12", "0", "0", "--", "--", "--", "--", "0.00", "0"],
    ]}]}
    rows = parse_tpex_daily(j)
    assert len(rows) == 1
    assert rows[0]["Close"] == 158.0


def test_non_ok_stat_returns_empty():
    assert parse_tpex_daily({"stat": "no data", "tables": []}) == []
    assert parse_tpex_daily({}) == []
    assert parse_tpex_daily(None) == []


@pytest.mark.parametrize("s,expected", [
    ("115/08/12", "2026-08-12"),     # 單檔日線的格式
    ("1150812", "2026-08-12"),       # OpenAPI 的格式
    ("115/1/2", "2026-01-02"),       # 沒補零
])
def test_roc_dates_both_formats(s, expected):
    assert _roc_to_ts(s) == pd.Timestamp(expected)


def test_roc_garbage_returns_none():
    for s in ("", "abc", "2026-08-12", None):
        assert _roc_to_ts(s) is None


# 真實回應（截自探針 A3）：鍵名有前導空白、不一致的空格
REAL_INSTI = {
    "Date": "1150812",
    "SecuritiesCompanyCode": "5347",
    "Foreign Investors include Mainland Area Investors "
    "(Foreign Dealers excluded)-Difference": "630,000",
    "ForeignDealers-Difference": "-11,000",
    "ForeignInvestorsInclude MainlandAreaInvestors-Difference": "619,000",
    "SecuritiesInvestmentTrustCompanies-Difference": "25,000",
    "Dealers-Difference": "-3,000",
    "TotalDifference": "641,000",
}


def test_insti_picks_foreign_excluding_foreign_dealers():
    """外資有兩個版本，要取『不含外資自營商』——與 TWSE T86 的首選欄位同語意。"""
    v = _insti_row_values(REAL_INSTI)
    assert v["foreign"] == 630_000        # 不是 619,000（含自營商那版）


def test_insti_dealer_does_not_match_foreign_dealer():
    """'foreigndealers-difference' 也含 'dealers-difference'，子字串比對會拿錯。"""
    v = _insti_row_values(REAL_INSTI)
    assert v["dealer"] == -3_000          # 不是 -11,000（外資自營商）


def test_insti_trust_and_total():
    v = _insti_row_values(REAL_INSTI)
    assert v["trust"] == 25_000
    assert v["total"] == 641_000


def test_insti_tolerates_leading_and_inner_spaces():
    """鍵名多打幾個空白也要抓得到，否則會靜默回 None 而不是報錯。"""
    row = {"  Securities Investment Trust Companies - Difference  ": "7,000"}
    assert _insti_row_values(row)["trust"] == 7_000


def test_norm_strips_all_whitespace_and_lowercases():
    assert _norm(" Total  Difference ") == "totaldifference"


def test_top_turnover_sorts_by_amount_and_filters_warrants(monkeypatch):
    monkeypatch.setattr("core.tpex.fetch_tpex_quotes", lambda: [
        {"SecuritiesCompanyCode": "5347", "CompanyName": "世界",
         "TransactionAmount": "2441304000"},
        {"SecuritiesCompanyCode": "6488", "CompanyName": "環球晶",
         "TransactionAmount": "5000000000"},
        {"SecuritiesCompanyCode": "006201", "CompanyName": "元大富櫃50",
         "TransactionAmount": "22841328"},
        {"SecuritiesCompanyCode": "03001X", "CompanyName": "某某認購",
         "TransactionAmount": "9999999999"},      # 權證：要被濾掉
    ])
    got = fetch_tpex_top_turnover(10)
    assert [c for c, _, _ in got] == ["6488", "5347", "006201"]
    assert got[0][2] == 5_000_000_000.0          # 帶金額回傳，上層才併得起來


def test_cache_is_keyed_and_expires(monkeypatch):
    """全市場快照要快取（逐檔問 150 次不能下載 150 遍），但不能永久快取——
    Streamlit 是長駐行程，永久快取會讓它整天顯示同一天的資料。"""
    import core.tpex as tpex
    tpex._cache.clear()
    calls = {"a": 0, "b": 0}
    tpex._cached("a", lambda: calls.__setitem__("a", calls["a"] + 1))
    tpex._cached("a", lambda: calls.__setitem__("a", calls["a"] + 1))
    tpex._cached("b", lambda: calls.__setitem__("b", calls["b"] + 1))
    assert (calls["a"], calls["b"]) == (1, 1)          # 同 key 只算一次、不同 key 各算

    t = [1000.0]
    monkeypatch.setattr(tpex.time, "monotonic", lambda: t[0])
    tpex._cache.clear()
    tpex._cached("a", lambda: calls.__setitem__("a", calls["a"] + 1))
    t[0] += tpex._CACHE_TTL + 1
    tpex._cached("a", lambda: calls.__setitem__("a", calls["a"] + 1))
    assert calls["a"] == 3                              # 過了 TTL 要重抓
    tpex._cache.clear()


def test_universe_merges_both_boards_and_reranks(monkeypatch):
    """母體要把上市＋上櫃『合併後重排』，不是兩份各取前 n 接起來。

    接起來的話會變成 2n 檔，而且上櫃第 n 名的成交金額可能只有上市第 n 名的零頭，
    混進來就不是『前 n 大成交股』了。
    """
    import core.data as data

    class _R:
        def json(self):
            return [
                {"Code": "2330", "Name": "台積電", "TradeValue": "900"},
                {"Code": "2317", "Name": "鴻海", "TradeValue": "300"},
                {"Code": "1101", "Name": "台泥", "TradeValue": "100"},
            ]

    monkeypatch.setattr(data.requests, "get", lambda *a, **k: _R())
    monkeypatch.setattr("core.tpex.fetch_tpex_top_turnover",
                        lambda n: [("5347", "世界", 500.0), ("6488", "環球晶", 200.0)])

    got = data.fetch_top_turnover(4)
    assert got == [("2330", "台積電"), ("5347", "世界"),
                   ("2317", "鴻海"), ("6488", "環球晶")]   # 依金額交錯排序
    assert len(got) == 4                                    # 不是 4+4


def test_universe_falls_back_to_twse_when_otc_fails(monkeypatch):
    """上櫃那邊掛掉不能讓整個選股班停擺——少一半母體好過沒有母體。"""
    import core.data as data

    class _R:
        def json(self):
            return [{"Code": "2330", "Name": "台積電", "TradeValue": "900"}]

    def boom(n):
        raise RuntimeError("tpex down")

    monkeypatch.setattr(data.requests, "get", lambda *a, **k: _R())
    monkeypatch.setattr("core.tpex.fetch_tpex_top_turnover", boom)
    assert data.fetch_top_turnover(5) == [("2330", "台積電")]


def test_daily_gives_up_after_two_months_when_tpex_has_nothing(monkeypatch):
    """快速失敗：主要呼叫情境是「TWSE 抓不到，試試是不是上櫃」，多數時候答案是不是。
    跑滿 months 個月 × 每月重試會讓一檔暫時限流的上市股卡幾十秒，選股掃 150 檔會爆。"""
    import core.tpex as tpex
    calls = []
    monkeypatch.setattr(tpex, "_fetch_tpex_month", lambda c, d: calls.append(d) or [])
    df = tpex.fetch_tpex_daily("2330", months=12,
                               today=pd.Timestamp("2026-08-12"), workers=1)
    assert df.empty
    assert len(calls) == 2                      # 只探兩個月就放棄，不是 12


def test_daily_does_not_refetch_the_probed_months(monkeypatch):
    """探過的月份不重抓——否則每檔上櫃股都多打兩次。"""
    import core.tpex as tpex
    calls = []

    def fake(c, d):
        calls.append(d.strftime("%Y-%m"))
        return [{"Date": pd.Timestamp(f"{d:%Y-%m}-05"), "Open": 1.0, "High": 2.0,
                 "Low": 0.5, "Close": 1.5, "Volume": 1000.0}]

    monkeypatch.setattr(tpex, "_fetch_tpex_month", fake)
    tpex.fetch_tpex_daily("5347", months=3,
                          today=pd.Timestamp("2026-08-12"), workers=1)
    assert calls == ["2026-08", "2026-07", "2026-06"]      # 每月剛好一次
    assert len(calls) == len(set(calls))


def test_daily_falls_back_to_previous_month_at_month_start(monkeypatch):
    """月初當月還沒有交易日時，不能誤判成『這檔不是上櫃』。"""
    import core.tpex as tpex

    def fake(c, d):
        if d.month == 8:
            return []                                       # 當月還沒開始交易
        return [{"Date": pd.Timestamp("2026-07-31"), "Open": 1.0, "High": 2.0,
                 "Low": 0.5, "Close": 1.5, "Volume": 1000.0}]

    monkeypatch.setattr(tpex, "_fetch_tpex_month", fake)
    df = tpex.fetch_tpex_daily("5347", months=3,
                               today=pd.Timestamp("2026-08-01"), workers=1)
    assert not df.empty and df.index[-1] == pd.Timestamp("2026-07-31")
