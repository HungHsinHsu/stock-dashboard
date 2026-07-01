import os
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
from core import db

st.set_page_config(page_title="台股觀察儀表板", layout="centered", page_icon="📊")
st.title("📊 台股觀察儀表板")
st.caption(
    f"🗄 資料來源：{'Postgres 資料庫 ✅' if db.db_enabled() else 'JSON 檔（未連到 DB）'}")

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
    st.dataframe(styled, use_container_width=True, hide_index=True)

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


def render_history_overview(records):
    """預測歷史總覽：各標的命中率 ＋ 每日命中矩陣（大盤與各股一次看）。"""
    if not records:
        st.info("尚無任何預測紀錄。開盤預測與收盤復盤會自動累積在這裡。")
        return
    stocks = effective_stocks()
    code2name = {cfg["code"]: name for name, cfg in stocks.items()}
    code2name["大盤"] = "🌐 大盤"
    targets = ["大盤"] + [cfg["code"] for cfg in stocks.values()]

    st.markdown("#### 各標的方向命中率")
    stat_rows = []
    for t in targets:
        recs_t = [r for r in records if r.get("stock") == t]
        reviewed = [r for r in recs_t if (r.get("review") or {}).get("results")]
        hits = sum(1 for r in reviewed
                   if ((r["review"]["results"]) or {}).get("direction"))
        rate = hit_rate(recs_t)
        stat_rows.append({
            "標的": code2name.get(t, t),
            "命中率": f"{rate * 100:.0f}%" if rate is not None else "—",
            "命中/已復盤": f"{hits}/{len(reviewed)}" if reviewed else "0/0",
            "預測筆數": len([r for r in recs_t if r.get("prediction")]),
        })
    st.dataframe(pd.DataFrame(stat_rows), hide_index=True,
                 use_container_width=True)

    st.markdown("#### 每日命中情況（✅命中／❌未中／🔮待驗）")

    def _cell(r):
        p = r.get("prediction") or {}
        d = p.get("direction", "") or ""
        rv = r.get("review") or {}
        res = rv.get("results") or {}
        if not rv or "direction" not in res:
            return f"🔮{d}" if d else "—"
        return ("✅" if res.get("direction") else "❌") + d

    lut = {(r["date"], r.get("stock")): _cell(r)
           for r in records if r.get("prediction")}
    dates = sorted({r["date"] for r in records if r.get("prediction")},
                   reverse=True)
    grid = []
    for dt in dates:
        row = {"日期": dt}
        for t in targets:
            row[code2name.get(t, t)] = lut.get((dt, t), "—")
        grid.append(row)
    st.dataframe(pd.DataFrame(grid), hide_index=True, use_container_width=True)
    st.caption("每格顯示『命中與否＋當日預測方向』，例：✅漲＝預測漲且命中、❌跌＝預測跌但沒中。"
               "詳細檢討到大盤頁或個股頁看。")


# 深連結：?code=2344 → 直接開個股頁、選好該股
_qp_code = st.query_params.get("code")
_page = st.radio("頁面", ["🌐 大盤", "📈 個股", "📅 預測歷史"],
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
    render_history_overview(load_records())

# ──────────────────────────── 個股頁 ────────────────────────────
else:
    STOCKS = effective_stocks()
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
