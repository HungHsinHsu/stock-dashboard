"""可切換的儲存後端：設了環境變數 DATABASE_URL 就走 Postgres(Supabase)，
否則維持 JSON 檔。各 store 模組(store/positions/lessons/watchlist/bot)在
load/save 時呼叫這裡；db_enabled() 為 False 時完全不碰 DB。

資料以最小映射存放（多用 jsonb 直接存原本的 dict），讓 JSON↔DB 結構一致、
遷移無痛。psycopg2 僅在真正連線時才 import。
"""
import json
import os

_schema_ready = False


def database_url():
    return os.environ.get("DATABASE_URL", "").strip()


def db_enabled():
    return bool(database_url())


def _connect():
    import psycopg2
    url = database_url()
    if "sslmode=" not in url:                      # Supabase 需要 SSL
        url += ("&" if "?" in url else "?") + "sslmode=require"
    return psycopg2.connect(url)


def _ensure_schema(cur):
    cur.execute("""CREATE TABLE IF NOT EXISTS predictions(
        date text, stock text, prediction jsonb, review jsonb,
        PRIMARY KEY(date, stock))""")
    cur.execute("""CREATE TABLE IF NOT EXISTS positions(
        stock text PRIMARY KEY, batches int, updated text)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS lessons(
        stock text, date text, lesson text, PRIMARY KEY(stock, date))""")
    cur.execute("""CREATE TABLE IF NOT EXISTS watchlist(
        code text PRIMARY KEY, data jsonb)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS app_state(
        key text PRIMARY KEY, value jsonb)""")
    # 網頁 chatbox（及未來 LINE）→ 機器人代跑 的訊息佇列
    cur.execute("""CREATE TABLE IF NOT EXISTS chat_queue(
        id bigserial PRIMARY KEY,
        source text,
        who text,
        text text,
        reply text,
        status text DEFAULT 'pending',
        ts timestamptz DEFAULT now())""")
    cur.execute("ALTER TABLE chat_queue ADD COLUMN IF NOT EXISTS who text")
    # chatbox 使用者（admin 由 secrets 認定；此表存 admin 新增的一般使用者）
    cur.execute("""CREATE TABLE IF NOT EXISTS users(
        username text PRIMARY KEY,
        pw_hash text,
        role text DEFAULT 'user',
        created text)""")


def _run(fn):
    global _schema_ready
    conn = _connect()
    try:
        with conn:
            with conn.cursor() as cur:
                if not _schema_ready:
                    _ensure_schema(cur)
                    _schema_ready = True
                return fn(cur)
    finally:
        conn.close()


def _loads(v):
    return json.loads(v) if isinstance(v, str) else v


def _dumps(v):
    return json.dumps(v, ensure_ascii=False)


# ── predictions（歷史）──────────────────────────────────────────
def load_predictions():
    def q(cur):
        cur.execute("SELECT date, stock, prediction, review FROM predictions")
        return [{"date": d, "stock": s, "prediction": _loads(p), "review": _loads(r)}
                for d, s, p, r in cur.fetchall()]
    return _run(q)


def save_predictions(records):
    def q(cur):
        for r in records:
            cur.execute(
                """INSERT INTO predictions(date, stock, prediction, review)
                   VALUES(%s, %s, %s::jsonb, %s::jsonb)
                   ON CONFLICT(date, stock) DO UPDATE SET
                     prediction=EXCLUDED.prediction, review=EXCLUDED.review""",
                (r.get("date"), r.get("stock"),
                 _dumps(r.get("prediction")), _dumps(r.get("review"))))
    _run(q)


# ── positions（部位）────────────────────────────────────────────
def load_positions():
    def q(cur):
        cur.execute("SELECT stock, batches, updated FROM positions")
        return {s: {"batches": b, "updated": u} for s, b, u in cur.fetchall()}
    return _run(q)


def save_positions(positions):
    def q(cur):
        cur.execute("DELETE FROM positions")
        for code, rec in positions.items():
            cur.execute(
                """INSERT INTO positions(stock, batches, updated)
                   VALUES(%s, %s, %s)""",
                (str(code), int(rec.get("batches", 0)), rec.get("updated")))
    _run(q)


# ── lessons（教訓）──────────────────────────────────────────────
def load_lessons():
    def q(cur):
        cur.execute("SELECT stock, date, lesson FROM lessons ORDER BY date")
        return [{"stock": s, "date": d, "lesson": l} for s, d, l in cur.fetchall()]
    return _run(q)


def save_lessons(lessons):
    def q(cur):
        cur.execute("DELETE FROM lessons")
        for x in lessons:
            cur.execute(
                """INSERT INTO lessons(stock, date, lesson) VALUES(%s, %s, %s)
                   ON CONFLICT(stock, date) DO UPDATE SET lesson=EXCLUDED.lesson""",
                (x.get("stock"), x.get("date"), x.get("lesson")))
    _run(q)


# ── watchlist（追蹤清單）────────────────────────────────────────
def load_watchlist():
    def q(cur):
        cur.execute("SELECT code, data FROM watchlist")
        return {c: _loads(d) for c, d in cur.fetchall()}
    return _run(q)


def save_watchlist(wl):
    def q(cur):
        cur.execute("DELETE FROM watchlist")
        for code, data in wl.items():
            cur.execute(
                "INSERT INTO watchlist(code, data) VALUES(%s, %s::jsonb)",
                (str(code), _dumps(data)))
    _run(q)


# ── app_state（bot 輪詢 offset 等）──────────────────────────────
def get_state(key, default=None):
    def q(cur):
        cur.execute("SELECT value FROM app_state WHERE key=%s", (key,))
        row = cur.fetchone()
        return _loads(row[0]) if row else default
    return _run(q)


def set_state(key, value):
    def q(cur):
        cur.execute(
            """INSERT INTO app_state(key, value) VALUES(%s, %s::jsonb)
               ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value""",
            (key, _dumps(value)))
    _run(q)


def get_states_by_prefix(prefix):
    """回 {key: value}，key 以 prefix 開頭（用於列舉各帳號的清單）。"""
    def q(cur):
        cur.execute("SELECT key, value FROM app_state WHERE key LIKE %s",
                    (prefix + "%",))
        return {k: _loads(v) for k, v in cur.fetchall()}
    return _run(q)


def migrate_owner_data():
    """把舊的『單一』watchlist/positions 一次性搬到 admin 帳號命名空間(app_state)。"""
    if not db_enabled():
        return
    if get_state("wl:admin") is None:
        old = load_watchlist()          # 舊 watchlist 表
        if old:
            set_state("wl:admin", old)
    if get_state("pos:admin") is None:
        oldp = load_positions()         # 舊 positions 表
        if oldp:
            set_state("pos:admin", oldp)
    migrate_seed_default_watchlist()


# 過去寫死在程式的預設種子股（現已改為可自由增刪）；一次性種進 admin 清單。
_DEFAULT_SEED = {"2344": {"name": "華邦電 (2344)"}}


def migrate_seed_default_watchlist():
    """把舊的『程式預設股』一次性種進 admin 清單，之後使用者可自由增刪。
    用旗標確保只種一次——就算之後被移除，也不會再被種回來。"""
    if not db_enabled():
        return
    if get_state("seed:base_done"):
        return
    wl = get_state("wl:admin", {}) or {}
    for code, info in _DEFAULT_SEED.items():
        wl.setdefault(code, info)
    set_state("wl:admin", wl)
    set_state("seed:base_done", True)


# ── chat_queue（網頁 chatbox / LINE → 機器人代跑）────────────────
def enqueue_chat(text, source="web", who="admin"):
    """把一則使用者訊息排進佇列，回 id（供之後輪詢回覆）。who=登入帳號。"""
    def q(cur):
        cur.execute(
            "INSERT INTO chat_queue(source, who, text, status) "
            "VALUES(%s, %s, %s, 'pending') RETURNING id", (source, who, text))
        return cur.fetchone()[0]
    return _run(q)


def get_chat_reply(cid):
    """已處理完回回覆字串，尚未處理回 None。"""
    def q(cur):
        cur.execute(
            "SELECT reply FROM chat_queue WHERE id=%s AND status='done'", (cid,))
        row = cur.fetchone()
        return row[0] if row else None
    return _run(q)


def pending_chats(limit=5):
    """取尚未處理的訊息（機器人端）。"""
    def q(cur):
        cur.execute(
            "SELECT id, source, who, text FROM chat_queue WHERE status='pending' "
            "ORDER BY id LIMIT %s", (limit,))
        return [{"id": i, "source": s, "who": w, "text": t}
                for i, s, w, t in cur.fetchall()]
    return _run(q)


def complete_chat(cid, reply):
    """機器人處理完，回寫回覆並標記 done；順手清掉一天前的舊紀錄。"""
    def q(cur):
        cur.execute("UPDATE chat_queue SET reply=%s, status='done' WHERE id=%s",
                    (reply, cid))
        cur.execute("DELETE FROM chat_queue WHERE ts < now() - interval '1 day'")
    _run(q)


# ── users（chatbox 使用者管理）──────────────────────────────────
def create_user(username, pw_hash, role="user", created=None):
    def q(cur):
        cur.execute(
            """INSERT INTO users(username, pw_hash, role, created)
               VALUES(%s, %s, %s, %s)
               ON CONFLICT(username) DO UPDATE SET
                 pw_hash=EXCLUDED.pw_hash, role=EXCLUDED.role""",
            (username, pw_hash, role, created))
    _run(q)


def get_user(username):
    def q(cur):
        cur.execute(
            "SELECT username, pw_hash, role FROM users WHERE username=%s",
            (username,))
        row = cur.fetchone()
        return {"username": row[0], "pw_hash": row[1], "role": row[2]} if row else None
    return _run(q)


def list_users():
    def q(cur):
        cur.execute("SELECT username, role, created FROM users ORDER BY username")
        return [{"username": u, "role": r, "created": c} for u, r, c in cur.fetchall()]
    return _run(q)


def delete_user(username):
    def q(cur):
        cur.execute("DELETE FROM users WHERE username=%s", (username,))
    _run(q)


# ── 一次性遷移：DB 為空且有舊 JSON 時，匯入 ─────────────────────
def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def migrate_from_json():
    """DB 啟用且資料表為空時，把現有 JSON 檔一次性匯入。已有資料則跳過。"""
    if not db_enabled():
        return

    def _empty(cur, table):
        cur.execute(f"SELECT 1 FROM {table} LIMIT 1")
        return cur.fetchone() is None

    preds = _read_json("history/predictions.json")
    lessons = _read_json("lessons.json")
    positions = _read_json("positions.json")
    watch = _read_json("watchlist.json")

    def q(cur):
        if preds and _empty(cur, "predictions"):
            for r in preds:
                cur.execute(
                    """INSERT INTO predictions(date, stock, prediction, review)
                       VALUES(%s, %s, %s::jsonb, %s::jsonb)
                       ON CONFLICT(date, stock) DO NOTHING""",
                    (r.get("date"), r.get("stock"),
                     _dumps(r.get("prediction")), _dumps(r.get("review"))))
        if lessons and _empty(cur, "lessons"):
            for x in lessons:
                cur.execute(
                    """INSERT INTO lessons(stock, date, lesson) VALUES(%s, %s, %s)
                       ON CONFLICT(stock, date) DO NOTHING""",
                    (x.get("stock"), x.get("date"), x.get("lesson")))
        if positions and _empty(cur, "positions"):
            for code, rec in positions.items():
                cur.execute(
                    "INSERT INTO positions(stock, batches, updated) VALUES(%s,%s,%s) "
                    "ON CONFLICT(stock) DO NOTHING",
                    (str(code), int(rec.get("batches", 0)), rec.get("updated")))
        if watch and _empty(cur, "watchlist"):
            for code, data in watch.items():
                cur.execute(
                    "INSERT INTO watchlist(code, data) VALUES(%s, %s::jsonb) "
                    "ON CONFLICT(code) DO NOTHING",
                    (str(code), _dumps(data)))
    _run(q)
