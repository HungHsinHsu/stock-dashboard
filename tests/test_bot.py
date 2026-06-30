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
    assert "/p" in bot.HELP


def test_help_one_command_per_line():
    # 每行最多一個指令（一個 "/"），不會把多個指令擠在同一行
    for line in bot.HELP.splitlines():
        assert line.count("/") <= 1


_REC = {"date": "2026-06-30", "stock": "2344",
        "prediction": {"signal": "觀望", "direction": "跌", "confidence": "中",
                       "bull_signals": [], "bear_signals": ["跌破支撐"],
                       "hold_ma20": False, "hold_support1": False, "reason": "量縮",
                       "indicators": {"close": 206.0, "ma20": 210.0},
                       "market": None, "batches": 1}}


def _wire_predict(monkeypatch):
    monkeypatch.setattr(bot, "_git_pull", lambda: None)
    monkeypatch.setattr(bot, "load_history", lambda: [_REC])
    monkeypatch.setattr(bot, "effective_stocks",
                        lambda: {"華邦電 (2344)": {"code": "2344"}})


def test_p_single_stock_detail(monkeypatch):
    _wire_predict(monkeypatch)
    monkeypatch.setattr(bot, "resolve_stocks", lambda q: [("2344", "華邦電")])
    out = bot.handle("/p 2344")
    assert "華邦電" in out and "觀望" in out and "1/3" in out  # 詳細卡＋部位


def test_p_no_arg_summary(monkeypatch):
    _wire_predict(monkeypatch)
    out = bot.handle("/p")
    assert "摘要" in out and "華邦電" in out and "觀望" in out


def test_p_no_records(monkeypatch):
    monkeypatch.setattr(bot, "_git_pull", lambda: None)
    monkeypatch.setattr(bot, "load_history", lambda: [])
    assert "沒有預測紀錄" in bot.handle("/p")
