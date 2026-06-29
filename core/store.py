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


def get_record(records, date):
    return next((r for r in records if r.get("date") == date), None)


def upsert_record(records, record):
    out = [r for r in records if r.get("date") != record["date"]]
    out.append(record)
    return sorted(out, key=lambda r: r["date"])
