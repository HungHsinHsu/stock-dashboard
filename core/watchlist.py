import json
import os

from core.data import STOCKS as BASE_STOCKS
from core import db

WATCHLIST_PATH = "watchlist.json"
DEFAULT_OWNER = "admin"          # Telegram/排程 job 的擁有者；每個網頁帳號用自己的 username


def _key(owner):
    return f"wl:{owner or DEFAULT_OWNER}"


def load_watchlist(owner=DEFAULT_OWNER, path=WATCHLIST_PATH):
    """回 dict：{code: {"name":..., "supports":{...}?}}；每個 owner 各自一份。"""
    if db.db_enabled():
        return db.get_state(_key(owner), {}) or {}
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_watchlist(d, owner=DEFAULT_OWNER, path=WATCHLIST_PATH):
    if db.db_enabled():
        db.set_state(_key(owner), d)
        return
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


def add_stock(code, name=None, supports=None, owner=DEFAULT_OWNER, path=WATCHLIST_PATH):
    wl = load_watchlist(owner, path)
    entry = {"name": name or f"({code})"}
    if supports:
        entry["supports"] = supports
    wl[code] = entry
    save_watchlist(wl, owner, path)
    return wl


def remove_stock(code, owner=DEFAULT_OWNER, path=WATCHLIST_PATH):
    wl = load_watchlist(owner, path)
    existed = code in wl
    wl.pop(code, None)
    save_watchlist(wl, owner, path)
    return existed


def _merge(by_code, wl):
    for code, info in (wl or {}).items():
        if code in by_code:
            continue
        cfg = {"code": code}
        if info.get("supports"):
            cfg["supports"] = info["supports"]
        by_code[code] = (info.get("name") or f"({code})", cfg)


def effective_stocks(owner=DEFAULT_OWNER, path=WATCHLIST_PATH):
    """該 owner 的有效清單：預設清單(BASE) ∪ 該 owner 的 watchlist。
    回 STOCKS 格式 {顯示名稱: {"code", "supports"?}}。"""
    by_code = {cfg["code"]: (name, dict(cfg)) for name, cfg in BASE_STOCKS.items()}
    _merge(by_code, load_watchlist(owner, path))
    return {name: cfg for name, cfg in by_code.values()}


def all_tracked_stocks():
    """所有帳號清單的聯集(＋BASE)，供每日預測『每支股票只算一次』。"""
    by_code = {cfg["code"]: (name, dict(cfg)) for name, cfg in BASE_STOCKS.items()}
    if db.db_enabled():
        for wl in db.get_states_by_prefix("wl:").values():
            _merge(by_code, wl)
    else:
        _merge(by_code, load_watchlist())
    return {name: cfg for name, cfg in by_code.values()}
