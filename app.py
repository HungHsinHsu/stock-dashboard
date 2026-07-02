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

from core.data import fetch_daily, fetch_index, fetch_foreign_flow, fetch_top_turnover
from core.indicators import compute_indicators
from core.rules import is_etf, NEAR_PCT, entry_setup, is_denied, DENYLIST
from core.screener import scan as _scan
from core.watchlist import effective_stocks, add_stock
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


def _render_chatbox_sidebar():
    """側邊 chatbox：訊息在固定高度捲動框（框內自己滾，不會把輸入框往下擠），
    這樣可以一邊看主畫面其他頁、一邊聊。"""
    st.markdown("#### 💬 助理")
    if not db.db_enabled():
        st.caption("未連上資料庫，助理無法使用（請先設定 DATABASE_URL）。")
        return
    box = st.container(height=340)                 # 固定高度、框內捲動
    for m in st.session_state.get("chat_hist", []):
        box.chat_message(m["role"]).write(m["text"])
    prompt = st.chat_input("輸入訊息…")
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
            st.caption("登入後這裡會出現『💬 助理』聊天框。需要帳號找管理者。")
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
        _render_chatbox_sidebar()


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


def render_schedule_info():
    """報告時間表／運作說明：寫在網頁上，不必每次問幾點出報告。"""
    with st.expander("🕒 報告時間表／運作說明（幾點出報告？點開看）", expanded=False):
        st.markdown("**每日時間表（台灣時間・僅交易日 週一～週五）**")
        st.table(pd.DataFrame([
            {"時間": "07:40", "事件／內容": "開盤前預測出爐（主班）"},
            {"時間": "08:10", "事件／內容": "備援班（主班被雲端排程漏跑時才補）"},
            {"時間": "13:30", "事件／內容": "台股收盤"},
            {"時間": "15:20", "事件／內容": "收盤復盤：當日資料到齊就結算並通知；"
                                        "沒到齊會明講「等 18:00」，不會靜默"},
            {"時間": "18:00", "事件／內容": "復盤補跑（15:20 收盤資料還沒到時）"},
        ]).set_index("時間"))
        st.markdown(
            "- 復盤通常是**收盤後、傍晚資料齊全才出爐**，盤中查是還沒有的。\n"
            "- 大盤自動推完整卡片；個股發一則精簡總表（詳細看個股頁或 /復盤 代號）。\n"
            "- **雙保險排程**：GitHub 的 cron 常延遲/漏班，所以除了它，**常駐在線的機器人也內建"
            "定時**——每班過約 10 分鐘緩衝後，若發現 GitHub 還沒做，機器人會**自己補跑**"
            "（預測寫一次鎖定、復盤已做就跳過，不會重覆）。就算雲端排程誤點，該出的還是會自動補上。\n"
            "- 若真的都沒收到，也可在 Telegram 傳 **/開盤** 手動補。")


render_account_sidebar()
render_schedule_info()


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
    # 三段均線＝三段支撐（短期 MA5 橘／中期 MA20 紫／長期 MA60 季線 深紅），隨收盤每日移動
    for period, color, width in ((5, "#e9a44c", 1), (20, "#9b59b6", 1.5),
                                 (60, "#c0392b", 1.2)):
        ma = df["MA20"] if period == 20 and "MA20" in df else df["Close"].rolling(period).mean()
        fig.add_trace(go.Scatter(
            x=df.index, y=ma, name=f"MA{period}",
            line=dict(color=color, width=width)), row=1, col=1)
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


@st.cache_data(ttl=1800)
def load_foreign(code):
    try:
        return fetch_foreign_flow(code)
    except Exception:
        return None


def _render_entry_gates(code, df, last, ma5, ma60):
    """個股進場四關：依現價實時判定，一關一關打勾/打叉，並顯示卡在第幾關。
    用的是與每日預測同一套規則(core.rules.entry_setup)，所以與訊號一致。"""
    st.markdown("##### 🚪 進場四關（依現價實時判定，一關一關看）")

    if is_denied(code):
        st.error(f"🛑 禁區標的（{DENYLIST.get(str(code), '動能股/槓桿')}）：一律避開，"
                 "不玩回檔承接法。")
        return
    if pd.notna(ma60) and last < ma60:
        st.error("🛑 已跌破支撐3（季線 MA60）＝停損區：不接刀；手上有部位者依紀律全數出場。"
                 "（四關不用看了，這關就出局）")
        return

    ind = compute_indicators(df, {})
    foreign = load_foreign(code)
    fstopped = foreign.get("stopped") if foreign else None
    setup = entry_setup(ind, code, fstopped)
    at_batch, ceiling = setup["at_batch"], setup["ceiling"]
    vol_ok, hold_ok = setup["vol_ok"], setup["hold_ok"]
    vr = ind.get("vol_ratio")

    if at_batch:
        g1 = f"✅ 已到{at_batch}"
    else:
        where = "位置偏高、還沒回檔" if (pd.notna(ma5) and last >= ma5) else "在真空帶"
        g1 = f"❌ 未到任何支撐（{where}）"
    g2 = "✅ 收盤沒再破底" if hold_ok else "❌ 收盤仍走弱、未站穩"
    if vr is None:
        g3 = "❓ 量比資料不足"
    elif vol_ok:
        g3 = f"✅ 量縮（量比 {vr}）"
    else:
        g3 = f"❌ 未量縮（量比 {vr}，屬放量）"
    if fstopped is True:
        g4 = "✅ 外資已停止賣超"
    elif fstopped is False:
        g4 = f"❌ 外資仍賣超（連{(foreign or {}).get('sold_streak') or 0}日）"
    else:
        g4 = "❓ 外資資料未取得，需自行確認"

    st.table(pd.DataFrame([
        {"關卡": "① 價格到位（回到支撐±2%）", "現況": g1},
        {"關卡": "② 收盤站穩（沒再破底）", "現況": g2},
        {"關卡": "③ 量縮（量比<1＝賣壓衰竭）", "現況": g3},
        {"關卡": "④ 外資停止賣超", "現況": g4},
    ]).set_index("關卡"))

    if ceiling == "進場":
        tail = ("外資資料缺、請自行確認" if fstopped is None
                else "用盤後定價 14:00–14:30 進場")
        st.success(f"✅ 四關全過 → 可進「{at_batch or '下一批'}」（{tail}）。")
    elif ceiling == "避開":
        st.error(f"🛑 避開：{setup['reason']}")
    else:
        fails = []
        if not at_batch:
            fails.append("①價格未到支撐")
        if not hold_ok:
            fails.append("②收盤未站穩")
        if not vol_ok:
            fails.append("③量未縮")
        if fstopped is False:
            fails.append("④外資仍賣超")
        tail = ("卡在：" + "、".join(fails)) if fails else setup["reason"]
        st.warning(f"⏳ 觀望（先不進場）——{tail}。過關的關卡打勾、沒過的打叉，"
                   "等全部打勾才是進場點。")


def render_support_playbook(code, df, last, ma5, ma20, ma60):
    """『現價位於三段支撐哪裡 → 該做什麼』完整對策，並標出目前位置。
    個股＝回檔承接法(分三批)＋進場四關即時判定；ETF＝趨勢框架。"""
    def near(v):
        return bool(pd.notna(v) and v and abs(last - v) / v * 100 <= NEAR_PCT)

    def align():
        if pd.notna(ma5) and pd.notna(ma20) and pd.notna(ma60):
            if ma5 > ma20 > ma60:
                return "多頭排列"
            if ma5 < ma20 < ma60:
                return "空頭排列"
        return "糾結"

    st.markdown("#### 📐 現價位置對策（每種可能都列出）")
    st.caption("支撐1＝短期均線 MA5、支撐2＝月線 MA20、支撐3＝季線 MA60"
               "（價位見上表）；以下位置一律用支撐1／2／3 說明。")

    if not is_etf(code):
        # 先給即時「四關」判定，再附完整位置對照表
        _render_entry_gates(code, df, last, ma5, ma60)
        st.markdown("###### 📋 位置對照（每種可能都列出，供參考）")

    if is_etf(code):
        below60 = bool(pd.notna(ma60) and last < ma60)
        al = align()
        if al == "空頭排列" and below60:
            cur = "down"
        elif al == "多頭排列" or (pd.notna(ma60) and last >= ma60):
            cur = "up"
        else:
            cur = "weak"
        scen = [
            ("up", "站上支撐3、均線多頭排列（支撐1＞2＞3）",
             "順勢偏多：可順勢續抱或定期定額。ETF 跟著追蹤指數走，不必抓短。"),
            ("weak", "在支撐3上下糾結、未成空頭排列",
             "趨勢轉弱觀望：等站回支撐3、方向轉明朗再說。"),
            ("down", "跌破支撐3、均線空頭排列（支撐1＜2＜3）",
             "明顯轉空避開：不接刀，趨勢沒回穩不進場。"),
        ]
        tone = {"up": st.success, "weak": st.warning, "down": st.error}
    else:
        if pd.notna(ma60) and last < ma60:
            cur = "stop"
        elif near(ma5):
            cur = "b1"
        elif near(ma20):
            cur = "b2"
        elif near(ma60):
            cur = "b3"
        elif pd.notna(ma5) and last >= ma5:
            cur = "high"
        else:
            cur = "vac"
        scen = [
            ("high", "在支撐1之上（位置偏高、還沒回檔）",
             "觀望：不追高，等回檔到支撐。錯過無傷。"),
            ("b1", "回到支撐1附近",
             "第一批『候選』（非買進訊號）：要收盤站穩＋量縮＋外資沒賣超，全過才進 1/3；缺一則觀望。"),
            ("vac", "支撐與支撐之間的真空帶",
             "等：不上不下不進場，等跌到下一段支撐、或帶量站回上一條支撐。"),
            ("b2", "回到支撐2附近",
             "第二批『候選』（非買進訊號）：同樣四條件全成立才進 2/3；缺一則觀望。"),
            ("b3", "回到支撐3附近",
             "第三批『候選』（最後一批、非買進訊號）：四條件全成立才進 3/3；缺一則觀望。"),
            ("stop", "收盤跌破支撐3",
             "停損區：三批全數認賠出場，不接刀。"),
        ]
        tone = {"high": st.warning, "vac": st.warning, "b1": st.success,
                "b2": st.success, "b3": st.success, "stop": st.error}

    cur_label, cur_action = next(((lb, ac) for k, lb, ac in scen if k == cur),
                                 ("", ""))
    if cur_label:
        tone.get(cur, st.info)(f"👉 **目前位置：{cur_label}**\n\n對策：{cur_action}")

    # 完整對照：每種可能一列，目前位置標 👉（排版清楚、不擠成一坨）
    rows = [{"位置": ("👉 " if k == cur else "") + lb, "該做什麼": ac}
            for k, lb, ac in scen]
    st.table(pd.DataFrame(rows).set_index("位置"))
    st.caption("進場一律看『收盤』確認、不看盤中；均線每天移動，位置每天重算。")


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
    st.markdown(
        "**圖例（每格＝狀態＋當初預測方向）**\n"
        "- ✅ 命中（預測對了）　❌ 沒中（預測錯了）\n"
        "- 🔮 已經預測、**還沒到收盤結算**（待驗證，通常傍晚才會變 ✅/❌）\n"
        "- — 當天沒有預測（或還沒加入追蹤）\n"
        "- 後面的「漲／跌」是**當初預測的方向**（例：`❌漲`＝當初猜漲、結果沒中）")
    st.table(pd.DataFrame(grid).set_index("日期"))
    st.caption(
        "最右欄＝當天所有標的命中率；最底列＝各標的累積命中率、右下角＝整體命中率"
        "——皆以『全部歷史』計算，不受上方顯示範圍影響。詳細檢討到大盤頁/個股頁看。")


def _run_screen(top):
    """掃當日成交額前 top 檔，套回檔承接法規則挑候選。
    回 (names, cands, uni_n, fetched_n)；不做結果快取——失敗重按能真的重試。
    降低併發(workers=2)＋節流，減少被 TWSE 限流；並統計抓成功檔數供診斷。"""
    uni = fetch_top_turnover(top)
    names = {c: nm for c, nm in uni}
    if not uni:
        return names, [], 0, 0
    stats = {"ok": 0}

    def _f(c):
        df = fetch_daily(c, months=5, workers=2)
        if df is not None and not getattr(df, "empty", True):
            stats["ok"] += 1
        return df

    cands = _scan([c for c, _ in uni], fetch=_f, foreign_lookup=fetch_foreign_flow,
                  limit=20, pause=0.05)
    return names, cands, len(uni), stats["ok"]


def _render_scan_result(names, cands, date_label):
    owner = _dash_owner()
    tracked_codes = {c.get("code") for c in effective_stocks(owner).values()}
    st.markdown(f"**{date_label}・相對最好的前 {len(cands)} 名**")
    st.caption(
        "🟢進場＝四關到位可接、🟡觀望＝趨勢沒破仍在等、🔴避開＝已跌破季線（相對最不弱、墊底參考）。"
        "📏 排序：訊號 進場＞觀望＞避開 ＞ 回檔到支撐 ＞ 收盤站穩 ＞ 量縮 ＞ 離均線近。"
        "※ 已逐檔補查外資、資料不齊者已排除，訊號已含外資。")

    def _badge(s):
        return ("🟢" if s in ("進場", "順勢偏多")
                else "🔴" if s in ("避開", "明顯轉空避開") else "🟡")

    st.caption("👉 在最左欄勾選要追蹤的（表單內連續勾都不會重載），勾完按下方「加入勾選」一次送出。")
    rows = []
    for x in cands:
        code = x["code"]
        disp = f"{names.get(code, code)} ({code})"
        tracked = code in tracked_codes
        rows.append({"追蹤": False, "訊號": f"{_badge(x['signal'])} {x['signal']}",
                     "標的": disp, "位置": x.get("at_batch") or x.get("kind", ""),
                     "為什麼（理由）": x.get("reason", ""),
                     "已在清單": "✅" if tracked else "",
                     "_code": code, "_disp": disp})
    df = pd.DataFrame(rows)
    # 用 form 包住：表單內勾選不會逐次 rerun，按送出才一次套用（可連續勾很多檔）
    with st.form(f"scanform_{date_label}", border=False):
        edited = st.data_editor(
            df, hide_index=True, use_container_width=True,
            key=f"scan_editor_{date_label}",
            column_config={
                "追蹤": st.column_config.CheckboxColumn("追蹤?", help="勾選要加入追蹤的"),
                "_code": None, "_disp": None,
            },
            disabled=["訊號", "標的", "位置", "為什麼（理由）", "已在清單"])
        submitted = st.form_submit_button("➕ 加入勾選的到追蹤清單", type="primary")
    if submitted:
        to_add = [(r["_code"], r["_disp"]) for _, r in edited.iterrows()
                  if r["追蹤"] and r["_code"] not in tracked_codes]
        for code, disp in to_add:
            add_stock(code, name=disp, owner=owner)
        if to_add:
            st.success(f"已加入 {len(to_add)} 檔追蹤")
            st.rerun()
        else:
            st.info("沒有勾選新的標的（或勾到的已在清單）。")


def render_screener_page():
    st.markdown("### 🔎 選股掃描（回檔承接法）")
    st.caption("每天**收盤後（約 15:35）自動掃前 150 大成交股**、挑出承接點候選、推到 Telegram "
               "並存在這裡；不用手動即時掃（那樣容易被 TWSE 限流）。")
    stored = None
    if db.db_enabled():
        try:
            stored = db.get_state("screen:latest")
        except Exception:
            stored = None
    if stored and stored.get("cands"):
        _render_scan_result(stored.get("names", {}), stored["cands"],
                            f"收盤後選股 · {stored.get('date', '')}")
    elif stored:
        st.info(f"最近一次掃描（{stored.get('date')}）沒有合適候選"
                f"（清單 {stored.get('uni_n')} 檔、成功讀取 {stored.get('fetched_n')} 檔）。")
    else:
        st.info("還沒有收盤後選股結果——今天收盤後（約 15:35）會自動產生並推到 Telegram。")

    with st.expander("⚙️ 手動即時重掃（較慢、可能被 TWSE 限流）", expanded=False):
        top = st.slider("範圍（當日成交額前 N 大）", 30, 150, 100, step=10)
        if st.button("🚀 立即重掃"):
            with st.spinner("即時掃描中…（約 30–90 秒）"):
                st.session_state["scan_result"] = _run_screen(top)
        res = st.session_state.get("scan_result")
        if res:
            names, cands, uni_n, fetched_n = res
            if cands:
                st.caption(f"（診斷：掃 {uni_n} 檔、成功讀取 {fetched_n} 檔歷史）")
                _render_scan_result(names, cands, "即時掃描")
            elif uni_n == 0:
                st.error("抓不到市場清單（TWSE 沒回應）。稍後再試。")
            elif fetched_n == 0:
                st.error(f"清單有 {uni_n} 檔，但個股歷史 0 檔抓成功——多半被 TWSE 限流。"
                         "稍等 1–2 分鐘或把範圍調更小再試。")
            else:
                st.warning(f"掃 {uni_n} 檔、成功讀取 {fetched_n} 檔，但都不符合。")


# 深連結：?code=2344 → 直接開個股頁、選好該股
_qp_code = st.query_params.get("code")
_page = st.radio("頁面", ["🌐 大盤", "📈 個股", "📅 預測歷史", "🔎 選股"],
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
            tf_date = mp.get("taifex_date")
            asof = f"（{tf_date}）" if tf_date else ""
            lines.append(f"- **台指期夜盤**：{tf:+.2f}%{asof}")
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

# ──────────────────────────── 選股掃描頁 ────────────────────────────
elif _page == "🔎 選股":
    render_screener_page()

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
            price_fig(resample_ohlc(df, rule)),   # 支撐＝圖上三條均線，不再畫寫死水平線
            use_container_width=True, config=_CHART_CFG)

        # 三段支撐＝三條均線，隨收盤每日移動；列出今日價位與現價相對位置
        st.markdown("#### 📉 三段支撐（均線，每日更新）")
        _cl = df["Close"]

        def _ma(p):
            return _cl.rolling(p).mean().iloc[-1] if len(_cl) >= p else float("nan")

        _ma5, _ma20v, _ma60 = _ma(5), _ma(20), _ma(60)
        _ma_rows = []
        for _lab, _v in (("支撐1 短期均線 (MA5)", _ma5),
                         ("支撐2 中期均線 (MA20／月線)", _ma20v),
                         ("支撐3 長期均線 (MA60／季線)", _ma60)):
            if pd.notna(_v):
                _d = (last - _v) / _v * 100 if _v else 0.0
                _pos = "🔺 站上" if last >= _v else "🔻 跌破"
                _ma_rows.append({"支撐": _lab, "今日價位": f"{_v:.2f}",
                                 "現價相對": f"{_pos}（{_d:+.1f}%）"})
        if _ma_rows:
            st.table(pd.DataFrame(_ma_rows).set_index("支撐"))
            st.caption(f"現價 {last:.2f}。均線每天隨最新收盤重算，故支撐位每日更新。")
        else:
            st.caption("資料期間不足，尚無法計算均線支撐。")

        render_support_playbook(cfg["code"], df, last, _ma5, _ma20v, _ma60)
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
