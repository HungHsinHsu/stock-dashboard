"""價格重設事件偵測：台股有 ±10% 漲跌停，超過就不可能是交易造成的。

樣本用寶雅 5904 的真實分割（2026-08-10 面額 10→1，7/29 收 720、8/10 收 79.2）。
這個 bug 的可怕之處是它**不會報錯**——均線照算、位階照算，只是全部混到了
事件前後兩種價格，結果看起來完全正常卻毫無意義。
"""
import pandas as pd

from jobs.quote import LIMIT_PCT, corporate_action_gaps


def _df(pairs):
    """pairs = [(date_str, close), ...]"""
    idx = pd.to_datetime([d for d, _ in pairs])
    return pd.DataFrame({"Close": [c for _, c in pairs]}, index=idx)


REAL_POYA = _df([
    ("2026-07-27", 668.0), ("2026-07-28", 679.0), ("2026-07-29", 720.0),
    # 7/30~8/7 停止交易（換股），8/10 新股恢復：720 → 79.2
    ("2026-08-10", 79.2), ("2026-08-11", 87.1), ("2026-08-12", 82.5),
    ("2026-08-13", 81.8),
])


def test_detects_the_real_poya_split():
    gaps = corporate_action_gaps(REAL_POYA)
    assert len(gaps) == 1
    d0, p0, d1, p1, chg = gaps[0]
    assert (str(d0), p0) == ("2026-07-29", 720.0)
    assert (str(d1), p1) == ("2026-08-10", 79.2)
    assert chg < -85          # 1拆10 → 約 −89%


def test_normal_limit_up_and_down_are_not_flagged():
    """漲停/跌停本身是合法交易，不能被當成價格重設。"""
    df = _df([("2026-08-10", 100.0), ("2026-08-11", 110.0),   # +10.0% 漲停
              ("2026-08-12", 99.0)])                           # −10.0% 跌停
    assert corporate_action_gaps(df) == []


def test_detects_upward_reset_too():
    """減資會讓價格往上跳——那會把位階算成 100%、或假裝突破均線，方向兩邊都要抓。"""
    df = _df([("2026-08-10", 20.0), ("2026-08-11", 50.0)])     # +150%
    gaps = corporate_action_gaps(df)
    assert len(gaps) == 1 and gaps[0][4] > LIMIT_PCT


def test_clean_series_has_no_gaps():
    df = _df([(f"2026-08-{d:02d}", 100.0 + d) for d in range(3, 13)])
    assert corporate_action_gaps(df) == []


def test_window_limits_how_far_back_it_looks():
    """只看最近 n 根——一年前的除權不影響現在的 MA20。"""
    rows = [("2026-01-05", 500.0), ("2026-01-06", 50.0)]       # 舊事件
    rows += [(f"2026-08-{d:02d}", 80.0) for d in range(3, 14)]
    df = _df(rows)
    assert corporate_action_gaps(df, n=200)          # 視窗夠大時抓得到
    assert corporate_action_gaps(df, n=5) == []      # 視窗小就不該回報


def test_handles_missing_and_short_input():
    assert corporate_action_gaps(_df([("2026-08-10", 100.0)])) == []
    assert corporate_action_gaps(pd.DataFrame()) == []
    idx = pd.to_datetime(["2026-08-10", "2026-08-11"])
    assert corporate_action_gaps(pd.DataFrame({"Open": [1.0, 2.0]}, index=idx)) == []


def test_zero_price_does_not_divide_by_zero():
    df = _df([("2026-08-10", 0.0), ("2026-08-11", 80.0), ("2026-08-12", 81.0)])
    assert corporate_action_gaps(df) == []


def test_etf_uses_a_higher_threshold_because_foreign_etfs_have_no_price_limit():
    """國外成分證券 ETF／境外 ETF 沒有漲跌幅限制，±10% 會誤報成除權。

    實例（真的誤報過）：00735 國泰臺韓科技持有韓股——
      2026-02-11 收 64.95 → 02-23 收 73.5（+13.2%）：中間隔 12 天春節休市，
        台股停、韓股美股照跑，開市補跳空。
      2026-07-30 收 83.7 → 07-31 收 97.9（+17.0%）：那天加權指數自己漲 7.98%。
    兩處都是真實交易，卻被當成價格重設而把整檔從選股清單排除。
    """
    real_00735 = _df([("2026-07-30", 83.7), ("2026-07-31", 97.9),
                      ("2026-08-03", 95.5), ("2026-08-13", 103.6)])
    assert corporate_action_gaps(real_00735, code="00735") == []   # ETF：不誤報
    assert corporate_action_gaps(real_00735, code="2330")          # 個股：同樣資料會報


def test_etf_split_is_still_caught():
    """門檻放寬不能放到抓不到真的分割——1拆2 是 −50%、2合1 是 +100%，都要抓得到。"""
    assert corporate_action_gaps(_df([("2026-08-10", 100.0),
                                      ("2026-08-11", 50.0)]), code="0050")
    assert corporate_action_gaps(_df([("2026-08-10", 50.0),
                                      ("2026-08-11", 100.0)]), code="0050")


def test_etf_threshold_leaves_headroom_over_korean_limit():
    """韓股本身漲跌停 ±30%，加匯率也到不了 40%——留這段空間才不會再誤報。"""
    assert corporate_action_gaps(_df([("2026-08-10", 100.0),
                                      ("2026-08-11", 135.0)]), code="00735") == []


def test_code_none_keeps_the_stock_threshold():
    """沒給代號時維持個股行為，不要因為新參數而悄悄改變既有呼叫的結果。"""
    df = _df([("2026-08-10", 100.0), ("2026-08-11", 117.0)])
    assert corporate_action_gaps(df)                      # 預設 10% → 有報
    assert corporate_action_gaps(df, code="00735") == []  # 指名 ETF → 不報
