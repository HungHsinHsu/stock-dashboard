from core.textclean import humanize
from core.predict import format_prediction
from core.review import format_review


def test_humanize_replaces_variable_names():
    s = humanize("方向看對且hold_ma20正確，但signal給『觀望』")
    assert "hold_ma20" not in s and "signal" not in s
    assert "站穩MA20" in s and "進場訊號" in s


def test_humanize_leaves_normal_text_and_indicators():
    # 正常中文與真正的指標名（MA20、MACD）不該被動到
    assert humanize("費半 +3.8% 順風，站上 MA20") == "費半 +3.8% 順風，站上 MA20"


def test_format_prediction_humanizes_reason_and_signals():
    pred = {"signal": "觀望", "direction": "漲", "confidence": "中",
            "bull_signals": ["hold_ma20 成立"], "bear_signals": ["signal 偏空"],
            "hold_ma20": True, "hold_support1": True,
            "reason": "因此 signal 給『觀望』，且 hold_ma20 正確",
            "indicators": {"close": 200.0, "ma20": 190.0}, "market": None}
    s = format_prediction("台積電 (2330)", "2026-06-30", pred)
    assert "hold_ma20" not in s and "signal" not in s
    assert "站穩MA20" in s and "進場訊號" in s


def test_format_review_humanizes_critique():
    review = {"actual_close": 200.0, "prev_close": 198.0, "direction_actual": "漲",
              "success": True, "results": {"direction": True, "hold_ma20": True},
              "critique": "方向對且 hold_ma20 正確，『觀望』signal 下對"}
    s = format_review("台積電 (2330)", "2026-06-30", review, None)
    assert "hold_ma20" not in s and "signal" not in s
