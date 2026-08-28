import pytest

from jobs.holdadmin import apply_ops, parse_ops


def test_parse_ops_all_forms():
    ops = parse_ops("sell:2451, sell:2618:250,buy:00830:100:83.2\nset:2383:2:5800")
    assert ops == [
        ("sell", "2451", None, None),
        ("sell", "2618", 250.0, None),
        ("buy", "00830", 100.0, 83.2),
        ("set", "2383", 2.0, 5800.0),
    ]


def test_parse_ops_rejects_bad_token_entirely():
    # 帳本操作寧可整批失敗，也不要「跳過壞的、套用好的」——半批入帳最難察覺
    with pytest.raises(ValueError):
        parse_ops("sell:2451,buy:00830:83.2")   # buy 少了股數


def test_apply_ops_mirrors_bot_semantics(tmp_path):
    path = str(tmp_path / "holdings.json")
    # 買進：無→新增；再買同檔→加權平均（同 /買了）
    apply_ops(parse_ops("buy:2408:20:400"), path=path)
    apply_ops(parse_ops("buy:2408:5:340"), path=path)
    from core.holdings import load_holdings
    rec = load_holdings(path=path)["2408"]
    assert rec["shares"] == 25
    assert rec["avg_cost"] == pytest.approx(388.0)
    # 部分賣出：均價不變（同 /賣了 代號 股數）
    apply_ops(parse_ops("sell:2408:8"), path=path)
    rec = load_holdings(path=path)["2408"]
    assert rec["shares"] == 17 and rec["avg_cost"] == pytest.approx(388.0)
    # 出清（同 /賣了 代號）；賣不存在的檔只警告不炸
    lines = apply_ops(parse_ops("sell:2408,sell:9999"), path=path)
    assert "2408" not in load_holdings(path=path)
    assert any("9999" in ln and "略過" in ln for ln in lines)
    # set：對帳後直接覆寫
    apply_ops(parse_ops("set:2383:2:5800"), path=path)
    rec = load_holdings(path=path)["2383"]
    assert rec["shares"] == 2 and rec["avg_cost"] == 5800
