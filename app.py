import os
import time
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# 把 Streamlit secret 的 DATABASE_URL 橋接成環境變數，讓 core.db 跟 Actions 一致
try:
    if st.secrets.get("DATABASE_URL"):
        os.environ["DATABASE_URL"] = st.secrets["DATABASE_URL"]
except Exception:
    pass

from core.data import fetch_daily, fetch_index
from core.watchlist import effective_stocks
from core.market import market_summary
from core.store import load_history
from core.review import hit_rate
from core.auth import hash_password, verify_password
from core import db

st.set_page_config(page_title="台股觀察儀表板", layout="wide", page_icon="📊")
st.title("📊 台股觀察儀表板")
if db.db_enabled():
    st.caption("🗄 資料來源：Postgres 資料庫 ✅")
else:
    st.error(
        "⚠️ 未連上資料庫（DATABASE_URL 未設定）。系統一律以 Postgres 為準，"
        "此頁目前只能顯示舊 JSON 快照、預測/復盤可能不是最新或空白。\n\n"
        "請到 Streamlit → 右下 **Manage app / Settings → Secrets**，加入一行："
        "`DATABASE_URL = \"你的 Supabase 連線字串\"`，儲存後 App 會自動重啟即恢復。")

# 多抓一點歷史，週/月線才有足夠根數
@st.cache_data(ttl=3600)
def load_index_df():
    return fetch_index(months=18)


@st.cache_data(ttl=3600)
def load_stock_df(code):
    return fetch_daily(code, months=18)


@st.cache_resource
def _migrate_once():
    try:
        from core import db
        db.migrate_from_json()      # DB 啟用且為空時匯入舊 JSON；無 DB 則 no-op
    except Exception as e:
        print("migrate skipped:", e)
    return True


_migrate_once()


@st.cache_data(ttl=120)
def load_records():
    return load_history()


# ─────────────── 帳號登入 ＋ chatbox（側邊）───────────────
def _secret(key):
    try:
        return st.secrets.get(key)
    except Exception:
        return None


def _login(username, password):
    """回 {'name','role'} 或 None。admin 由 secrets 認定，一般使用者查 DB。"""
    admin_user, admin_pw = _secret("ADMIN_USER"), _secret("ADMIN_PASSWORD")
    if admin_user and username == admin_user and password == admin_pw:
        return {"name": username, "role": "admin"}
    if db.db_enabled():
        try:
            u = db.get_user(username)
        except Exception:
            u = None
        if u and verify_password(password, u["pw_hash"]):
            return {"name": u["username"], "role": u["role"]}
    return None


def _dash_owner():
    u = st.session_state.get("auth_user")
    return u["name"] if u else "admin"


def _web_ask(prompt, who="admin", timeout_s=100):
    """把訊息排進 DB 佇列，等機器人處理完的回覆（最多約 timeout_s 秒）。"""
    if not db.db_enabled():
        return "⚠️ 未連上資料庫，chatbox 無法使用（請先設定 DATABASE_URL）。"
    try:
        cid = db.enqueue_chat(prompt, source="web", who=who)
    except Exception as e:
        return f"⚠️ 送出失敗：{e}"
    with st.spinner("助理思考中…（最多約 1 分鐘）"):
        for _ in range(max(1, timeout_s // 2)):
            time.sleep(2)
            try:
                r = db.get_chat_reply(cid)
            except Exception:
                r = None
            if r is not None:
                return r
    return "⏳ 這次等太久了（機器人可能正在重啟或忙碌），請稍後再試。"


def _render_admin_panel():
    """使用者管理內容（不含 expander；由呼叫端收合）。"""
    if not db.db_enabled():
        st.warning("未連上資料庫，無法管理使用者。")
        return
    st.markdown("**👤 使用者管理**")
    with st.form("add_user", clear_on_submit=True):
        nu = st.text_input("新使用者帳號")
        npw = st.text_input("初始密碼", type="password")
        if st.form_submit_button("➕ 新增使用者"):
            if not nu.strip() or not npw:
                st.error("帳號與密碼都要填。")
            elif nu.strip() == (_secret("ADMIN_USER") or ""):
                st.error("此帳號保留給管理者。")
            else:
                db.create_user(nu.strip(), hash_password(npw), role="user")
                st.success(f"已新增使用者：{nu.strip()}")
                st.rerun()
    users = db.list_users()
    if users:
        st.caption("目前使用者：")
        for u in users:
            c1, c2 = st.columns([3, 1])
            c1.write(f"・{u['username']}")
            if c2.button("刪除", key=f"del_{u['username']}"):
                db.delete_user(u["username"])
                st.rerun()


def render_chat_page():
    """主畫面的『💬 助理』：訊息在固定高度捲動框、輸入框釘在最底（正常聊天體驗）。"""
    st.markdown("### 💬 助理")
    user = st.session_state.get("auth_user")
    if not user:
        st.info("請先在左側側邊欄登入，才能使用助理。")
        return
    if not db.db_enabled():
        st.warning("未連上資料庫，助理無法使用（請先設定 DATABASE_URL）。")
        return
    st.caption("可打指令（/預測 2330、/復盤、/list…）或直接問股票問題。")
    box = st.container(height=460)                 # 固定高度、自己捲動
    for m in st.session_state.get("chat_hist", []):
        box.chat_message(m["role"]).write(m["text"])
    prompt = st.chat_input("輸入訊息…")             # 釘在畫面最底
    if prompt:
        st.session_state.setdefault("chat_hist", []).append(
            {"role": "user", "text": prompt})
        box.chat_message("user").write(prompt)
        reply = _web_ask(prompt, who=_dash_owner())
        st.session_state.chat_hist.append({"role": "assistant", "text": reply})
        st.rerun()


def render_account_sidebar():
    """側邊只放精簡登入；管理面板與入口收進『⚙️ 管理』收合區，不佔版面。"""
    with st.sidebar:
        user = st.session_state.get("auth_user")
        if not user:
            st.markdown("#### 🔐 登入")
            with st.form("login", clear_on_submit=False):
                u = st.text_input("帳號")
                p = st.text_input("密碼", type="password")
                if st.form_submit_button("登入", use_container_width=True):
                    acct = _login(u.strip(), p)
                    if acct:
                        st.session_state.auth_user = acct
                        st.rerun()
                    else:
                        st.error("帳號或密碼錯誤")
            st.caption("登入後到主畫面『💬 助理』。需要帳號找管理者。")
            return
        role_txt = "管理者" if user["role"] == "admin" else "使用者"
        st.caption(f"👤 {user['name']}（{role_txt}）")
        if st.button("登出", use_container_width=True):
            del st.session_state["auth_user"]
            st.rerun()
        if user["role"] == "admin":
            with st.expander("⚙️ 管理", expanded=False):
                _render_admin_panel()
                _render_admin_links()


def _supabase_ref(database_url):
    """從 DATABASE_URL 解析 Supabase 專案代號（pooler 帳號 postgres.<ref> 或 host db.<ref>.supabase.co）。"""
    try:
        from urllib.parse import urlparse
        u = urlparse(database_url or "")
        user = u.username or ""
        if user.startswith("postgres.") and len(user) > len("postgres."):
            return user.split(".", 1)[1]
        host = u.hostname or ""
        if host.startswith("db.") and host.endswith(".supabase.co"):
            return host.split(".")[1]
    except Exception:
        pass
    return ""


def _render_admin_links():
    """管理入口：直接連到『你這個』DB(Supabase) 與部署(Streamlit) 後台。"""
    sb = _secret("SUPABASE_URL")
    if not sb:
        ref = _supabase_ref(os.environ.get("DATABASE_URL", "")
                            or (_secret("DATABASE_URL") or ""))
        sb = (f"https://supabase.com/dashboard/project/{ref}" if ref
              else "https://supabase.com/dashboard/projects")
    stl = _secret("STREAMLIT_APP_URL") or "https://share.streamlit.io/"
    st.markdown("#### 🔗 管理入口（僅管理者）")
    st.markdown(f"- [🗄 Supabase 資料庫（本專案）]({sb})")
    st.markdown(f"- [🚀 Streamlit 部署 / Secrets]({stl})")
    st.caption("點擊直接前往後台，再於該站登入即可。")


render_account_sidebar()


_TF = {"日": None, "週": "W", "月": "ME"}
_CHART_CFG = {"displayModeBar": True, "displaylogo": False, "scrollZoom": True}


def resample_ohlc(df, rule):
    """把日線 OHLC 聚合成週/月線；rule=None 回原日線。"""
    if rule is None or df.empty:
        return df
    agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last"}
    if "Volume" in df.columns:
        agg["Volume"] = "sum"
    # 月線：新版 pandas 用 'ME'、舊版用 'M'，兩者都試以相容不同 Streamlit Cloud 版本
    candidates = ["ME", "M"] if rule == "ME" else [rule]
    for r in candidates:
        try:
            out = df.resample(r).agg(agg).dropna(subset=["Close"])
            break
        except ValueError:
            continue
    else:
        return df
    out["MA20"] = out["Close"].rolling(20).mean()
    return out


def timeframe_radio(key):
    label = st.radio("時間段", list(_TF.keys()), horizontal=True,
                     key=key, label_visibility="collapsed")
    return _TF[label]


def price_fig(df, supports=None, with_volume=True):
    if with_volume and "Volume" in df.columns:
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                            vertical_spacing=0.04, row_heights=[0.74, 0.26])
        show_vol = True
    else:
        fig = make_subplots(rows=1, cols=1)
        show_vol = False
    fig.add_trace(go.Candlestick(
        x=df.index, open=df["Open"], high=df["High"],
        low=df["Low"], close=df["Close"], name="K線",
        increasing_line_color="#e63946", decreasing_line_color="#2a9d8f",
    ), row=1, col=1)
    if "MA20" in df:
        fig.add_trace(go.Scatter(
            x=df.index, y=df["MA20"], name="MA20",
            line=dict(color="#9b59b6", width=1.5)), row=1, col=1)
    colors = {"支撐1 (短期)": "orange", "支撐3 (長期)": "magenta"}
    for name, price in (supports or {}).items():
        fig.add_hline(y=price, line_dash="dash", line_color=colors.get(name, "gray"),
                      annotation_text=f"{name} {price}", annotation_position="top left",
                      annotation_font_size=10, row=1, col=1)
    if show_vol:
        vol_colors = ["#e63946" if c >= o else "#2a9d8f"
                      for c, o in zip(df["Close"], df["Open"])]
        fig.add_trace(go.Bar(x=df.index, y=df["Volume"], name="成交量",
                             marker_color=vol_colors, showlegend=False), row=2, col=1)
        fig.update_yaxes(title_text="量", row=2, col=1, title_font_size=10)
    fig.update_layout(
        height=520, margin=dict(l=8, r=8, t=36, b=8),
        xaxis_rangeslider_visible=False,
        legend=dict(orientation="h", yanchor="bottom", y=1.0,
                    xanchor="left", x=0, font=dict(size=11)),
        dragmode="pan")
    fig.update_xaxes(rangeslider_visible=False, row=1, col=1)
    return fig


from core.textclean import humanize as _humanize


def _md_bullets(text):
    """把檢討文字統一成 markdown 條列（避免全形「・」＋單換行被 markdown 擠成一坨）。"""
    import re
    text = _humanize(text)
    if not isinstance(text, str) or not text.strip():
        return text or ""
    t = re.sub(r"[・•‧]", "\n", text)                 # 全形項目符號 → 換行
    parts = [re.sub(r"^[\-\*\s　]+", "", p).strip()    # 去掉行首既有符號
             for p in t.split("\n")]
    parts = [p for p in parts if p]
    if len(parts) <= 1:
        return text                                   # 無法分點就原樣顯示
    return "\n".join(f"- {p}" for p in parts)


def render_history(records, show_signal):
    """預測歷史表（含復盤命中）。大盤與個股共用，差別只在是否顯示『訊號』欄。"""
    ordered = sorted(records, key=lambda x: x["date"], reverse=True)
    rate = hit_rate(records)
    if rate is not None:
        st.metric("方向命中率", f"{rate * 100:.0f}%")
    rows = []
    for r in ordered:
        p = r.get("prediction") or {}
        rv = r.get("review") or {}
        res = rv.get("results") or {}
        row = {"日期": r["date"]}
        if show_signal:
            row["訊號"] = p.get("signal", "—")
        ac, pc = rv.get("actual_close"), rv.get("prev_close")
        if isinstance(ac, (int, float)) and isinstance(pc, (int, float)) and pc:
            chg = ac - pc
            chg_txt = f"{chg:+.2f}"
            pct_txt = f"{chg / pc * 100:+.2f}%"
        else:
            chg_txt = pct_txt = "—"
        row.update({
            "預測方向": p.get("direction", "—"),
            "信心": p.get("confidence", "—"),
            "實際方向": rv.get("direction_actual", "—"),
            "方向命中": "✅" if res.get("direction") else ("❌" if rv else "—"),
            "收盤": f"{ac:.2f}" if isinstance(ac, (int, float)) else "—",
            "漲跌": chg_txt,
            "漲跌%": pct_txt,
        })
        rows.append(row)

    df_tbl = pd.DataFrame(rows)

    def _redgreen(col):
        # 台股慣例：漲紅(#e63946)、跌綠(#2a9d8f)
        out = []
        for v in col:
            t = str(v)
            if t.startswith("+") or t == "漲":
                out.append("color:#e63946")
            elif t.startswith("-") or t == "跌":
                out.append("color:#2a9d8f")
            else:
                out.append("")
        return out

    color_cols = [c for c in ("預測方向", "實際方向", "漲跌", "漲跌%")
                  if c in df_tbl.columns]
    styled = df_tbl.style.apply(_redgreen, subset=color_cols)
    # 高度隨列數自動撐開，避免擠在小捲動框裡（上限避免過長）
    h = min(len(df_tbl) + 1, 25) * 35 + 3
    st.dataframe(styled, use_container_width=True, hide_index=True, height=h)

    # 檢討（預測失敗的教訓）
    crits = [r for r in ordered if (r.get("review") or {}).get("critique")]
    if crits:
        st.markdown("**📝 檢討紀錄（每日復盤，不論猜對猜錯都檢討）**")
        for r in crits[:8]:
            rv = r["review"]
            p = r.get("prediction") or {}
            hit = "✅" if (rv.get("results") or {}).get("direction") else "❌"
            title = (f"{r['date']}　預測{p.get('direction', '—')} → "
                     f"實際{rv.get('direction_actual', '—')} {hit}")
            with st.expander(title):
                st.markdown(_md_bullets(rv["critique"]))


def _pct(hits, n):
    return f"{hits / n * 100:.0f}% ({hits}/{n})" if n else "—"


def render_history_overview(records, owner="admin"):
    """預測歷史總覽：日期×標的的命中矩陣，最右欄＝當日命中率、最底列＝累積命中率。"""
    if not records:
        st.info("尚無任何預測紀錄。開盤預測與收盤復盤會自動累積在這裡。")
        return
    stocks = effective_stocks(owner)
    code2name = {cfg["code"]: name for name, cfg in stocks.items()}
    code2name["大盤"] = "🌐 大盤"
    targets = ["大盤"] + [cfg["code"] for cfg in stocks.values()]
    RATE_COL = "📊 當日命中率"

    import datetime as _dt

    by = {(r["date"], r.get("stock")): r
          for r in records if r.get("prediction")}
    all_dates = sorted({d for (d, _) in by}, reverse=True)  # 新→舊，全部歷史

    def hit_of(r):
        """命中回 True、未中 False、尚未復盤回 None。"""
        rv = (r or {}).get("review") or {}
        res = rv.get("results") or {}
        if not rv or "direction" not in res:
            return None
        return bool(res.get("direction"))

    def cell(r):
        if r is None:
            return "—"
        d = (r.get("prediction") or {}).get("direction", "") or ""
        h = hit_of(r)
        return f"🔮{d}" if h is None else ("✅" if h else "❌") + d

    # 底列：各標的累積命中率——一律用「全部歷史」計算，不受下方顯示範圍影響
    foot, tot_hits, tot_rev = {"日期": "📊 累積命中率"}, 0, 0
    for t in targets:
        hs = [hit_of(by[(d, t)]) for d in all_dates if (d, t) in by]
        hs = [x for x in hs if x is not None]
        foot[code2name.get(t, t)] = _pct(sum(1 for x in hs if x), len(hs))
        tot_hits += sum(1 for x in hs if x)
        tot_rev += len(hs)
    foot[RATE_COL] = _pct(tot_hits, tot_rev)

    # 顯示範圍控制（日期會越來越多 → 預設只顯示最近 10 天，可切換或挑區間）
    mode = st.radio("顯示範圍", ["最近10天", "最近30天", "全部", "自訂區間"],
                    horizontal=True, key="hist_range")
    if mode == "最近10天":
        show_dates = all_dates[:10]
    elif mode == "最近30天":
        show_dates = all_dates[:30]
    elif mode == "全部":
        show_dates = all_dates
    else:
        dmin = _dt.date.fromisoformat(all_dates[-1])
        dmax = _dt.date.fromisoformat(all_dates[0])
        d_start = _dt.date.fromisoformat(all_dates[min(len(all_dates) - 1, 9)])
        rng = st.date_input("挑選日期區間", value=(d_start, dmax),
                            min_value=dmin, max_value=dmax, key="hist_daterange")
        s, e = rng if isinstance(rng, (list, tuple)) and len(rng) == 2 else (rng, rng)
        show_dates = [d for d in all_dates
                      if s <= _dt.date.fromisoformat(d) <= e]

    grid = []
    for dt in show_dates:                              # 每一天一列（只顯示選定範圍）
        row, day_hits, day_rev = {"日期": dt}, 0, 0
        for t in targets:
            r = by.get((dt, t))
            row[code2name.get(t, t)] = cell(r)
            h = hit_of(r) if r is not None else None
            if h is not None:
                day_rev += 1
                day_hits += 1 if h else 0
        row[RATE_COL] = _pct(day_hits, day_rev)        # 最右：當日命中率
        grid.append(row)

    omitted = len(all_dates) - len(show_dates)
    if omitted > 0:                                    # 被收合的較早日期 → …帶過
        ell = {"日期": f"⋯ 更早還有 {omitted} 天（改上方範圍查看）"}
        for t in targets:
            ell[code2name.get(t, t)] = "⋯"
        ell[RATE_COL] = "⋯"
        grid.append(ell)

    grid.append(foot)                                  # 累積列永遠在最底
    st.table(pd.DataFrame(grid).set_index("日期"))
    st.caption(
        "每格＝命中與否＋當日預測方向（✅漲=預測漲且命中、❌跌=預測跌沒中、"
        "🔮=已預測待收盤驗證、—=當天無預測）。最右欄＝當天所有標的命中率；"
        "最底列＝各標的累積命中率、右下角＝整體命中率——皆以『全部歷史』計算，"
        "不受上方顯示範圍影響。詳細檢討到大盤頁/個股頁看。")


# 深連結：?code=2344 → 直接開個股頁、選好該股
_qp_code = st.query_params.get("code")
_page = st.radio("頁面", ["🌐 大盤", "📈 個股", "📅 預測歷史", "💬 助理"],
                 index=(1 if _qp_code else 0),
                 horizontal=True, label_visibility="collapsed")

# ──────────────────────────── 大盤頁 ────────────────────────────
if _page == "🌐 大盤":
    idx_df = load_index_df()
    mkt = market_summary(idx_df)
    if mkt and mkt.get("close") is not None:
        pct = mkt.get("pct")
        delta = f"{pct:+.2f}%" if isinstance(pct, (int, float)) else None
        st.metric("加權指數(大盤)", f"{mkt['close']:.2f}", delta,
                  delta_color="inverse")   # 台股漲紅跌綠

    records = load_records()
    mrecs = [r for r in records if r.get("stock") == "大盤"]

    # 最新預測卡
    if mrecs:
        mr = max(mrecs, key=lambda r: r.get("date", ""))
        mp = mr.get("prediction") or {}
        arrow = "🔺 漲" if mp.get("direction") == "漲" else "🔻 跌"
        conf = mp.get("confidence")
        conf_txt = f"（信心{conf}）" if conf else ""
        st.markdown(f"#### 最新預測 · {mr['date']}")
        lines = [f"- **預期開盤方向**：{arrow}{conf_txt}"]
        tf = mp.get("taifex_night")
        if isinstance(tf, (int, float)):
            lines.append(f"- **台指期夜盤**：{tf:+.2f}%")
        us = mp.get("us_overnight") or {}
        if us:
            lines.append("- **美股隔夜**：" +
                         "　".join(f"{k} {v:+.2f}%" for k, v in us.items()))
        st.markdown("\n".join(lines))
        drivers = mp.get("drivers") or []
        if drivers:
            st.markdown("**依據**")
            for d in drivers:
                st.markdown(f"- {d}")
        if mp.get("reason"):
            st.caption(f"💬 {mp['reason']}")
        st.divider()
    else:
        st.info("尚無大盤預測。每交易日 08:30 開盤前預測會自動產生。")

    # 圖表（日/週/月）
    if not idx_df.empty:
        rule = timeframe_radio("tf_market")
        st.plotly_chart(price_fig(resample_ohlc(idx_df, rule), with_volume=False),
                        use_container_width=True, config=_CHART_CFG)
    st.caption("資料來源：台灣證交所 TWSE。大盤預測以美股隔夜＋台指期夜盤為領先指標。")

    st.markdown("### 📒 預測歷史")
    if mrecs:
        render_history(mrecs, show_signal=False)
    else:
        st.info("尚無大盤預測紀錄。")

# ──────────────────────────── 預測歷史頁 ────────────────────────────
elif _page == "📅 預測歷史":
    st.markdown("### 📅 預測歷史總覽")
    render_history_overview(load_records(), _dash_owner())

# ──────────────────────────── 助理 chatbox 頁 ────────────────────────
elif _page == "💬 助理":
    render_chat_page()

# ──────────────────────────── 個股頁 ────────────────────────────
else:
    STOCKS = effective_stocks(_dash_owner())
    _names = list(STOCKS.keys())
    _idx = next((i for i, n in enumerate(_names)
                 if STOCKS[n]["code"] == _qp_code), 0)
    choice = st.selectbox("選擇股票", _names, index=_idx)
    cfg = STOCKS[choice]
    df = load_stock_df(cfg["code"])

    if df.empty:
        st.error("抓不到資料，把這個畫面回報給我。")
    else:
        last = df["Close"].iloc[-1]
        prev = df["Close"].iloc[-2] if len(df) >= 2 else last
        chg = last - prev
        pct = (chg / prev * 100) if prev else 0.0
        ma20_last = df["MA20"].iloc[-1]

        c1, c2, c3 = st.columns(3)
        c1.metric("最新收盤", f"{last:.2f}", f"{chg:+.2f} ({pct:+.2f}%)",
                  delta_color="inverse")   # 台股漲紅跌綠
        c2.metric("最高(近期)", f"{df['High'].max():.2f}")
        c3.metric("最低(近期)", f"{df['Low'].min():.2f}")

        rule = timeframe_radio("tf_stock")
        st.plotly_chart(
            price_fig(resample_ohlc(df, rule), supports=cfg.get("supports", {})),
            use_container_width=True, config=_CHART_CFG)

        s = cfg.get("supports", {})
        s1 = s.get("支撐1 (短期)")
        s3 = s.get("支撐3 (長期)")
        if s1 is None and s3 is None:
            st.info("此標的未設定支撐位，僅以 MA20 參考。")
        elif s1 is not None and last > s1:
            st.success("價格在支撐1之上")
        elif pd.notna(ma20_last) and last > ma20_last:
            st.warning(f"⚠️ 真空帶：支撐1已破、MA20({ma20_last:.1f})之上。照紀律：等。")
        elif s3 is not None and last > s3:
            st.warning("⚠️ 跌破 MA20，接近支撐3，留意收盤是否止穩、量是否縮。")
        else:
            st.error("跌破支撐3，重新評估。")
        st.caption("資料來源：台灣證交所 TWSE（盤後）。進場判斷仍須看收盤確認，本工具僅供觀察。")

    st.divider()
    st.markdown("### 📒 預測歷史")
    srecs = [r for r in load_records() if r.get("stock") == cfg["code"]]
    if not srecs:
        st.info("尚無預測紀錄。GitHub Actions 跑過開盤/收盤後會出現。")
    else:
        ordered = sorted(srecs, key=lambda x: x["date"], reverse=True)
        latest = ordered[0]
        lp = latest.get("prediction") or {}
        if lp:
            arrow = "🔺 漲" if lp.get("direction") == "漲" else "🔻 跌"
            conf = lp.get("confidence")
            conf_txt = f"（信心{conf}）" if conf else ""
            sig = lp.get("signal", "—")
            bt = lp.get("batches")
            bt_txt = f"{bt}/3 批" if isinstance(bt, int) else "—"
            st.markdown(f"#### 最新預測 · {latest['date']}")
            st.markdown(
                f"- **預期方向**：{arrow}{conf_txt}\n"
                f"- **進場訊號**：{sig}\n"
                f"- **部位**：{bt_txt}"
            )
            note = lp.get("signal_rule_note")
            if note:
                st.info(f"📐 紀律：{note}")
            bull = lp.get("bull_signals") or []
            bear = lp.get("bear_signals") or []
            if bull or bear:
                b1, b2 = st.columns(2)
                with b1:
                    st.markdown("**🟢 偏多**")
                    for sgl in bull:
                        st.markdown(f"- {sgl}")
                    if not bull:
                        st.caption("—")
                with b2:
                    st.markdown("**🔴 偏空**")
                    for sgl in bear:
                        st.markdown(f"- {sgl}")
                    if not bear:
                        st.caption("—")
            if lp.get("reason"):
                st.caption(f"💬 {lp['reason']}")
            st.divider()
        render_history(srecs, show_signal=True)
