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
    assert {"predict", "review", "add", "remove", "list",
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


def test_review_single_stock_detail(monkeypatch):
    _wire_predict(monkeypatch)
    monkeypatch.setattr(bot, "resolve_stocks", lambda q: [("2344", "華邦電")])
    out = bot.handle("/復盤 2344")
    assert "華邦電" in out and "觀望" in out and "1/3" in out  # 詳細卡＋部位


def test_review_no_arg_summary(monkeypatch):
    _wire_predict(monkeypatch)
    out = bot.handle("/復盤")
    assert "摘要" in out and "華邦電" in out and "觀望" in out


def test_review_no_records(monkeypatch):
    monkeypatch.setattr(bot, "_git_pull", lambda: None)
    monkeypatch.setattr(bot, "load_history", lambda: [])
    assert "沒有預測紀錄" in bot.handle("/復盤")


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
    assert any("試算" in a for a in acks)          # 有先回覆「試算中」


def test_f_stock_live(monkeypatch):
    acks = _wire_forecast(monkeypatch)
    monkeypatch.setattr(bot, "resolve_stocks", lambda q: [("2330", "台積電")])
    monkeypatch.setattr(bot, "effective_stocks",
                        lambda: {"台積電 (2330)": {"code": "2330"}})
    out = bot.handle("/f 2330")
    assert out == "STOCK_CARD:2330"


def test_resolve_chinese_name_from_watchlist_when_listing_empty(monkeypatch):
    # 清單內已有台積電，但外部 TWSE 名單抓不到（回 []）→ 仍能用中文名解析
    monkeypatch.setattr(bot, "effective_stocks",
                        lambda: {"台積電 (2330)": {"code": "2330"}})
    monkeypatch.setattr(bot, "resolve_stocks", lambda q: [])
    code, disp = bot._resolve_one("台積電")
    assert code == "2330" and disp == "台積電 (2330)"
    # 代號一樣可解析
    assert bot._resolve_one("2330")[0] == "2330"
    # 部分名稱（台積）也命中
    assert bot._resolve_one("台積")[0] == "2330"


def test_predict_chinese_name_when_listing_empty(monkeypatch):
    # 中文名→正確代號→即時試算下一交易日（外部名單失效也行）
    monkeypatch.setattr(bot, "_git_pull", lambda: None)
    monkeypatch.setattr(bot, "tg", type("T", (), {
        "send": staticmethod(lambda t: True)}))
    monkeypatch.setattr(bot, "effective_stocks",
                        lambda: {"華邦電 (2344)": {"code": "2344"}})
    monkeypatch.setattr(bot, "resolve_stocks", lambda q: [])
    monkeypatch.setattr(bot, "_forecast_stock",
                        lambda code, name, supports: f"CARD:{code}")
    assert bot.handle("/預測 華邦電") == "CARD:2344"


def test_predict_is_live_forecast_not_today_result(monkeypatch):
    # 預測指令走即時試算，不會回今天的命中復盤
    monkeypatch.setattr(bot, "_git_pull", lambda: None)
    monkeypatch.setattr(bot, "tg", type("T", (), {
        "send": staticmethod(lambda t: True)}))
    monkeypatch.setattr(bot, "effective_stocks",
                        lambda: {"華邦電 (2344)": {"code": "2344"}})
    monkeypatch.setattr(bot, "resolve_stocks", lambda q: [("2344", "華邦電")])
    monkeypatch.setattr(bot, "_forecast_stock",
                        lambda code, name, supports: "🔮 即時試算 預判下一交易日")
    out = bot.handle("/預測 2344")
    assert "即時試算" in out and "命中" not in out


def test_freeform_question_routes_to_llm(monkeypatch):
    monkeypatch.setattr(bot, "_git_pull", lambda: None)
    monkeypatch.setattr(bot, "load_history", lambda: [])
    monkeypatch.setattr(bot, "effective_stocks",
                        lambda: {"台積電 (2330)": {"code": "2330"}})
    monkeypatch.setattr(bot, "held_positions", lambda: {})
    acks = []
    monkeypatch.setattr(bot, "tg", type("T", (), {
        "send": staticmethod(lambda t: acks.append(t) or True)}))
    seen = {}

    def fake_gt(system, user):
        seen["system"], seen["user"] = system, user
        return "台積電目前站上季線，偏多。"

    monkeypatch.setattr(bot, "generate_text", fake_gt)
    out = bot.handle("台積電還能買嗎？")
    assert out == "台積電目前站上季線，偏多。"
    assert "台積電還能買嗎" in seen["user"]        # 問題有帶入
    assert "追蹤清單" in seen["user"]              # 背景有帶入
    assert any("想一下" in a for a in acks)         # 有先回覆思考中


def test_slash_typo_still_reports_unknown(monkeypatch):
    # 以「/」開頭但打錯 → 仍回「不認得的指令」，不會誤丟給問答
    assert "不認得的指令" in bot.handle("/blahblah")


def test_forecast_in_help():
    assert "/forecast" in bot.HELP
