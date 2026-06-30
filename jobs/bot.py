import json
import os
import subprocess
import time
import requests

from core.data import STOCKS as BASE_STOCKS, resolve_stocks
from core.watchlist import add_stock, remove_stock, effective_stocks
import core.telegram as tg

STATE_PATH = "bot_state.json"

HELP = (
    "📋 股票清單指令（代號或中文名稱皆可）\n"
    "/add 2330　或　/add 台積電　加入\n"
    "　（可帶支撐：/add 2330 1000 850）\n"
    "/remove 台積電　或　/remove 2330　移除\n"
    "/list　目前清單\n"
    "/help　說明"
)


def _ambiguous(query, matches):
    sample = "、".join(f"{n}({c})" for c, n in matches[:8])
    return f"「{query}」對應多檔，請更精確或改用代號：\n{sample}"


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


def get_updates(token, offset, long_poll=0):
    """long_poll>0 時用 Telegram 長輪詢：有訊息即回，否則阻塞至多 long_poll 秒。"""
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    r = requests.get(
        url, params={"offset": offset, "timeout": long_poll},
        timeout=long_poll + 15,
    )
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
        if not args:
            return "用法：/add 代號或名稱，例如 /add 2330 或 /add 台積電"
        query, rest = args[0], args[1:]
        matches = resolve_stocks(query)
        if not matches:
            return f"找不到「{query}」。請確認代號或中文名稱（限上市股票）。"
        if len(matches) > 1:
            return _ambiguous(query, matches)
        code, name = matches[0]
        supports = None
        if len(rest) >= 2:
            try:
                supports = {"支撐1 (短期)": float(rest[0]),
                            "支撐3 (長期)": float(rest[1])}
            except ValueError:
                supports = None
        disp = f"{name} ({code})" if name else f"({code})"
        add_stock(code, name=disp, supports=supports)
        return f"✅ 已加入 {disp}" + ("（含支撐）" if supports else "")
    if cmd in ("remove", "del", "delete", "rm"):
        if not args:
            return "用法：/remove 代號或名稱，例如 /remove 2330 或 /remove 台積電"
        query = args[0]
        if query.isdigit():
            code = query
        else:
            matches = resolve_stocks(query)
            if not matches:
                return f"找不到「{query}」。"
            if len(matches) > 1:
                return _ambiguous(query, matches)
            code = matches[0][0]
        if remove_stock(code):
            return f"🗑 已移除 {code}"
        if code in {c["code"] for c in BASE_STOCKS.values()}:
            return f"{code} 是預設股票，無法用指令移除。"
        return f"清單中找不到 {code}。"
    return "不認得的指令。傳 /help 看用法。"


def _process(updates, chat_id):
    """處理一批 updates，回 (new_offset, handled_count)。"""
    offset, handled = 0, 0
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
    return offset, handled


def _commit_push():
    """把 watchlist/state 變更 commit & push 回分支（在 Actions runner 內）。"""
    ref = os.environ.get("GITHUB_REF_NAME", "main")
    files = [f for f in ("watchlist.json", STATE_PATH) if os.path.exists(f)]
    if not files:
        return
    try:
        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=False)
        subprocess.run(
            ["git", "config", "user.email",
             "github-actions[bot]@users.noreply.github.com"], check=False)
        subprocess.run(["git", "add", *files], check=False)
        staged = subprocess.run(["git", "diff", "--staged", "--quiet"]).returncode
        if staged == 1:  # 有變更才 commit
            subprocess.run(
                ["git", "commit", "-m", "Chore: 更新股票清單/輪詢狀態 [skip ci]"],
                check=False)
        subprocess.run(["git", "pull", "--rebase", "origin", ref], check=False)
        subprocess.run(["git", "push", "origin", f"HEAD:{ref}"], check=False)
    except Exception as e:
        print("git commit/push 失敗：", e)


def run(loop=False):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("缺 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID")
        return
    offset = _load_offset()

    if not loop:  # 單次（手動/測試用）
        new_off, handled = _process(get_updates(token, offset), chat_id)
        offset = max(offset, new_off)
        _save_offset(offset)
        print(f"handled={handled} offset={offset}")
        return

    # 長輪詢：持續監聽約 5.8 小時（< Actions 6h 上限），由 cron 接力重啟。
    deadline = time.monotonic() + 5.8 * 3600
    print(f"long-poll start, offset={offset}")
    while time.monotonic() < deadline:
        try:
            updates = get_updates(token, offset, long_poll=25)
        except Exception as e:
            print("getUpdates 失敗，稍後重試：", e)
            time.sleep(5)
            continue
        if not updates:
            continue  # 長輪詢已阻塞約 25 秒，直接再聽
        new_off, handled = _process(updates, chat_id)
        offset = max(offset, new_off)
        _save_offset(offset)
        _commit_push()  # 即時把清單/offset 推回，排程才讀得到
        print(f"handled={handled} offset={offset}")


if __name__ == "__main__":
    import sys
    run(loop="--loop" in sys.argv)
