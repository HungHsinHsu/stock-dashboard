"""選股推播必須把『訊號』翻成『明天掛多少』。

實際踩過的坑：清單是收盤後算的、隔天開盤才能下單，但推播只有訊號與理由、
一個價格都沒有 → 使用者早上不知道掛什麼價，等盤中問完，好價位已經過去。
（真實案例：儒鴻 8/3 訊號收 346.5，8/4 開盤 342.5 更便宜、完全買得到，
但因為推播沒有掛單價而錯過。）
"""
from jobs.screen import _order_hint, _line

_RUHONG = {                      # 儒鴻 2026-08-03 實際數據
    "signal": "進場", "code": "1476", "kind": "個股",
    "at_batch": "支撐1(第一批)", "trend": "多頭排列",
    "close": 346.5, "ma5": 344.4, "ma20": 338.6, "ma60": 336.62,
    "reason": "回檔到支撐1、收盤止穩且量縮",
}


def test_order_hint_uses_the_matching_support():
    hint = _order_hint(_RUHONG)
    # 支撐1 → MA5 344.4，掛單上限 = 344.4 × 1.02 = 351.288
    assert "351.29" in hint and "344.40" in hint
    assert "336.62" in hint             # 季線停損
    assert "風險 −4.2%" in hint          # (351.288-336.62)/351.288 = 4.18%


def test_order_hint_picks_ma20_for_second_batch():
    x = {**_RUHONG, "at_batch": "支撐2/MA20(第二批)"}
    assert f"{338.6 * 1.02:.2f}" in _order_hint(x)


def test_order_hint_none_when_support_missing():
    assert _order_hint({**_RUHONG, "ma5": None}) is None
    assert _order_hint({**_RUHONG, "at_batch": ""}) is None


def test_line_only_gives_price_for_entry_signal():
    # 進場 → 附掛單價
    assert "明日掛單" in _line(_RUHONG, {})
    # 觀望/避開 → 不給價格（給了等於變相鼓勵進場）
    assert "明日掛單" not in _line({**_RUHONG, "signal": "觀望"}, {})
    assert "明日掛單" not in _line({**_RUHONG, "signal": "避開"}, {})
