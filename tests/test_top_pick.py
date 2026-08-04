"""今日首選：把一堆候選收斂成「明天掛多少」的單一決定。

硬門檻要能擋掉的三種情況，都是本專案真的踩過的：
  ・月線走平/下彎 → 高檔回落，不是上升趨勢中的回檔（仁寶、晶豪科那型）
  ・位階偏高 → 訊號對但買在區間頂（長榮航 89.6%）
  ・停損太遠 → 期望值被吃掉（友訊距季線 17.5%）
"""
from jobs.screen import _top_pick, _limit_price, _stop_pct
from core.fundamentals import valuation_notes


def _c(code, **kw):
    base = {
        "code": code, "kind": "個股", "signal": "進場",
        "at_batch": "支撐1(第一批)", "trend": "多頭排列", "reason": "回檔到支撐1",
        "close": 100.0, "ma5": 100.0, "ma20": 98.0, "ma60": 96.0,
        "ma20_slope5": 1.0, "vol_ratio": 0.5, "pos_pct": 40.0,
    }
    base.update(kw)
    return base


def test_limit_price_and_stop():
    lp, sup, name = _limit_price(_c("1111"))
    assert abs(lp - 102.0) < 1e-9 and sup == 100.0 and name == "支撐1"
    # 掛 102、季線 96 → (102-96)/102 = 5.88%
    assert abs(_stop_pct(102.0, 96.0) - 5.882352941) < 1e-6
    assert _stop_pct(102.0, None) is None
    assert _stop_pct(95.0, 96.0) is None        # 掛單價已在季線下 → 不算


def test_pick_rejects_flat_month_line_high_position_and_far_stop():
    cands = [
        _c("1111", ma20_slope5=-0.5),                     # 月線下彎
        _c("2222", pos_pct=85.0),                         # 位階偏高
        _c("3333", ma60=80.0),                            # 停損 (102-80)/102 = 21.6%
        _c("4444", signal="觀望"),                        # 非進場
        _c("5555", kind="ETF"),                           # ETF 走另一套
    ]
    out = "\n".join(_top_pick(cands, {}))
    assert "不出手" in out
    assert "月線走平" in out and "位階" in out and "太遠" in out


def test_pick_ranks_by_stop_distance_then_position():
    # A 停損較遠、B 停損較近 → 選 B（風險報酬優先，不是誰漲得快）
    a = _c("AAAA", ma60=90.0, pos_pct=20.0)     # (102-90)/102 = 11.8% → 超過門檻被刷
    b = _c("BBBB", ma60=97.0, pos_pct=60.0)     # (102-97)/102 = 4.9%
    c = _c("CCCC", ma60=98.0, pos_pct=65.0)     # (102-98)/102 = 3.9% ← 最近
    out = "\n".join(_top_pick([a, b, c], {"BBBB": "乙", "CCCC": "丙"}))
    assert "🥇 丙 (CCCC)" in out
    assert "次選：乙" in out
    assert "掛單 ≤102.00" in out and "風險 −3.9%" in out


def test_pick_includes_valuation_when_available():
    out = "\n".join(_top_pick([_c("6666")], {}, {"6666": {"pe": 15.0, "yield": 5.0,
                                                          "pb": 2.0}}))
    assert "本益比 15.0" in out and "殖利率 5.00%" in out
    # 沒有估值資料時要明講，不能靜默省略
    assert "無估值資料" in "\n".join(_top_pick([_c("7777")], {}))


def test_valuation_notes_flags_cheap_and_rich():
    assert "偏高" in "".join(valuation_notes({"pe": 55.0}))
    assert "假便宜" in "".join(valuation_notes({"pe": 6.0}))
    assert valuation_notes(None) == []
