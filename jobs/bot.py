import json
import os
import subprocess
import time
import requests

from core.data import (
    STOCKS as BASE_STOCKS, resolve_stocks,
    fetch_daily, fetch_index, fetch_us_overnight, fetch_taifex, fetch_foreign_flow,
)
from core.watchlist import add_stock, remove_stock, effective_stocks
from core.positions import (
    enter_batch, exit_position, held_positions, get_batches, MAX_BATCHES,
)
from core.store import load_history
from core.market import market_summary
from core.indicators import compute_indicators
from core.predict import (
    make_prediction, make_market_prediction,
    format_prediction, format_market_prediction,
)
from core.lessons import lessons_prompt
import core.telegram as tg

STATE_PATH = "bot_state.json"

HELP = "\n".join([
    "📋 指令清單（代號或中文名稱皆可）",
    "",
    "—— 查預測 ——",
    "/predict　今天全部個股預測摘要",
    "/predict 2330　單檔詳細預測",
    "/forecast　即時試算大盤（不等自動推送）",
    "/forecast 2330　即時試算個股",
    "",
    "—— 股票清單 ——",
    "/add 2330　加入",
    "/add 2330 1000 850　加入並設支撐",
    "/remove 2330　移除",
    "/list　目前清單",
    "",
    "—— 部位（回檔承接法分三批）——",
    "/enter 2330　記錄進一批",
    "/exit 2330　出場、清空部位",
    "/position　目前持有批數",
    "",
    "/help　說明",
    "（中文也通：/預測 /即時預測 /加入 /移除 /清單 /進場 /出場 /部位 /說明）",
])

# Telegram 指令選單（打「/」會跳出，附中文說明）。command 須小寫英文。
BOT_COMMANDS = [
    ("predict", "查今天的預測（加代號看單檔）"),
    ("forecast", "即時試算預測，不等自動推送"),
    ("add", "加入追蹤股票"),
    ("remove", "移除追蹤股票"),
    ("list", "目前追蹤清單"),
    ("enter", "記錄進一批部位"),
    ("exit", "出場、清空部位"),
    ("position", "目前持有批數"),
    ("help", "指令說明"),
]


def register_commands(token):
    """把指令清單註冊到 Telegram 選單（打「/」會自動跳出）。"""
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/setMyCommands",
            json={"commands": [{"command": c, "description": d}
                               for c, d in BOT_COMMANDS]},
            timeout=10)
    except Exception as e:
        print("setMyCommands 失敗：", e)


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


def _forecast_market():
    """即時重新計算大盤開盤前預測（依最新收盤資料）。"""
    idx_df = fetch_index()
    if idx_df.empty:
        return "⚠️ 抓不到大盤資料，稍後再試。"
    market = market_summary(idx_df)
    us = fetch_us_overnight()
    tf = fetch_taifex()
    ind = compute_indicators(idx_df, {})
    mpred = make_market_prediction(ind, us, market, tf,
                                   lessons=lessons_prompt(load_history(), "大盤"))
    card = format_market_prediction(str(idx_df.index[-1].date()), mpred)
    return "🔮 即時試算（依最新收盤、預判下一交易日）\n\n" + card


def _forecast_stock(code, name, supports):
    """即時重新計算個股開盤前預測。"""
    df = fetch_daily(code)
    if df.empty:
        return f"⚠️ {name} 抓不到資料。"
    market = market_summary(fetch_index())
    us = fetch_us_overnight()
    foreign = fetch_foreign_flow(code)
    ind = compute_indicators(df, supports or {})
    pred = make_prediction(ind, name, market=market, us_overnight=us, code=code,
                           foreign=foreign, batches=get_batches(code),
                           lessons=lessons_prompt(load_history(), code))
    card = format_prediction(name, str(df.index[-1].date()), pred)
    return "🔮 即時試算（依最新收盤、預判下一交易日）\n\n" + card


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

    if cmd in ("start", "help", "說明", "幫助", "指令"):
        return HELP
    if cmd in ("list", "清單", "列表"):
        names = list(effective_stocks().keys())
        return "📋 目前追蹤清單：\n" + "\n".join(f"・{n}" for n in names)
    if cmd in ("add", "加入", "新增"):
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
    if cmd in ("remove", "移除", "刪除", "del", "delete", "rm"):
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
    if cmd in ("enter", "進場", "buy", "in", "買進"):
        if not args:
            return "用法：/enter 代號或名稱，例如 /enter 2330"
        code, disp = _resolve_one(args[0])
        if code is None:
            return disp
        new = enter_batch(code)
        tail = "　⚠️三批已滿，不再加碼" if new >= MAX_BATCHES else ""
        return f"✅ {disp} 已進第 {new} 批（{new}/{MAX_BATCHES}）{tail}"
    if cmd in ("exit", "出場", "sell", "out", "賣出", "清空"):
        if not args:
            return "用法：/exit 代號或名稱，例如 /exit 2330"
        code, disp = _resolve_one(args[0])
        if code is None:
            return disp
        had = exit_position(code)
        return (f"🗑 {disp} 已全數出場、清空部位" if had
                else f"{disp} 本來就沒有部位。")
    if cmd in ("position", "部位", "pos", "持股"):
        held = held_positions()
        if not held:
            return "📦 目前沒有任何部位。"
        return "📦 目前部位：\n" + "\n".join(
            f"・{c}：{b}/{MAX_BATCHES} 批" for c, b in held.items())
    if cmd in ("predict", "預測", "查預測", "查", "p", "pred"):
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
            out = format_prediction(_name_for(code), rec["date"], rec["prediction"])
            rv = rec.get("review")
            if rv:
                hit = "命中 ✅" if rv.get("success") else "未中 ❌"
                out += (f"\n\n──── 復盤 ────\n"
                        f"🎯 {hit}（實際{rv.get('direction_actual', '—')}）")
                if rv.get("critique"):
                    out += f"\n💬 檢討：{rv['critique']}"
            return out
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
    if cmd in ("forecast", "即時預測", "即時", "現在預測", "f"):
        _git_pull()                       # 取最新 history 供教訓回饋
        if args:
            code, disp = _resolve_one(args[0])
            if code is None:
                return disp
            cfg = next((c for c in effective_stocks().values()
                        if c.get("code") == code), {})
            tg.send("⏳ 即時計算中（約 30–60 秒）…")
            try:
                return _forecast_stock(code, _name_for(code), cfg.get("supports"))
            except Exception as e:
                return f"⚠️ 即時計算失敗：{e}"
        tg.send("⏳ 即時計算大盤中（約 30–60 秒）…")
        try:
            return _forecast_market()
        except Exception as e:
            return f"⚠️ 即時計算失敗：{e}"
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
    register_commands(token)   # 更新 Telegram 指令選單
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
