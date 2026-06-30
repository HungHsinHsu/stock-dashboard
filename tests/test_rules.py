from core.rules import signal_ceiling, constrain_signal, entry_setup, is_denied


# 華邦電快照：支撐1≈222、MA20≈181、支撐3≈142。close 用「距 % 」表示位置。

def test_denylist_blocks_entry():
    assert is_denied("3481") and is_denied("00631L")
    ind = {"close": 100, "ma20": 90, "dist_support1_pct": 1.0, "dist_support3_pct": 30}
    assert signal_ceiling(ind, code="3481") == "避開"


def test_below_support3_is_stop_loss_avoid():
    # 收盤跌破支撐3(長期均線) → 停損 → 避開
    ind = {"close": 138, "prev_close": 145, "ma20": 160,
           "dist_support1_pct": -38, "dist_support3_pct": -3.5, "vol_ratio": 1.4}
    assert signal_ceiling(ind) == "避開"


def test_pullback_to_support_with_shrink_volume_allows_entry():
    # 情境一：回檔到支撐1(±2%內)、收盤止穩(close>=prev)、量縮 → 進場
    ind = {"close": 223, "prev_close": 222, "ma20": 181,
           "dist_support1_pct": 0.5, "dist_support3_pct": 57, "vol_ratio": 0.8}
    s = entry_setup(ind)
    assert s["ceiling"] == "進場" and "支撐1" in s["at_batch"]


def test_at_support_but_volume_not_shrunk_is_watch():
    # 到價但放量(量未縮) → 觀望，等收盤確認
    ind = {"close": 223, "prev_close": 222, "ma20": 181,
           "dist_support1_pct": 0.5, "dist_support3_pct": 57, "vol_ratio": 1.3}
    assert signal_ceiling(ind) == "觀望"


def test_at_support_but_not_stabilised_is_watch():
    # 到價但收盤還在破底(close<prev) → 觀望
    ind = {"close": 218, "prev_close": 230, "ma20": 181,
           "dist_support1_pct": -1.8, "dist_support3_pct": 53, "vol_ratio": 0.7}
    assert signal_ceiling(ind) == "觀望"


def test_vacuum_zone_high_position_is_watch_not_entry():
    # 位置偏高、不在任一支撐(真空帶) → 觀望（不是因為在支撐上方就追進）
    ind = {"close": 206, "prev_close": 204, "ma20": 181,
           "dist_support1_pct": -7.2, "dist_support3_pct": 45, "vol_ratio": 0.8}
    assert signal_ceiling(ind) == "觀望"


def test_scenario_two_reclaim_ma20_with_volume():
    # 情境二：帶量站回上方均線、收盤站穩、非空頭 → 進場
    ind = {"close": 184, "prev_close": 180, "ma20": 181, "ma_align": "糾結",
           "dist_support1_pct": -17, "dist_support3_pct": 29, "vol_ratio": 1.6}
    assert signal_ceiling(ind) == "進場"


def test_constrain_caps_llm_entry_when_not_setup():
    # LLM 喊進場但位置在真空帶 → 夾回觀望
    ind = {"close": 206, "prev_close": 204, "ma20": 181,
           "dist_support1_pct": -7.2, "dist_support3_pct": 45, "vol_ratio": 0.8}
    final, note = constrain_signal({"signal": "進場"}, ind)
    assert final == "觀望" and note


def test_constrain_keeps_valid_entry_and_warns_foreign():
    ind = {"close": 223, "prev_close": 222, "ma20": 181,
           "dist_support1_pct": 0.5, "dist_support3_pct": 57, "vol_ratio": 0.8}
    final, note = constrain_signal({"signal": "進場"}, ind)
    assert final == "進場" and "外資" in note  # 進場仍提醒人工確認外資與盤後定價
