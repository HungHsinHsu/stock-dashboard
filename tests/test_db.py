import core.db as db
import core.store as store
import core.positions as positions
import core.lessons as lessons
import core.watchlist as watchlist


def test_db_enabled_reflects_env(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert db.db_enabled() is False
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host:5432/db")
    assert db.db_enabled() is True


def test_store_routes_to_db_when_enabled(monkeypatch):
    monkeypatch.setattr(db, "db_enabled", lambda: True)
    monkeypatch.setattr(db, "load_predictions",
                        lambda: [{"date": "2026-06-30", "stock": "大盤",
                                  "prediction": {}, "review": None}])
    captured = {}
    monkeypatch.setattr(db, "save_predictions",
                        lambda recs: captured.update(recs=recs))
    assert store.load_history()[0]["stock"] == "大盤"
    store.save_history([{"date": "d", "stock": "2330"}])
    assert captured["recs"][0]["stock"] == "2330"


def test_positions_routes_to_db(monkeypatch):
    monkeypatch.setattr(db, "db_enabled", lambda: True)
    monkeypatch.setattr(db, "load_positions", lambda: {"2330": {"batches": 2}})
    cap = {}
    monkeypatch.setattr(db, "save_positions", lambda p: cap.update(p=p))
    assert positions.get_batches("2330") == 2


def test_lessons_routes_to_db(monkeypatch):
    monkeypatch.setattr(db, "db_enabled", lambda: True)
    monkeypatch.setattr(db, "load_lessons",
                        lambda: [{"stock": "2344", "date": "2026-06-30", "lesson": "x"}])
    assert lessons.load_lessons()[0]["lesson"] == "x"


def test_watchlist_routes_to_db(monkeypatch):
    monkeypatch.setattr(db, "db_enabled", lambda: True)
    monkeypatch.setattr(db, "load_watchlist", lambda: {"2330": {"name": "台積電 (2330)"}})
    assert "2330" in watchlist.load_watchlist()


def test_json_path_when_db_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "db_enabled", lambda: False)
    p = str(tmp_path / "h.json")
    store.save_history([{"date": "2026-06-30", "stock": "2330",
                         "prediction": {}, "review": None}], p)
    assert store.load_history(p)[0]["stock"] == "2330"
