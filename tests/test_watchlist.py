from core.watchlist import (
    add_stock, remove_stock, load_watchlist, effective_stocks,
)
from core.data import resolve_stocks
import jobs.bot as bot


def test_add_and_load(tmp_path):
    p = str(tmp_path / "wl.json")
    add_stock("2330", name="台積電 (2330)", path=p)
    assert load_watchlist(p)["2330"]["name"] == "台積電 (2330)"


def test_add_with_supports(tmp_path):
    p = str(tmp_path / "wl.json")
    add_stock("2454", name="聯發科 (2454)",
              supports={"支撐1 (短期)": 1000, "支撐3 (長期)": 850}, path=p)
    assert load_watchlist(p)["2454"]["supports"]["支撐1 (短期)"] == 1000


def test_remove(tmp_path):
    p = str(tmp_path / "wl.json")
    add_stock("2330", name="台積電 (2330)", path=p)
    assert remove_stock("2330", path=p) is True
    assert remove_stock("2330", path=p) is False
    assert load_watchlist(p) == {}


def test_effective_merges_base_and_watchlist(tmp_path):
    p = str(tmp_path / "wl.json")
    add_stock("2330", name="台積電 (2330)", path=p)
    eff = effective_stocks(path=p)
    assert "華邦電 (2344)" in eff          # 預設
    assert eff["台積電 (2330)"]["code"] == "2330"  # 新增


def test_bot_help_and_list():
    assert "/add" in bot.handle("/help")
    assert "華邦電 (2344)" in bot.handle("/list")


def test_bot_add_no_arg():
    assert "用法" in bot.handle("/add")
    assert "用法" in bot.handle("/remove")


def test_bot_unknown():
    assert bot.handle("/foobar").startswith("不認得")


def test_resolve_by_code_and_name():
    listing = {"2330": "台積電", "2454": "聯發科", "2344": "華邦電"}
    assert resolve_stocks("2330", listing=listing) == [("2330", "台積電")]
    assert resolve_stocks("台積電", listing=listing) == [("2330", "台積電")]
    assert resolve_stocks("不存在XYZ", listing=listing) == []


def test_resolve_name_ambiguous():
    listing = {"2330": "台積電", "2308": "台達電"}
    assert len(resolve_stocks("台", listing=listing)) == 2
