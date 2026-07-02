import pandas as pd
from core.screener import scan


def _df(closes, vols=None):
    n = len(closes)
    idx = pd.date_range(end="2026-07-02", periods=n, freq="D")
    vols = vols or [1000.0] * n
    df = pd.DataFrame({"Open": closes, "High": [c + 1 for c in closes],
                       "Low": [c - 1 for c in closes], "Close": closes,
                       "Volume": vols}, index=idx)
    df["MA20"] = df["Close"].rolling(20).mean()
    return df


def _pullback_hold_shrink():
    # 先漲一段(拉開均線)，最後回檔到接近短均線、收紅站穩、量縮 → 應判進場
    closes = [100 + i * 0.5 for i in range(60)]      # 緩漲 60 天
    closes[-1] = closes[-2] + 0.3                    # 最後一天小紅(站穩)
    vols = [1000.0] * 60
    vols[-1] = 500.0                                 # 量縮
    return _df(closes, vols)


def _still_falling():
    closes = [200 - i for i in range(60)]            # 一路跌 → 跌破長均線(停損區)
    return _df(closes)


def test_scan_picks_entry_and_skips_others():
    data = {"2330": _pullback_hold_shrink(), "9999": _still_falling()}
    out = scan(["2330", "9999"], fetch=lambda c: data.get(c))
    codes = [x["code"] for x in out]
    assert "2330" in codes            # 承接點候選有被挑出
    assert "9999" not in codes        # 跌破長均線(停損)不入選


def test_scan_skips_short_history_and_missing():
    data = {"1111": _df([10.0] * 5)}   # 資料太短(<60) → 略過
    out = scan(["1111", "2222"], fetch=lambda c: data.get(c))  # 2222 抓不到
    assert out == []


def test_scan_limit_and_sorted_by_volume_shrink():
    # 兩檔都進場，量比較小(量縮更明顯)的排前面
    a = _pullback_hold_shrink()
    b = _pullback_hold_shrink()
    b.iloc[-1, b.columns.get_loc("Volume")] = 200.0   # b 量縮更兇
    out = scan(["AAAA", "BBBB"], fetch=lambda c: {"AAAA": a, "BBBB": b}[c], limit=1)
    assert len(out) == 1 and out[0]["code"] == "BBBB"
