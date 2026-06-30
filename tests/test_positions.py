from core.positions import (
    get_batches, enter_batch, exit_position, held_positions, MAX_BATCHES,
)


def _p(tmp_path):
    return str(tmp_path / "positions.json")


def test_enter_and_get_batches(tmp_path):
    p = _p(tmp_path)
    assert get_batches("2344", path=p) == 0
    assert enter_batch("2344", date="2026-06-30", path=p) == 1
    assert enter_batch("2344", date="2026-06-30", path=p) == 2
    assert get_batches("2344", path=p) == 2


def test_enter_caps_at_three(tmp_path):
    p = _p(tmp_path)
    for _ in range(5):
        n = enter_batch("2344", date="2026-06-30", path=p)
    assert n == MAX_BATCHES and get_batches("2344", path=p) == 3


def test_exit_clears_position(tmp_path):
    p = _p(tmp_path)
    enter_batch("2344", date="2026-06-30", path=p)
    assert exit_position("2344", path=p) is True
    assert get_batches("2344", path=p) == 0
    assert exit_position("2344", path=p) is False     # 已無部位


def test_held_positions_lists_only_nonzero(tmp_path):
    p = _p(tmp_path)
    enter_batch("2344", date="2026-06-30", path=p)
    enter_batch("3037", date="2026-06-30", path=p)
    exit_position("3037", path=p)
    assert held_positions(path=p) == {"2344": 1}
