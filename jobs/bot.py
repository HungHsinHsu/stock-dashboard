import json
import os
import subprocess
import time
import requests

from core.data import STOCKS as BASE_STOCKS, resolve_stocks
from core.watchlist import add_stock, remove_stock, effective_stocks
from core.positions import (
    enter_batch, exit_position, held_positions, MAX_BATCHES,
)
from core.store import load_history
from core.predict import format_prediction
import core.telegram as tg

STATE_PATH = "bot_state.json"

HELP = "\n".join([
    "📋 指令清單（代號或中文名稱皆可）",
    "",
    "—— 查預測 ——",
    "/p　今日全部個股預測摘要",
    "/p 2330　單檔詳細預測",
    "",
    "—— 股票清單 ——",
    "/add 2330　加入",
    "/add 2330 1000 850　加入並設支撐",
    "/remove 2330　移除",
    "/list　目前清單",
    "",
    "—— 部位（回檔承接法分三批）——",
    "/in 2330　進一批",
    "/out 2330　出場清空",
    "/pos　目前持有批數",
    "",
    "/help　說明",
])


def _git_pull():
    """讀預測前先把 main 上最新的 history 拉下來（morning 會 commit 預測）。"""
    ref = os.environ.get("GITHUB_REF_NAME", "main")
    try:
        subprocess.run(["git", "pull", "--rebase", "origin", ref], check=False)
    except Exception as e:
        print("git pull 失敗：", e)


def _name_for(code):
    for n, cfg in effective_stocks().items():
        if cfg.get("code") == str(code):
            return n
    return f"({code})"


def _latest_for(records, code):
    recs = [r for r in records if r.get("stock") == str(code) and r.get("prediction")]
    return max(recs, key=lambda r: r.get("date", ""), default=None)


def _summary_line(name, rec):
    p = rec.get("prediction") or {}
    conf = p.get("confidence")
    conf_txt = f"({conf})" if conf else ""
    bt = p.get("batches")
    bt_txt = f"｜{bt}/3批" if isinstance(bt, int) else ""
    return f"・{name}：{p.get('signal', '—')}｜預期{p.get('direction', '—')}{conf_txt}{bt_txt}"


def _resolve_one(query):
    """把代號或名稱解析成單一 (code, 顯示名)。回 (code, disp) 或 (None, 錯誤字串)。"""
    matches = resolve_stocks(query)
    if not matches:
        return None, f"找不到「{query}」。請確認代號或中文名稱（限上市股票）。"
    if len(matches) > 1:
        return None, _ambiguous(query, matches)
    code, name = matches[0]
    return code, (f"{name} ({code})" if name else f"({code})")


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
    if cmd in ("in", "buy"):
        if not args:
            return "用法：/in 代號或名稱，例如 /in 2330"
        code, disp = _resolve_one(args[0])
        if code is None:
            return disp
        new = enter_batch(code)
        tail = "　⚠️三批已滿，不再加碼" if new >= MAX_BATCHES else ""
        return f"✅ {disp} 已進第 {new} 批（{new}/{MAX_BATCHES}）{tail}"
    if cmd in ("out", "exit", "sell"):
        if not args:
            return "用法：/out 代號或名稱，例如 /out 2330"
        code, disp = _resolve_one(args[0])
        if code is None:
            return disp
        had = exit_position(code)
        return (f"🗑 {disp} 已全數出場、清空部位" if had
                else f"{disp} 本來就沒有部位。")
    if cmd in ("pos", "position"):
        held = held_positions()
        if not held:
            return "📦 目前沒有任何部位。"
        return "📦 目前部位：\n" + "\n".join(
            f"・{c}：{b}/{MAX_BATCHES} 批" for c, b in held.items())
    if cmd in ("p", "predict", "pred"):
        _git_pull()
        records = load_history()
        if not records:
            return "目前沒有預測紀錄（開盤後才會產生）。"
        if args:                                  # 單檔詳細
            code, disp = _resolve_one(args[0])
            if code is None:
                return disp
            rec = _latest_for(records, code)
            if rec is None:
                return f"{disp} 目前沒有預測紀錄。"
            return format_prediction(_name_for(code), rec["date"], rec["prediction"])
        # 無參數 → 清單內各股摘要
        lines = ["📋 今日各股預測摘要（詳細：/p 代號）"]
        any_rec = False
        for name, cfg in effective_stocks().items():
            rec = _latest_for(records, cfg["code"])
            if rec:
                any_rec = True
                lines.append(_summary_line(name, rec))
        if not any_rec:
            return "目前清單內的股票都還沒有預測紀錄。"
        return "\n".join(lines)
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
    files = [f for f in ("watchlist.json", "positions.json", STATE_PATH)
             if os.path.exists(f)]
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
