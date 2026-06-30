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


def test_stooq_change_parses(monkeypatch):
    import core.data as data
    csv = ("Date,Open,High,Low,Close,Volume\n"
           "2026-06-26,100,101,99,100,0\n"
           "2026-06-27,100,106,100,105,0\n")

    class _R:
        text = csv

    monkeypatch.setattr(data.requests, "get", lambda *a, **k: _R())
    assert data._stooq_change("^spx") == (105.0, 5.0)  # 105 vs 100 = +5%


def test_fetch_us_overnight(monkeypatch):
    import core.data as data
    monkeypatch.setattr(data, "_stooq_change", lambda sym: (100.0, 1.5))
    out = data.fetch_us_overnight()
    assert out["費半SOX"] == 1.5 and len(out) == 4
