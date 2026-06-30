import json
import os
import requests

from core.data import STOCKS as BASE_STOCKS, fetch_stock_name
from core.watchlist import add_stock, remove_stock, effective_stocks
import core.telegram as tg

STATE_PATH = "bot_state.json"

HELP = (
    "📋 股票清單指令\n"
    "/add 2330　加入(可帶支撐：/add 2330 1000 850)\n"
    "/remove 2330　移除\n"
    "/list　目前清單\n"
    "/help　說明"
)


def _load_offset(path=STATE_PATH):
    if not os.path.exists(path):
        return 0
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("offset", 0)
    except Exception:
        return 0


def _save_offset(offset, path=STATE_PATH):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"offset": offset}, f)


def get_updates(token, offset):
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    r = requests.get(url, params={"offset": offset, "timeout": 0}, timeout=20)
    return r.json().get("result", [])


def handle(text):
    """解析一條指令，回傳要回覆的字串（None=不回）。"""
    parts = text.strip().split()
    if not parts:
        return None
    cmd = parts[0].lower().lstrip("/").split("@")[0]
    args = parts[1:]

    if cmd in ("start", "help"):
        return HELP
    if cmd == "list":
        names = list(effective_stocks().keys())
        return "📋 目前追蹤清單：\n" + "\n".join(f"・{n}" for n in names)
    if cmd == "add":
        if not args or not args[0].isdigit():
            return "用法：/add 股票代號，例如 /add 2330"
        code = args[0]
        supports = None
        if len(args) >= 3:
            try:
                supports = {"支撐1 (短期)": float(args[1]),
                            "支撐3 (長期)": float(args[2])}
            except ValueError:
                supports = None
        name = fetch_stock_name(code)
        disp = f"{name} ({code})" if name else f"({code})"
        add_stock(code, name=disp, supports=supports)
        return f"✅ 已加入 {disp}" + ("（含支撐）" if supports else "")
    if cmd in ("remove", "del", "delete", "rm"):
        if not args or not args[0].isdigit():
            return "用法：/remove 股票代號，例如 /remove 2330"
        code = args[0]
        if remove_stock(code):
            return f"🗑 已移除 {code}"
        if code in {c["code"] for c in BASE_STOCKS.values()}:
            return f"{code} 是預設股票，無法用指令移除。"
        return f"清單中找不到 {code}。"
    return "不認得的指令。傳 /help 看用法。"


def run():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("缺 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID")
        return
    offset = _load_offset()
    updates = get_updates(token, offset)
    handled = 0
    for u in updates:
        offset = max(offset, u["update_id"] + 1)
        msg = u.get("message") or u.get("edited_message")
        if not msg:
            continue
        # 安全：只處理你本人的訊息
        if str(msg.get("chat", {}).get("id")) != str(chat_id):
            continue
        text = msg.get("text", "")
        if not text:
            continue
        reply = handle(text)
        if reply:
            tg.send(reply)
            handled += 1
    _save_offset(offset)
    print(f"updates={len(updates)} handled={handled} offset={offset}")


if __name__ == "__main__":
    run()
