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


def _vacuum_uptrend():
    # 緩漲、位置偏高（在支撐之上、未回檔）→ 觀望，但趨勢沒破，應列出並標『觀望』
    closes = [100 + i for i in range(60)]
    return _df(closes)


def test_scan_ranks_entry_first_broken_last_but_still_listed():
    data = {"2330": _pullback_hold_shrink(), "9999": _still_falling()}
    out = scan(["9999", "2330"], fetch=lambda c: data.get(c),
               foreign_lookup=lambda c: {"stopped": True})
    codes = [x["code"] for x in out]
    assert codes[0] == "2330" and out[0]["signal"] == "進場"   # 進場排最前
    # 跌破季線的也『仍列出』(在後面)、標避開，不再一片空白
    assert "9999" in codes
    assert out[codes.index("9999")]["signal"] == "避開"


def test_scan_reports_trend():
    # 每個候選都帶『波段體質』(均線排列＋是否站上季線)
    out = scan(["2330"], fetch=lambda c: _pullback_hold_shrink(),
               foreign_lookup=lambda c: {"stopped": True})
    assert out and "排列" in out[0]["trend"] and "季線" in out[0]["trend"]


def test_scan_stocks_ranked_before_etfs():
    # 個股優先、ETF 分開放：就算 ETF 是『順勢偏多』(高分)，個股(即使只是觀望)也排在 ETF 前面
    data = {"0050": _vacuum_uptrend(), "8888": _vacuum_uptrend()}
    out = scan(["0050", "8888"], fetch=lambda c: data[c])
    assert [x["kind"] for x in out] == ["個股", "ETF"]   # 個股在前、ETF 收在後
    assert out[0]["code"] == "8888" and out[1]["code"] == "0050"


def test_scan_etf_limit_caps_etf_section():
    # etf_limit 只截 ETF 段，不影響個股
    data = {"0050": _vacuum_uptrend(), "0056": _vacuum_uptrend(), "8888": _vacuum_uptrend()}
    out = scan(["0050", "0056", "8888"], fetch=lambda c: data[c], etf_limit=1)
    assert [x["kind"] for x in out] == ["個股", "ETF"]   # 兩檔 ETF 被截成 1 檔


def test_scan_orders_watch_by_proximity_to_entry():
    # 同是觀望：到支撐、站穩、量縮(較接近進場)的排在『位置偏高沒回檔』的前面
    data = {"NEAR": _pullback_hold_shrink(), "FAR": _vacuum_uptrend()}
    out = scan(["FAR", "NEAR"], fetch=lambda c: data[c])   # 不給外資→都觀望
    assert [x["signal"] for x in out] == ["觀望", "觀望"]
    assert out[0]["code"] == "NEAR"                        # 越接近進場排越前


def test_scan_excludes_denylist():
    # 禁區股(群創 3481)本來就不玩 → 不列
    out = scan(["3481"], fetch=lambda c: _pullback_hold_shrink())
    assert out == []


def test_scan_confirms_foreign_downgrades_entry():
    # 技術面到位(無外資=進場)，但補查外資發現仍賣超 → 降為觀望
    df = _pullback_hold_shrink()
    out = scan(["2330"], fetch=lambda c: df,
               foreign_lookup=lambda c: {"stopped": False, "sold_streak": 3})
    assert out[0]["signal"] == "觀望" and "外資仍在賣超" in out[0]["reason"]


def test_scan_foreign_stopped_keeps_entry():
    df = _pullback_hold_shrink()
    out = scan(["2330"], fetch=lambda c: df,
               foreign_lookup=lambda c: {"stopped": True})
    assert out[0]["signal"] == "進場" and "外資已停止倒貨" in out[0]["reason"]


def test_scan_still_lists_watch_when_no_entry():
    # 就算沒有『進場』，觀望但趨勢沒破的也要列出，並標上訊號
    out = scan(["8888"], fetch=lambda c: _vacuum_uptrend())
    assert len(out) == 1
    assert out[0]["signal"] in ("觀望", "進場")   # 有標訊號
    assert out[0]["signal"] == "觀望"


def test_scan_entry_ranks_above_watch():
    # 進場分數高於觀望 → 進場排前面
    data = {"E": _pullback_hold_shrink(), "W": _vacuum_uptrend()}
    out = scan(["W", "E"], fetch=lambda c: data[c], limit=10,
               foreign_lookup=lambda c: {"stopped": True})
    assert out[0]["code"] == "E" and out[0]["signal"] == "進場"


def test_scan_excludes_when_foreign_data_missing():
    # 資料要齊：查不到外資的個股 → 整檔剔除，不推薦資料不全的標的
    df = _pullback_hold_shrink()
    assert scan(["2330"], fetch=lambda c: df,
                foreign_lookup=lambda c: {"stopped": None}) == []
    assert scan(["2330"], fetch=lambda c: df,
                foreign_lookup=lambda c: None) == []


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
