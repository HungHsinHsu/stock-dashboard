import json
import os

HISTORY_PATH = "history/predictions.json"


def load_history(path=HISTORY_PATH):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_history(records, path=HISTORY_PATH):
    d = os.path.dirname(str(path))
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


def get_record(records, date, stock=None):
    """取某日（可指定股票）的紀錄。未指定股票時只比對日期（向後相容）。"""
    for r in records:
        if r.get("date") != date:
            continue
        if stock is None or r.get("stock") == stock:
            return r
    return None


def _key(r):
    return (r.get("date"), r.get("stock"))


def upsert_record(records, record):
    """以「日期＋股票」為唯一鍵覆寫；可同時保存同一天多檔股票。"""
    out = [r for r in records if _key(r) != _key(record)]
    out.append(record)
    return sorted(out, key=lambda r: (r.get("date") or "", r.get("stock") or ""))
