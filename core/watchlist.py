import json
import os

from core.data import STOCKS as BASE_STOCKS

WATCHLIST_PATH = "watchlist.json"


def load_watchlist(path=WATCHLIST_PATH):
    """回 dict：{code: {"name": ..., "supports": {...}?}}；不存在回 {}。"""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_watchlist(d, path=WATCHLIST_PATH):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


def add_stock(code, name=None, supports=None, path=WATCHLIST_PATH):
    wl = load_watchlist(path)
    entry = {"name": name or f"({code})"}
    if supports:
        entry["supports"] = supports
    wl[code] = entry
    save_watchlist(wl, path)
    return wl


def remove_stock(code, path=WATCHLIST_PATH):
    wl = load_watchlist(path)
    existed = code in wl
    wl.pop(code, None)
    save_watchlist(wl, path)
    return existed


def effective_stocks(path=WATCHLIST_PATH):
    """預設清單(core.data.STOCKS) ∪ watchlist；以代號去重，回 STOCKS 格式
    {顯示名稱: {"code", "supports"?}}。"""
    by_code = {}
    for name, cfg in BASE_STOCKS.items():
        by_code[cfg["code"]] = (name, dict(cfg))
    for code, info in load_watchlist(path).items():
        if code in by_code:
            continue
        cfg = {"code": code}
        if info.get("supports"):
            cfg["supports"] = info["supports"]
        by_code[code] = (info.get("name") or f"({code})", cfg)
    return {name: cfg for name, cfg in by_code.values()}
