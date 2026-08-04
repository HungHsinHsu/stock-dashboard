"""月線換手：索引方向要往未來走，不是往過去走。"""
import pandas as pd
from jobs.quote import ma20_roll


def _df(vals):
    idx = pd.date_range("2026-01-01", periods=len(vals), freq="D")
    return pd.DataFrame({"Close": vals}, index=idx)


def test_roll_lists_bars_about_to_leave_window():
    # 25 根：0..24。窗口＝最後 20 根(index 5..24)，最舊的是 index 5。
    df = _df(list(range(100, 125)))
    cur, rows = ma20_roll(df, n=3)
    assert cur == 124.0
    # 明天滾出 index5=105，後天 index6=106，再來 index7=107
    assert [r[1] for r in rows] == [105.0, 106.0, 107.0]
    assert all(r[2] for r in rows)          # 都比現價低 → 都是拉升


def test_roll_marks_drag_when_old_price_higher():
    # 25 根 → 窗口＝index 5..24，即將滾出的是 index 5，把它設成高於現價的 200
    vals = [100.0] * 5 + [200.0] + [100.0] * 19
    df = _df(vals)
    cur, rows = ma20_roll(df, n=2)
    assert cur == 100.0
    assert rows[0][1] == 200.0 and rows[0][2] is False   # 高於現價 → 拖累


def test_roll_needs_enough_bars():
    assert ma20_roll(_df([1.0] * 20)) == (None, [])
