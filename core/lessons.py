"""教訓回饋：把過去『預測錯』的檢討存起來，並在下次預測時餵回模型，讓它記取教訓。

兩個來源：
1. 各標的歷史紀錄裡『預測失敗且有檢討(critique)』的近幾筆。
2. 跨標的累積的通用教訓清單 lessons.json（復盤失敗時自動累加，保留最近 N 條）。

注意：教訓是『提醒避免重蹈』，不是叫模型一律反向；prompt 會明確要求仍以當前
技術面為準、勿過度反應。
"""
import json
import os

from core.review import hit_rate
from core import db

LESSONS_PATH = "lessons.json"
MAX_LESSONS = 30


def load_lessons(path=None):
    if db.db_enabled():
        return db.load_lessons()
    path = path or LESSONS_PATH
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def save_lessons(lessons, path=None):
    if db.db_enabled():
        db.save_lessons(lessons[-MAX_LESSONS:])
        return
    path = path or LESSONS_PATH
    with open(path, "w", encoding="utf-8") as f:
        json.dump(lessons[-MAX_LESSONS:], f, ensure_ascii=False, indent=2)


def add_lesson(stock, date, text, path=None):
    """復盤失敗時累加一條教訓（去重：同股同日只留一條）。"""
    if not text:
        return
    lessons = [x for x in load_lessons(path)
               if not (x.get("stock") == stock and x.get("date") == date)]
    lessons.append({"date": date, "stock": stock, "lesson": text})
    save_lessons(lessons, path)


def recent_misses(records, stock, n=3):
    """某標的最近 n 筆『預測錯且有檢討』的紀錄（新到舊）。"""
    out = []
    for r in sorted([x for x in records if x.get("stock") == stock],
                    key=lambda r: r.get("date", ""), reverse=True):
        rv = r.get("review") or {}
        if rv.get("success") is False and rv.get("critique"):
            out.append(r)
        if len(out) >= n:
            break
    return out


def lessons_prompt(records, stock, path=None):
    """組成要餵進預測的『過去教訓』文字；沒有任何教訓時回空字串。"""
    same = [r for r in records if r.get("stock") == stock]
    rate = hit_rate(same)
    misses = recent_misses(records, stock, 3)
    glob = load_lessons(path)
    if rate is None and not misses and not glob:
        return ""
    lines = []
    if rate is not None:
        lines.append(f"此標的歷史方向命中率：{rate * 100:.0f}%。")
    for r in misses:
        p = r.get("prediction") or {}
        rv = r.get("review") or {}
        lines.append(
            f"- {r['date']} 預測{p.get('direction')}、實際{rv.get('direction_actual')}："
            f"{rv.get('critique')}")
    other = [g for g in glob if g.get("stock") != stock][-5:]
    if other:
        lines.append("其他標的近期教訓（供參考）：")
        for g in other:
            lines.append(f"- {g.get('stock')} {g.get('date')}：{g.get('lesson')}")
    return ("【過去教訓（務必避免重蹈；但仍以當前技術面為準，勿因過去而過度反應或一律反向）】\n"
            + "\n".join(lines))
