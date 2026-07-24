import pandas as pd

from core.holdings import (
    set_holding, load_holdings, remove_holding, holding_action, position_pct,
    effective_mode, HIGH_POS_PCT,
)


# ── 聰明預設模式：ETF→長期、個股→波段、槓桿→波段、明確設定優先 ──
def test_effective_mode_defaults():
    assert effective_mode("0050", {}) == "長期"
    assert effective_mode("00830", None) == "長期"
    assert effective_mode("2330", {}) == "波段"
    assert effective_mode("00631L", {}) == "波段"       # 槓桿不預設長期
    assert effective_mode("0050", {"mode": "波段"}) == "波段"   # 明確設定優先


# ── 儲存（無 DB → 走 json 檔，用 tmp path 隔離）──
def test_store_set_load_remove(tmp_path):
    p = str(tmp_path / "h.json")
    set_holding("2408", 25, 418.0, owner="u1", path=p)
    set_holding("2618", 250, 41.5, owner="u1", path=p)
    h = load_holdings("u1", path=p)
    assert h["2408"]["shares"] == 25
    assert h["2408"]["avg_cost"] == 418.0
    assert h["2618"]["shares"] == 250
    assert remove_holding("2408", owner="u1", path=p) is True
    assert "2408" not in load_holdings("u1", path=p)
    assert remove_holding("9999", owner="u1", path=p) is False


def _ind(close, ma5, ma20, ma60, *, prev=None, vr=0.7, slope=1.0,
         align="多頭排列", d1=None, d3=None):
    return {
        "close": close, "prev_close": prev if prev is not None else close - 1,
        "ma5": ma5, "ma20": ma20, "ma60": ma60,
        "vol_ratio": vr, "ma20_slope5": slope, "ma_align": align,
        "dist_support1_pct": d1, "dist_support3_pct": d3,
    }


# ── 出場：收盤跌破季線 ──
def test_action_exit_below_season():
    ind = _ind(85, 95, 95, 90)
    r = holding_action(ind, code="2330", batches=2, avg_cost=100)
    assert r["action"] == "出場"
    assert r["pnl_pct"] < 0


# ── 續抱：站穩月線、無進場訊號 ──
def test_action_hold():
    ind = _ind(105, 104, 100, 90, align="糾結", d1=None, d3=15)
    r = holding_action(ind, code="2330", batches=1, avg_cost=100)
    assert r["action"] == "續抱"
    assert abs(r["pnl_pct"] - 5.0) < 1e-6


# ── 加倉：四關到位（到支撐1、止穩、量縮、月線上揚）＋外資停手＋批數未滿 ──
def test_action_add_when_entry_and_room():
    ind = _ind(100, 100, 95, 90, prev=99, vr=0.7, slope=1.0,
               align="多頭排列", d1=0.0, d3=11)
    r = holding_action(ind, code="2330", foreign_stopped=True, batches=0, avg_cost=98)
    assert r["action"] == "加倉"


# ── 加倉被位階擋下：同樣進場訊號但位階中上緣 → 續抱＋提醒 ──
def test_action_add_blocked_by_high_position():
    ind = _ind(100, 100, 95, 90, prev=99, vr=0.7, slope=1.0,
               align="多頭排列", d1=0.0, d3=11)
    r = holding_action(ind, code="2330", foreign_stopped=True, batches=0,
                       avg_cost=98, pos_pct=HIGH_POS_PCT + 5)
    assert r["action"] == "續抱"
    assert any("位階偏高" in a for a in r["alerts"])


# ── 加倉不觸發：外資仍在賣（foreign_stopped=False）→ 續抱 ──
def test_action_no_add_when_foreign_selling():
    ind = _ind(100, 100, 95, 90, prev=99, vr=0.7, slope=1.0,
               align="多頭排列", d1=0.0, d3=11)
    r = holding_action(ind, code="2330", foreign_stopped=False, batches=0, avg_cost=98)
    assert r["action"] == "續抱"


# ── 減碼：跌破月線、仍在季線上、三批已滿 ──
def test_action_trim_full_batches():
    ind = _ind(92, 95, 95, 90, align="糾結")
    r = holding_action(ind, code="2330", batches=3, avg_cost=100)
    assert r["action"] == "減碼"


# ── 停損接近預警：站穩月線但離季線 <3% ──
def test_stop_near_alert():
    ind = _ind(91.5, 91, 90.5, 90, align="糾結", d3=1.6)
    r = holding_action(ind, code="2330", batches=1, avg_cost=100)
    assert r["action"] == "續抱"
    assert any("接近季線停損" in a for a in r["alerts"])


# ── 槓桿 ETF：一律附上『勿長抱』警告 ──
def test_leveraged_etf_warning():
    ind = _ind(30, 29, 28, 25, align="多頭排列")
    r = holding_action(ind, code="00631L", avg_cost=28)
    assert any("再平衡耗損" in a for a in r["alerts"])


# ── 長期模式：不套個股季線停損，跌破季線不叫出場 ──
def test_long_mode_no_hard_stop_below_season():
    ind = _ind(85, 95, 95, 90, align="糾結")   # close 85 < 季線90
    r = holding_action(ind, code="0050", avg_cost=100, mode="長期")
    assert r["action"] != "出場"
    assert r["levels"]["stop"] is None
    assert any("加碼" in a for a in r["alerts"])


# ── 長期模式：回檔到月線之下 → 逢低加碼 ──
def test_long_mode_add_on_dip():
    ind = _ind(94, 95, 95, 90, align="糾結")   # close 94 <= 月線95
    r = holding_action(ind, code="0050", avg_cost=100, mode="長期")
    assert r["action"] == "加倉"


# ── 長期模式：站在月線之上（相對貴）→ 續抱、不追 ──
def test_long_mode_hold_when_extended():
    ind = _ind(105, 104, 100, 90, align="多頭排列")   # close 105 > 月線100
    r = holding_action(ind, code="0050", avg_cost=100, mode="長期")
    assert r["action"] == "續抱"


# ── 波段模式（預設）：仍套硬停損，跌破季線＝出場 ──
def test_swing_mode_still_hard_stops():
    ind = _ind(85, 95, 95, 90)
    assert holding_action(ind, code="0050", avg_cost=100, mode="波段")["action"] == "出場"


# ── 儲存操作模式（給了就存、沒給沿用舊值、預設波段）──
def test_store_mode(tmp_path):
    p = str(tmp_path / "h.json")
    set_holding("0050", 100, 100.0, mode="長期", owner="u", path=p)
    assert load_holdings("u", path=p)["0050"]["mode"] == "長期"
    set_holding("2330", 10, 500.0, owner="u", path=p)
    assert load_holdings("u", path=p)["2330"]["mode"] == "波段"
    set_holding("0050", 200, 101.0, owner="u", path=p)     # 重存不給 mode
    assert load_holdings("u", path=p)["0050"]["mode"] == "長期"


# ── 位階計算 ──
def test_position_pct():
    df = pd.DataFrame({"Close": list(range(1, 11))})   # 1..10，現價=10=最高
    assert position_pct(df) == 100.0
    df2 = pd.DataFrame({"Close": [10, 20, 15]})         # 15 在 [10,20] 的一半
    assert abs(position_pct(df2) - 50.0) < 1e-6
    assert position_pct(pd.DataFrame({"Close": [5]})) is None
