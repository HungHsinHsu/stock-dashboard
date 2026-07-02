import json
import os
import re
import subprocess
import time
import requests

from core.data import (
    resolve_stocks,
    fetch_daily, fetch_index, fetch_us_overnight, fetch_taifex_detail,
    fetch_foreign_flow, fetch_margin, fetch_top_turnover,
)
from core.screener import scan as _scan
from core.watchlist import add_stock, remove_stock, effective_stocks
from core.positions import (
    enter_batch, exit_position, held_positions, get_batches, MAX_BATCHES,
)
from core.store import load_history, get_record
from core.review import hit_rate
from core.llm import generate_text
from core.market import market_summary
from core.indicators import compute_indicators
from core.predict import (
    make_prediction, make_market_prediction,
    format_prediction, format_market_prediction,
)
from core.lessons import lessons_prompt
from core.textclean import humanize
from core.strategy import RULEBOOK, OPERATIONS
from core.config import DASHBOARD_URL
from core import db
import core.telegram as tg
from datetime import datetime, timezone, timedelta

STATE_PATH = "bot_state.json"

# 目前正在處理的擁有者（Telegram=admin；網頁 chatbox=登入的 username）。
# 機器人單執行緒逐則處理，用模組層變數當上下文即可。
_ACTIVE_OWNER = "admin"


def _owner():
    return _ACTIVE_OWNER

HELP = "\n".join([
    "📋 指令清單（代號或中文名稱皆可）",
    "",
    "—— 預測（預判下一交易日，即時試算）——",
    "/預測　預測明天大盤",
    "/預測 2330　預測明天個股",
    "",
    "—— 復盤（查今天已記錄的預測與命中/檢討）——",
    "/復盤　今天各股預測摘要",
    "/復盤 2330　單檔詳細＋命中與檢討",
    "",
    "—— 立即產生正式開盤預測（排程沒發車時手動補）——",
    "/開盤　立即產生今日大盤＋個股預測並記錄",
    "/開盤 00830　只補算某檔並寫進資料庫（早上沒跑成功時）",
    "",
    "—— 選股掃描 ——",
    "/選股　依回檔承接法從前150大成交股找承接點候選",
    "",
    "—— 股票清單 ——",
    "/add 2330　加入（三段支撐用均線自動判斷）",
    "/remove 2330　移除",
    "/list　目前清單",
    "",
    "—— 部位（回檔承接法分三批）——",
    "/enter 2330　記錄進一批",
    "/exit 2330　出場、清空部位",
    "/position　目前持有批數",
    "",
    "/help　說明",
    "",
    "—— 直接問股票問題 ——",
    "不用指令，直接打字問即可，例如：",
    "「台積電站上均線了嗎？」「今天大盤氣氛如何？」",
    "（技術/系統問題請找開發端，機器人只聊股票）",
    "",
    "（英文也通：/predict /forecast /review /add /remove /list"
    " /enter /exit /position /help）",
])

# Telegram 指令選單（打「/」會跳出，附中文說明）。command 須小寫英文。
BOT_COMMANDS = [
    ("predict", "預測下一交易日（即時試算，加代號看單檔）"),
    ("review", "復盤：查今天的預測與命中/檢討"),
    ("morning", "立即產生今日開盤預測（排程沒發車時手動補）"),
    ("scan", "選股掃描：依回檔承接法從全市場找承接點候選"),
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
    if db.db_enabled():
        return                                    # 資料在 DB，不需拉 git
    ref = os.environ.get("GITHUB_REF_NAME", "main")
    try:
        subprocess.run(["git", "pull", "--rebase", "origin", ref], check=False)
    except Exception as e:
        print("git pull 失敗：", e)


def _name_for(code):
    for n, cfg in effective_stocks(_owner()).items():
        if cfg.get("code") == str(code):
            return n
    return f"({code})"


def _latest_for(records, code):
    recs = [r for r in records if r.get("stock") == str(code) and r.get("prediction")]
    return max(recs, key=lambda r: r.get("date", ""), default=None)


QA_SYSTEM = (
    "你是台股投資討論助手，服務單一使用者。用自然、口語的繁體中文回答，"
    "精簡扼要（沒必要不長篇），必要時分點。"
    "會提供你『使用者的追蹤清單、各股最新預測與復盤、目前部位、命中率』當背景，"
    "請善用這些資料回答；資料沒有的就照你的台股知識回答，並老實說是一般性看法。"
    "重要：這是投資討論、不是保證獲利的投顧建議，涉及買賣決策時提醒風險、由使用者自行判斷。"
    "不要杜撰未提供的具體數字或新聞；不確定就說不確定。"
    "禁止出現程式變數/欄位名（如 hold_ma20、signal、direction 等），一律用中文說法。"
    "嚴格限定主題：只回答台股／投資相關問題。"
    "若使用者問的是程式/系統/bug 等技術問題，請回：這類技術問題請找開發端（Claude Code）處理。"
    "若問的是與台股／投資完全無關的內容（閒聊、其他領域、生活雜項等），"
    "不要回答其內容，只婉拒並提醒：「我只負責台股討論，這題幫不上忙喔 🙏」。"
    "\n\n以下是你必須遵循的交易策略手冊；回答進出場/選股/紀律相關問題時"
    "一律依這套規則，不要自創別的策略：\n" + RULEBOOK
    + "\n\n以下是本系統的運作方式；回答『幾點出報告/什麼時候復盤/怎麼運作』"
    "這類問題時務必依此，不要自己編時間：\n" + OPERATIONS
)


def _qa_context():
    """組出給問答用的精簡背景：清單＋各股最新預測/復盤＋部位＋命中率。"""
    records = load_history()
    stocks = effective_stocks(_owner())
    lines = ["【背景資料（僅供參考，非投顧建議）】",
             "追蹤清單：" + "、".join(stocks.keys())]
    for name, cfg in stocks.items():
        rec = _latest_for(records, cfg["code"])
        if not rec:
            continue
        p = rec.get("prediction") or {}
        seg = (f"- {name}：{rec.get('date','')} 預測方向{p.get('direction','—')}"
               f"／訊號{p.get('signal','—')}")
        rv = rec.get("review") or {}
        if rv.get("success") is not None:
            seg += f"，復盤{'命中' if rv['success'] else '未中'}"
        lines.append(seg)
    held = held_positions(_owner())
    if held:
        lines.append("目前部位：" + "、".join(
            f"{c} {b}/{MAX_BATCHES}批" for c, b in held.items()))
    rate = hit_rate(records)
    if rate is not None:
        lines.append(f"歷史大盤/個股方向命中率：{rate * 100:.0f}%")
    return "\n".join(lines)


def _answer_question(text):
    """把非指令訊息當成股票問題，帶背景交給 Claude 回答。"""
    tg.send("🤔 想一下…")
    try:
        _git_pull()
        user = f"{_qa_context()}\n\n使用者問題：{text}"
        return humanize(generate_text(QA_SYSTEM, user))
    except Exception as e:
        return f"⚠️ 回答失敗：{e}"


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
    tf_floor = str(idx_df.index[-1].date()) if not idx_df.empty else None
    tf_detail = fetch_taifex_detail(min_date=tf_floor)
    tf = tf_detail["pct"] if tf_detail else None
    tf_asof = tf_detail["date"] if tf_detail else None
    ind = compute_indicators(idx_df, {})
    mpred = make_market_prediction(ind, us, market, tf,
                                   lessons=lessons_prompt(load_history(), "大盤"),
                                   taifex_asof=tf_asof)
    card = format_market_prediction(str(idx_df.index[-1].date()), mpred,
                                    forecast=True)
    return "🔮 即時試算\n\n" + card


def _forecast_stock(code, name, supports):
    """即時重新計算個股開盤前預測。"""
    df = fetch_daily(code)
    if df.empty:
        return f"⚠️ {name} 抓不到資料。"
    market = market_summary(fetch_index())
    us = fetch_us_overnight()
    foreign = fetch_foreign_flow(code)
    margin = fetch_margin(code)
    ind = compute_indicators(df, supports or {})
    pred = make_prediction(ind, name, market=market, us_overnight=us, code=code,
                           foreign=foreign, batches=get_batches(code, _owner()),
                           lessons=lessons_prompt(load_history(), code), margin=margin)
    card = format_prediction(name, str(df.index[-1].date()), pred, forecast=True)
    return "🔮 即時試算\n\n" + card


def _run_morning_now(query=None):
    """立即產生今日「正式」開盤預測並『寫進 DB』（等同排程那班，非即時試算）。
    記錄後收盤會自動復盤、算進命中率——不是只印卡片給你看。
    寫一次鎖定：今天已有預測就不重算，改把既有卡重貼出來。
    query 有給 → 只補算該檔（例如 00830 早上失敗，補寫進今天的正式預測）。"""
    from jobs import morning
    _git_pull()
    today = str(datetime.today().date())

    if query:                                   # 只補算單一個股（寫進 DB）
        code, disp = _resolve_one(query)
        if code is None:
            return disp
        rec = get_record(load_history(), today, code)
        if rec and rec.get("prediction"):       # 已鎖定 → 重貼既有紀錄，不重算
            card = format_prediction(_name_for(code) or disp, today, rec["prediction"])
            return (f"📌 {disp} 今天已經有正式開盤預測了（寫一次鎖定、不重算）：\n\n"
                    + card + "\n\n收盤會自動復盤、算進命中率。")
        cfg = next((c for c in effective_stocks(_owner()).values()
                    if c.get("code") == code), {"code": code})
        tg.send(f"⏳ 正在為 {disp} 補算今日正式開盤預測並寫進資料庫…")
        try:
            produced = morning.run(stocks={disp: cfg})
        except Exception as e:
            return f"⚠️ 補算失敗：{e}"
        if any(r.get("stock") == code for r in produced):
            return (f"✅ {disp} 今日開盤預測已產生並『寫進資料庫』，收盤會自動復盤、"
                    "算進命中率（不是只印給你看）。")
        return (f"⚠️ {disp} 這次 AI 試算仍失敗（資料有到、多半是暫時限流）。"
                f"稍等一下再試 /開盤 {code} 即可。")

    mrec = get_record(load_history(), today, "大盤")
    if mrec and mrec.get("prediction"):
        card = format_market_prediction(today, mrec["prediction"])
        return ("📌 今天已經產生過開盤預測了（寫一次就鎖定、不重算，避免竄改歷史）。\n"
                "這是今天已記錄的大盤預測：\n\n" + card +
                "\n\n某檔早上沒跑成功可用 /開盤 代號 補寫；想試算下一交易日用 /預測。")
    tg.send("⏳ 正在產生今日開盤預測（大盤＋個股，約 1–3 分鐘）…")
    try:
        produced = morning.run()          # 內部會推播大盤卡與個股總表
    except Exception as e:
        return f"⚠️ 產生開盤預測失敗：{e}"
    return (f"✅ 今日開盤預測已產生並記錄（大盤＋個股 {len(produced)} 檔，見上方卡片），"
            "之後收盤會自動復盤、算進命中率。")


def _scan_candidates_digest(top=120, limit=12):
    """依回檔承接法規則掃『當日成交額前 top 檔』，挑出現在符合進場的候選。"""
    uni = fetch_top_turnover(top)
    if not uni:
        return "⚠️ 抓不到市場清單（TWSE 沒回應），稍後再試 /選股。"
    name = {c: nm for c, nm in uni}
    stats = {"ok": 0}

    def _f(c):
        df = fetch_daily(c, months=5, workers=2)
        if df is not None and not getattr(df, "empty", True):
            stats["ok"] += 1
        return df

    cands = _scan([c for c, _ in uni], fetch=_f, foreign_lookup=fetch_foreign_flow,
                  limit=limit, pause=0.05)
    if not cands:
        if stats["ok"] == 0:
            return (f"⚠️ 清單抓到 {len(uni)} 檔，但個股歷史 0 檔抓成功——多半是 TWSE 限流／"
                    "擋住連續請求。稍等 1–2 分鐘再試一次 /選股。")
        return (f"⚠️ 抓到 {len(uni)} 檔、成功讀取 {stats['ok']} 檔，但都不符合。稍後再試。")
    lines = [
        f"🔎 選股掃描（回檔承接法・前 {top} 大成交股，相對最好的前 {len(cands)} 名）",
        "📏 評選標準（分數高→前）：訊號 進場>觀望>避開　＞　回檔到支撐附近"
        "　＞　收盤站穩　＞　量縮(賣壓衰竭)　＞　離均線越近；禁區/槓桿股不列。", ""]
    for x in cands:
        nm = name.get(x["code"], x["code"])
        where = x.get("at_batch") or x["kind"]
        lines.append(f"・[{x['signal']}] {nm} ({x['code']})：{where}｜{x['reason']}")
    lines += ["", "（進場＝四關到位可接；觀望＝趨勢沒破、等回檔或確認；避開＝已跌破季線，墊底參考）",
              "※ 已逐檔補查外資、資料不齊者已排除，訊號含外資；要追蹤用 /add 代號",
              f"🔗 {DASHBOARD_URL}"]
    return "\n".join(lines)


def _scan_command():
    """/選股：優先回『今日收盤後已存的選股清單』（即時、不再打 TWSE）；
    沒有存檔才即時掃（較慢）。"""
    try:
        stored = db.get_state("screen:latest") if db.db_enabled() else None
    except Exception:
        stored = None
    if stored and stored.get("cands"):
        from jobs.screen import _digest
        return _digest(stored.get("date", ""), stored["cands"],
                       stored.get("names", {}), stored.get("top", 150))
    tg.send("⏳ 尚無今日收盤後清單，改即時掃描（約 1–2 分鐘）…")
    return _scan_candidates_digest()


def _resolve_in_watchlist(query):
    """先在使用者追蹤清單裡解析（即使 TWSE 名單抓不到也能用）。
    回 (code, 顯示名) 或 None。"""
    q = (query or "").strip()
    if not q:
        return None
    idx = []  # [(code, 中文名, 顯示名)]
    for disp, cfg in effective_stocks(_owner()).items():
        code = cfg.get("code")
        m = re.match(r"^(.*?)\s*[（(]\s*\w+\s*[)）]\s*$", disp)
        name = (m.group(1).strip() if m else disp).strip()
        idx.append((code, name, disp))
    if q.isdigit():
        return next(((c, d) for c, n, d in idx if c == q), None)
    exact = [(c, d) for c, n, d in idx if n == q]
    if exact:
        return exact[0]
    partial = [(c, d) for c, n, d in idx if q in n]
    return partial[0] if len(partial) == 1 else None


def _resolve_one(query):
    """把代號或名稱解析成單一 (code, 顯示名)。回 (code, disp) 或 (None, 錯誤字串)。"""
    local = _resolve_in_watchlist(query)   # 清單內優先，不依賴外部名單可用性
    if local:
        return local
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
    if db.db_enabled():
        return db.get_state("bot_offset", 0) or 0
    if not os.path.exists(path):
        return 0
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("offset", 0)
    except Exception:
        return 0


def _save_offset(offset, path=STATE_PATH):
    if db.db_enabled():
        db.set_state("bot_offset", offset)
        return
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


# 所有指令別名（用來判斷「這是不是指令」→ 指令回覆一定附網站連結）
KNOWN_CMDS = {
    "start", "help", "說明", "幫助", "指令",
    "list", "清單", "列表", "add", "加入", "新增",
    "remove", "移除", "刪除", "del", "delete", "rm",
    "enter", "進場", "buy", "in", "買進",
    "exit", "出場", "sell", "out", "賣出", "清空",
    "position", "部位", "pos", "持股",
    "predict", "預測", "forecast", "即時預測", "即時", "現在預測", "p", "pred", "f",
    "review", "復盤", "結果", "查", "查預測", "查詢", "紀錄", "記錄",
    "開盤", "產生預測", "推播", "跑預測", "morning", "run",
    "選股", "掃描", "選標的", "找標的", "scan", "screen",
}


def _is_command(text):
    stripped = (text or "").strip()
    if not stripped:
        return False
    if stripped.startswith("/"):
        return True
    first = stripped.split()[0].lower().lstrip("/").split("@")[0]
    return first in KNOWN_CMDS


def _append_link(reply):
    """指令回覆一定附上網站連結（已含就不重複）。"""
    if not reply or not DASHBOARD_URL:
        return reply
    if DASHBOARD_URL in reply:
        return reply
    return reply + f"\n\n🔗 看網站：{DASHBOARD_URL}"


def handle(text, owner="admin"):
    """解析一條訊息並回覆。owner=清單/部位的擁有者。
    指令的回覆一定附網站連結；自由聊天問答則不附。"""
    global _ACTIVE_OWNER
    _ACTIVE_OWNER = owner or "admin"
    reply = _dispatch(text)
    if reply is not None and _is_command(text):
        reply = _append_link(reply)
    return reply


def _dispatch(text):
    """實際分派指令/問答，回覆字串（None=不回）。"""
    parts = text.strip().split()
    if not parts:
        return None
    cmd = parts[0].lower().lstrip("/").split("@")[0]
    args = parts[1:]

    if cmd in ("start", "help", "說明", "幫助", "指令"):
        return HELP
    if cmd in ("list", "清單", "列表"):
        names = list(effective_stocks(_owner()).keys())
        return "📋 目前追蹤清單：\n" + "\n".join(f"・{n}" for n in names)
    if cmd in ("add", "加入", "新增"):
        if not args:
            return "用法：/add 代號或名稱，例如 /add 2330 或 /add 台積電"
        query = args[0]
        matches = resolve_stocks(query)
        if not matches:
            return f"找不到「{query}」。請確認代號或中文名稱（限上市股票）。"
        if len(matches) > 1:
            return _ambiguous(query, matches)
        code, name = matches[0]
        disp = f"{name} ({code})" if name else f"({code})"
        add_stock(code, name=disp, owner=_owner())
        return f"✅ 已加入 {disp}（三段支撐用均線自動判斷，不必設價位）"
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
        if remove_stock(code, _owner()):
            return f"🗑 已移除 {code}"
        return f"清單中找不到 {code}。"
    if cmd in ("enter", "進場", "buy", "in", "買進"):
        if not args:
            return "用法：/enter 代號或名稱，例如 /enter 2330"
        code, disp = _resolve_one(args[0])
        if code is None:
            return disp
        new = enter_batch(code, owner=_owner())
        tail = "　⚠️三批已滿，不再加碼" if new >= MAX_BATCHES else ""
        return f"✅ {disp} 已進第 {new} 批（{new}/{MAX_BATCHES}）{tail}"
    if cmd in ("exit", "出場", "sell", "out", "賣出", "清空"):
        if not args:
            return "用法：/exit 代號或名稱，例如 /exit 2330"
        code, disp = _resolve_one(args[0])
        if code is None:
            return disp
        had = exit_position(code, _owner())
        return (f"🗑 {disp} 已全數出場、清空部位" if had
                else f"{disp} 本來就沒有部位。")
    if cmd in ("position", "部位", "pos", "持股"):
        held = held_positions(_owner())
        if not held:
            return "📦 目前沒有任何部位。"
        return "📦 目前部位：\n" + "\n".join(
            f"・{c}：{b}/{MAX_BATCHES} 批" for c, b in held.items())
    # 預測＝預判「下一交易日」（即時試算；不含今天的命中結果）
    if cmd in ("predict", "預測", "forecast", "即時預測", "即時",
               "現在預測", "p", "pred", "f"):
        _git_pull()                       # 取最新 history 供教訓回饋
        if args:
            code, disp = _resolve_one(args[0])
            if code is None:
                return disp
            cfg = next((c for c in effective_stocks(_owner()).values()
                        if c.get("code") == code), {})
            tg.send("⏳ 即時試算中（約 30–60 秒）…")
            try:
                return _forecast_stock(code, _name_for(code), cfg.get("supports"))
            except Exception as e:
                return f"⚠️ 即時試算失敗：{e}"
        tg.send("⏳ 即時試算大盤中（約 30–60 秒）…")
        try:
            return _forecast_market()
        except Exception as e:
            return f"⚠️ 即時試算失敗：{e}"
    # 復盤＝查「今天已記錄」的預測與命中/檢討（過去式，跟預測明天分開）
    if cmd in ("review", "復盤", "結果", "查", "查預測", "查詢", "紀錄", "記錄"):
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
                    out += f"\n💬 檢討：{humanize(rv['critique'])}"
            return out
        # 無參數 → 清單內各股摘要
        lines = ["📋 今日各股預測摘要（詳細：/復盤 代號）"]
        any_rec = False
        for name, cfg in effective_stocks(_owner()).items():
            rec = _latest_for(records, cfg["code"])
            if rec:
                any_rec = True
                lines.append(_summary_line(name, rec))
        if not any_rec:
            return "目前清單內的股票都還沒有預測紀錄。"
        return "\n".join(lines)
    # 開盤＝立即產生今日「正式」開盤預測（記錄＋推播），補排程沒發車時的手動觸發
    if cmd in ("開盤", "產生預測", "推播", "跑預測", "morning", "run"):
        return _run_morning_now(args[0] if args else None)
    # 選股掃描：依回檔承接法規則從全市場找出當下的承接點候選
    if cmd in ("選股", "掃描", "選標的", "找標的", "scan", "screen"):
        try:
            return _scan_command()
        except Exception as e:
            return f"⚠️ 選股失敗：{e}"
    # 以「/」開頭卻沒對到任何指令 → 打錯指令
    if text.strip().startswith("/"):
        return "不認得的指令。傳 /help 看用法。"
    # 其餘自由文字 → 當成股票問題，交給 Claude 回答
    return _answer_question(text)


def process_web_message(text, owner="admin"):
    """處理來自網頁 chatbox（或 LINE）的訊息：重用 handle()，回最終回覆字串。
    owner=登入的帳號，用來讀寫『那個帳號自己的』清單/部位。

    handle() 內部有些指令會用 tg.send 發『⏳ 計算中』等中間提示；網頁端改用
    轉圈圈提示，這裡把 tg 暫時換成 no-op，避免那些中間訊息外洩到 Telegram。
    """
    global tg
    saved = tg

    class _Null:
        @staticmethod
        def send(_t):
            return True

    tg = _Null
    try:
        return handle(text, owner=owner) or ""
    except Exception as e:            # 單則失敗不影響機器人存活
        return f"⚠️ 處理失敗：{e}"
    finally:
        tg = saved


def drain_web_queue():
    """處理網頁 chatbox 佇列裡所有待辦，把回覆寫回 DB。"""
    if not db.db_enabled():
        return
    try:
        pending = db.pending_chats()
    except Exception as e:
        print("chat_queue 讀取失敗：", e)
        return
    for item in pending:
        reply = process_web_message(item.get("text", ""),
                                    owner=item.get("who") or "admin")
        try:
            db.complete_chat(item["id"], reply)
        except Exception as e:
            print("chat_queue 回寫失敗：", e)


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
    if db.db_enabled():
        return                                    # 資料已寫進 DB，不必 commit
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


def _notify_started():
    """開機/重啟完成後主動通知使用者（不含股票內容，只報上線）。"""
    sha = (os.environ.get("GITHUB_SHA") or "")[:7]
    ver = f"（版本 {sha}）" if sha else ""
    try:
        tg.send(f"🤖 機器人已重啟完成，開始待命 ✅{ver}\n"
                "可直接問股票問題，或用 /help 看指令。")
    except Exception as e:
        print("開機通知失敗：", e)


# ── 常駐排程備援：GitHub cron 常延遲/漏班，機器人自己在時間到時補跑 ──
# 各班留 GRACE 分鐘讓 GitHub 先跑；GitHub 已做過(冪等檢查)就略過，避免重覆。
_SCHED_SLOTS = [("morning", 7, 40), ("morning", 8, 10),
                ("evening", 15, 20), ("screen", 15, 35), ("evening", 18, 0)]
_SCHED_GRACE_MIN = 10
_sched_done = set()          # 記憶體備援（無 DB 時用）


def _tw_now():
    return datetime.now(timezone.utc) + timedelta(hours=8)   # 台灣 UTC+8


def _slot_done(key):
    if key in _sched_done:
        return True
    if db.db_enabled():
        try:
            return bool(db.get_state(key))
        except Exception:
            return False
    return False


def _mark_slot(key):
    _sched_done.add(key)
    if db.db_enabled():
        try:
            db.set_state(key, True)
        except Exception:
            pass


def _already_produced(job, date):
    """GitHub 那班是否已做過（避免重覆）：morning 看今日大盤預測、evening 看大盤復盤、
    screen 看今日選股結果是否已存。已做過就不重跑。"""
    if job == "screen":
        try:
            s = db.get_state("screen:latest") or {}
        except Exception:
            return False
        return s.get("date") == date
    try:
        m = get_record(load_history(), date, "大盤")
    except Exception:
        return False
    if not m:
        return False
    if job == "morning":
        return bool(m.get("prediction"))
    return bool((m.get("review") or {}).get("critique"))


def _run_scheduled_jobs():
    """機器人常駐備援排程：時間到（過 GRACE 分鐘）就自己補跑，冪等、只在 GitHub 沒做時才動。"""
    now = _tw_now()
    if now.weekday() >= 5:                    # 週六日不跑
        return
    today = now.date().isoformat()
    now_min = now.hour * 60 + now.minute
    for job, hh, mm in _SCHED_SLOTS:
        if now_min < hh * 60 + mm + _SCHED_GRACE_MIN:
            continue                          # 還沒到（或還在讓 GitHub 先跑的緩衝內）
        if job == "morning" and now_min > 10 * 60:
            continue                          # 過了早上 10:00 就別再補「開盤」預測
        key = f"sched_done:{job}:{hh:02d}{mm:02d}:{today}"
        if _slot_done(key):
            continue                          # 今天這個時段已處理過
        _mark_slot(key)                       # 先標記，避免重試風暴
        if _already_produced(job, today):
            print(f"[sched] {job} {hh:02d}:{mm:02d} GitHub 已做過，略過")
            continue
        try:
            print(f"[sched] GitHub 沒準時發車，機器人備援補跑 {job}（{hh:02d}:{mm:02d} 班）")
            if job == "morning":
                from jobs import morning
                morning.run()
            elif job == "screen":
                from jobs import screen
                screen.run()
            else:
                from jobs import evening
                evening.run()
        except Exception as e:
            print(f"[sched] {job} 補跑失敗：", e)


def run(loop=False):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("缺 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID")
        return
    db.migrate_from_json()     # DB 首次啟用時匯入舊 JSON（無 DB 則 no-op）
    db.migrate_owner_data()
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
    _notify_started()          # 上線後主動報「重啟完成」
    while time.monotonic() < deadline:
        try:
            _run_scheduled_jobs()   # 常駐備援：GitHub 排程誤點時自己補跑預測/復盤
        except Exception as e:
            print("排程備援檢查失敗：", e)
        drain_web_queue()      # 先處理網頁 chatbox 的待辦
        try:
            # 長輪詢縮到 15 秒，讓網頁 chatbox 的訊息較快被處理
            updates = get_updates(token, offset, long_poll=15)
        except Exception as e:
            print("getUpdates 失敗，稍後重試：", e)
            time.sleep(5)
            continue
        drain_web_queue()      # 輪詢回來後再處理一次，降低網頁延遲
        if not updates:
            continue  # 長輪詢已阻塞約 15 秒，直接再聽
        new_off, handled = _process(updates, chat_id)
        offset = max(offset, new_off)
        _save_offset(offset)
        _commit_push()  # 即時把清單/offset 推回，排程才讀得到
        print(f"handled={handled} offset={offset}")


if __name__ == "__main__":
    import sys
    run(loop="--loop" in sys.argv)
