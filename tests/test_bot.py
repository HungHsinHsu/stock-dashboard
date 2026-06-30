import jobs.bot as bot
import core.positions as positions


def _wire(monkeypatch, tmp_path):
    p = str(tmp_path / "pos.json")
    monkeypatch.setattr(bot, "resolve_stocks", lambda q: [("2344", "華邦電")])
    monkeypatch.setattr(bot, "enter_batch", lambda code: positions.enter_batch(code, path=p))
    monkeypatch.setattr(bot, "exit_position", lambda code: positions.exit_position(code, path=p))
    monkeypatch.setattr(bot, "held_positions", lambda: positions.held_positions(path=p))


def test_in_pos_out_flow(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path)
    assert "第 1 批" in bot.handle("/in 2344")
    assert "第 2 批" in bot.handle("/in 華邦電")      # 名稱也能操作
    pos = bot.handle("/pos")
    assert "2344" in pos and "2/3" in pos
    assert "出場" in bot.handle("/out 2344")
    assert "沒有任何部位" in bot.handle("/pos")


def test_in_caps_at_three(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path)
    for _ in range(3):
        msg = bot.handle("/in 2344")
    assert "3/3" in msg and "三批已滿" in msg


def test_help_lists_position_commands():
    assert "/in" in bot.HELP and "/out" in bot.HELP and "/pos" in bot.HELP
