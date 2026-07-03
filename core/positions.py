"""分批部位追蹤：記錄各股『回檔承接法』已進到第幾批（0~3）。每個帳號各自一份。

手冊規定一檔分三批承接（支撐1/MA20/支撐3 各 1/3），停損則全數出場。
本檔只記『已進批數』，透過 chatbox /enter /exit 維護，預測時據以提示下一批或
提醒三批已滿/該停損出場。
"""
import json
import os
from datetime import datetime
from core.tz import now_tw

from core import db

POSITIONS_PATH = "positions.json"
MAX_BATCHES = 3
DEFAULT_OWNER = "admin"


def _key(owner):
    return f"pos:{owner or DEFAULT_OWNER}"


def load_positions(owner=DEFAULT_OWNER, path=POSITIONS_PATH):
    if db.db_enabled():
        return db.get_state(_key(owner), {}) or {}
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_positions(positions, owner=DEFAULT_OWNER, path=POSITIONS_PATH):
    if db.db_enabled():
        db.set_state(_key(owner), positions)
        return
    with open(path, "w", encoding="utf-8") as f:
        json.dump(positions, f, ensure_ascii=False, indent=2)


def get_batches(code, owner=DEFAULT_OWNER, path=POSITIONS_PATH):
    """該股已進批數（0~3）。"""
    rec = load_positions(owner, path).get(str(code))
    if not rec:
        return 0
    try:
        return int(rec.get("batches", 0))
    except (TypeError, ValueError):
        return 0


def enter_batch(code, date=None, owner=DEFAULT_OWNER, path=POSITIONS_PATH):
    """進一批（上限 3）。回新的已進批數。"""
    positions = load_positions(owner, path)
    cur = 0
    rec = positions.get(str(code))
    if rec:
        try:
            cur = int(rec.get("batches", 0))
        except (TypeError, ValueError):
            cur = 0
    new = min(cur + 1, MAX_BATCHES)
    positions[str(code)] = {
        "batches": new,
        "updated": date or now_tw().strftime("%Y-%m-%d"),
    }
    save_positions(positions, owner, path)
    return new


def exit_position(code, owner=DEFAULT_OWNER, path=POSITIONS_PATH):
    """全數出場（清為 0）。回先前是否有部位。"""
    positions = load_positions(owner, path)
    rec = positions.pop(str(code), None)
    save_positions(positions, owner, path)
    had = False
    if rec:
        try:
            had = int(rec.get("batches", 0)) > 0
        except (TypeError, ValueError):
            had = False
    return had


def held_positions(owner=DEFAULT_OWNER, path=POSITIONS_PATH):
    """目前有部位(批數>0)的 {code: batches}。"""
    out = {}
    for code, rec in load_positions(owner, path).items():
        try:
            b = int(rec.get("batches", 0))
        except (TypeError, ValueError):
            b = 0
        if b > 0:
            out[code] = b
    return out
