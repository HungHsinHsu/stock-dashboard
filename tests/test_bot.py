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


def test_help_uses_full_words():
    for full in ("/predict", "/forecast", "/add", "/remove", "/list",
                 "/enter", "/exit", "/position", "/help"):
        assert full in bot.HELP


def test_help_one_command_per_line():
    # 每行最多一個指令（中文別名提示行例外）
    for line in bot.HELP.splitlines():
        if line.startswith("（"):
            continue
        assert line.count("/") <= 1


def test_chinese_and_full_aliases():
    # 中文與完整英文別名都能用
    assert bot.handle("/說明") == bot.HELP
    assert bot.handle("/help") == bot.HELP


def test_register_commands_payload():
    cmds = {c for c, _ in bot.BOT_COMMANDS}
    assert {"predict", "forecast", "add", "remove", "list",
            "enter", "exit", "position", "help"} <= cmds


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


def _wire_forecast(monkeypatch):
    acks = []
    monkeypatch.setattr(bot, "_git_pull", lambda: None)
    monkeypatch.setattr(bot, "tg", type("T", (), {
        "send": staticmethod(lambda t: acks.append(t) or True)}))
    monkeypatch.setattr(bot, "_forecast_market", lambda: "MARKET_CARD")
    monkeypatch.setattr(bot, "_forecast_stock",
                        lambda code, name, supports: f"STOCK_CARD:{code}")
    return acks


def test_f_market_live(monkeypatch):
    acks = _wire_forecast(monkeypatch)
    out = bot.handle("/f")
    assert out == "MARKET_CARD"
    assert any("計算" in a for a in acks)          # 有先回覆「計算中」


def test_f_stock_live(monkeypatch):
    acks = _wire_forecast(monkeypatch)
    monkeypatch.setattr(bot, "resolve_stocks", lambda q: [("2330", "台積電")])
    monkeypatch.setattr(bot, "effective_stocks",
                        lambda: {"台積電 (2330)": {"code": "2330"}})
    out = bot.handle("/f 2330")
    assert out == "STOCK_CARD:2330"


def test_forecast_in_help():
    assert "/forecast" in bot.HELP
