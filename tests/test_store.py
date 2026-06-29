from core.store import load_history, save_history, get_record, upsert_record


def test_load_missing_returns_empty(tmp_path):
    assert load_history(tmp_path / "nope.json") == []


def test_save_then_load_roundtrip(tmp_path):
    p = tmp_path / "h.json"
    recs = [{"date": "2026-06-29", "stock": "2344"}]
    save_history(recs, p)
    assert load_history(p) == recs


def test_upsert_overwrites_same_date():
    recs = [{"date": "2026-06-29", "stock": "2344", "v": 1}]
    out = upsert_record(recs, {"date": "2026-06-29", "stock": "2344", "v": 2})
    assert len(out) == 1 and out[0]["v"] == 2


def test_upsert_adds_and_sorts():
    out = upsert_record([{"date": "2026-06-29"}], {"date": "2026-06-28"})
    assert [r["date"] for r in out] == ["2026-06-28", "2026-06-29"]


def test_get_record():
    recs = [{"date": "2026-06-29", "stock": "2344"}]
    assert get_record(recs, "2026-06-29")["stock"] == "2344"
    assert get_record(recs, "2026-01-01") is None
