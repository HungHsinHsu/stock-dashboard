from core.rules import signal_ceiling, constrain_signal


def test_ceiling_above_support1_and_ma20_allows_entry():
    ind = {"close": 230, "ma20": 220, "dist_support1_pct": 3.6, "dist_support3_pct": 60}
    assert signal_ceiling(ind) == "進場"


def test_ceiling_vacuum_zone_caps_at_watch():
    # 破支撐1(d1<0) 但仍在 MA20 之上 → 真空帶 → 最多觀望
    ind = {"close": 215, "ma20": 210, "dist_support1_pct": -3.2, "dist_support3_pct": 50}
    assert signal_ceiling(ind) == "觀望"


def test_ceiling_below_ma20_caps_at_watch():
    ind = {"close": 205, "ma20": 220, "dist_support1_pct": -7.7, "dist_support3_pct": 44}
    assert signal_ceiling(ind) == "觀望"


def test_ceiling_below_support3_is_avoid():
    ind = {"close": 130, "ma20": 150, "dist_support1_pct": -41, "dist_support3_pct": -8.5}
    assert signal_ceiling(ind) == "避開"


def test_ceiling_no_supports_uses_ma20():
    assert signal_ceiling({"close": 100, "ma20": 90}) == "進場"
    assert signal_ceiling({"close": 80, "ma20": 90}) == "觀望"


def test_constrain_caps_entry_to_ceiling():
    ind = {"close": 205, "ma20": 220, "dist_support1_pct": -7.7, "dist_support3_pct": 44}
    final, note = constrain_signal(
        {"signal": "進場", "direction": "漲", "confidence": "中"}, ind)
    assert final == "觀望" and note  # LLM 喊進場被紀律夾回觀望


def test_constrain_down_direction_blocks_entry():
    ind = {"close": 230, "ma20": 220, "dist_support1_pct": 3.6, "dist_support3_pct": 60}
    final, note = constrain_signal(
        {"signal": "進場", "direction": "跌", "confidence": "高"}, ind)
    assert final == "觀望" and "看跌" in note


def test_constrain_low_confidence_blocks_entry():
    ind = {"close": 230, "ma20": 220, "dist_support1_pct": 3.6, "dist_support3_pct": 60}
    final, note = constrain_signal(
        {"signal": "進場", "direction": "漲", "confidence": "低"}, ind)
    assert final == "觀望" and "信心低" in note


def test_constrain_keeps_compliant_signal_untouched():
    ind = {"close": 230, "ma20": 220, "dist_support1_pct": 3.6, "dist_support3_pct": 60}
    final, note = constrain_signal(
        {"signal": "進場", "direction": "漲", "confidence": "高"}, ind)
    assert final == "進場" and note is None
