"""事件行事曆（時事第一層）：規則窗、手動事件、除權息解析、prompt/推播接線。"""
import core.events as ev
from core.predict import make_market_prediction, format_market_prediction


def _isolate_manual(tmp_path, monkeypatch):
    monkeypatch.setattr(ev, "_MANUAL_PATH", str(tmp_path / "manual.json"))


# ── 規則事件：月營收窗 ──────────────────────────────────────────

def test_revenue_window_8_to_10_only():
    assert ev.rule_events("2026-09-07") == []
    assert "剩 2 天" in ev.rule_events("2026-09-08")[0]
    assert "截止日" in ev.rule_events("2026-09-10")[0]
    assert ev.rule_events("2026-09-11") == []


def test_static_fomc_day():
    evts = ev.events_for("2026-09-16")
    assert any("FOMC" in e for e in evts)


# ── 手動事件 ─────────────────────────────────────────────────────

def test_manual_add_list_remove(tmp_path, monkeypatch):
    _isolate_manual(tmp_path, monkeypatch)
    assert ev.load_manual() == []
    ev.add_manual_event("2026-09-16", "台積電法說會")
    assert ev.events_for("2026-09-16").count("台積電法說會") == 1
    assert ev.events_for("2026-09-15") == [ev.STATIC_EVENTS["2026-09-15"][0]]
    assert ev.remove_manual_events("2026-09-16") == 1
    assert "台積電法說會" not in ev.events_for("2026-09-16")


def test_parse_date_variants():
    from datetime import date
    today = date(2026, 9, 4)
    assert ev.parse_date("2026-09-16") == "2026-09-16"
    assert ev.parse_date("9/16", today=today) == "2026-09-16"
    assert ev.parse_date("09-16", today=today) == "2026-09-16"
    # 只給月日且已過 → 明年
    assert ev.parse_date("1/5", today=today) == "2027-01-05"
    assert ev.parse_date("亂打") is None
    assert ev.parse_date("13/45", today=today) is None


# ── 除權息（canned TWSE openapi 回應）───────────────────────────

def test_exdiv_matches_tracked_codes_today_only():
    rows = [
        {"Date": "1150904", "Code": "6669", "Name": "緯穎", "DividendType": "權"},
        {"Date": "1150904", "Code": "9999", "Name": "別人家", "DividendType": "息"},
        {"Date": "1150905", "Code": "2330", "Name": "台積電", "DividendType": "息"},
    ]
    out = ev.exdiv_events("2026-09-04", ["6669", "2330"], fetcher=lambda: rows)
    assert len(out) == 1 and "緯穎(6669)" in out[0] and "除權" in out[0]
    # 抓不到（丟例外）→ 靜默回空，不擋早盤
    assert ev.exdiv_events("2026-09-04", ["6669"],
                           fetcher=lambda: 1 / 0) == []


# ── 接線：事件進大盤預測 prompt 與推播卡片 ──────────────────────

def test_market_prediction_carries_events_into_prompt_and_card():
    captured = {}

    def fake_llm(system, user, schema):
        captured["user"] = user
        return {"direction": "漲", "confidence": "中", "reason": "r", "drivers": []}

    pred = make_market_prediction(
        {"close": 24000}, {"費半SOX": 1.0}, {"direction": "漲", "pct": 0.5},
        llm=fake_llm, events=["FOMC 會議第 2 天"])
    assert "FOMC 會議第 2 天" in captured["user"]
    assert pred["events"] == ["FOMC 會議第 2 天"]
    card = format_market_prediction("2026-09-16", pred)
    assert "今日事件" in card and "FOMC 會議第 2 天" in card


def test_market_prediction_no_events_no_section():
    fake_llm = lambda s, u, sc: {"direction": "漲", "confidence": "中",
                                 "reason": "r", "drivers": []}
    pred = make_market_prediction(
        {"close": 24000}, {"費半SOX": 1.0}, {"direction": "漲", "pct": 0.5},
        llm=fake_llm)
    assert pred["events"] == []
    assert "今日事件" not in format_market_prediction("2026-09-04", pred)
