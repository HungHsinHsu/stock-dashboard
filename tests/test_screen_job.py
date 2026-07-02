import pandas as pd
import core.db as db
from jobs import screen


def _df(n=60):
    idx = pd.date_range(end="2026-07-02", periods=n, freq="D")
    closes = [100 + i for i in range(n)]
    return pd.DataFrame({"Open": closes, "High": [c + 1 for c in closes],
                         "Low": [c - 1 for c in closes], "Close": closes,
                         "Volume": [1000.0] * n}, index=idx)


def test_run_does_not_overwrite_when_universe_empty(monkeypatch):
    # TWSE 沒回應(清單=0) → 不可覆寫 DB，保留上一份好結果
    calls = []
    monkeypatch.setattr(db, "set_state", lambda k, v: calls.append((k, v)))
    r = screen.run(uni_fetch=lambda n: [], notify=False)
    assert r["cands"] == [] and calls == []


def test_run_overwrites_when_universe_present(monkeypatch):
    # 有抓到清單 → 正常寫入 screen:latest
    calls = []
    monkeypatch.setattr(db, "set_state", lambda k, v: calls.append((k, v)))
    monkeypatch.setattr(screen, "fetch_foreign_flow", lambda c: {"stopped": True})
    r = screen.run(uni_fetch=lambda n: [("8888", "測試")],
                   fetch=lambda c: _df(), notify=False)
    assert calls and calls[0][0] == "screen:latest"
    assert r["uni_n"] == 1
