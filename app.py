import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from core.data import fetch_daily, fetch_index
from core.watchlist import effective_stocks
from core.market import market_summary
from core.store import load_history
from core.review import hit_rate

st.set_page_config(page_title="台股觀察儀表板", layout="centered", page_icon="📊")
st.title("📊 台股觀察儀表板")


@st.cache_data(ttl=3600)
def load_index_df():
    return fetch_index()


@st.cache_data(ttl=3600)
def load_stock_df(code):
    return fetch_daily(code)


@st.cache_data(ttl=1800)
def load_records():
    return load_history()


def price_fig(df, supports=None, with_volume=True):
    """K 線 + MA20（個股另含成交量副圖）。"""
    if with_volume:
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                            vertical_spacing=0.04, row_heights=[0.74, 0.26])
    else:
        fig = make_subplots(rows=1, cols=1)
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
    if with_volume and "Volume" in df:
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


_CHART_CFG = {"displayModeBar": True, "displaylogo": False, "scrollZoom": True}

tab_market, tab_stock = st.tabs(["🌐 大盤", "📈 個股"])

# ──────────────────────────── 大盤頁 ────────────────────────────
with tab_market:
    idx_df = load_index_df()
    mkt = market_summary(idx_df)
    if mkt and mkt.get("close") is not None:
        pct = mkt.get("pct")
        delta = f"{pct:+.2f}%" if isinstance(pct, (int, float)) else None
        st.metric("加權指數(大盤)", f"{mkt['close']:.2f}", delta)

    mrecs = [r for r in load_records() if r.get("stock") == "大盤"]
    if mrecs:
        mr = max(mrecs, key=lambda r: r.get("date", ""))
        mp = mr.get("prediction") or {}
        arrow = "🔺 漲" if mp.get("direction") == "漲" else "🔻 跌"
        conf = mp.get("confidence")
        conf_txt = f"（信心{conf}）" if conf else ""
        st.markdown(f"#### 開盤前預測 · {mr['date']}")
        st.markdown(f"- **預期開盤方向**：{arrow}{conf_txt}")
        tf = mp.get("taifex_night")
        if isinstance(tf, (int, float)):
            st.markdown(f"- **台指期夜盤**：{tf:+.2f}%")
        us = mp.get("us_overnight") or {}
        if us:
            st.markdown("- **美股隔夜**：" +
                        "　".join(f"{k} {v:+.2f}%" for k, v in us.items()))
        drivers = mp.get("drivers") or []
        if drivers:
            st.markdown("**依據**")
            for d in drivers:
                st.markdown(f"- {d}")
        if mp.get("reason"):
            st.caption(f"💬 {mp['reason']}")
    else:
        st.info("尚無大盤預測。每交易日 08:30 開盤前預測會自動產生。")

    if not idx_df.empty:
        st.plotly_chart(price_fig(idx_df, with_volume=False),
                        use_container_width=True, config=_CHART_CFG)
    st.caption("資料來源：台灣證交所 TWSE。大盤預測以美股隔夜＋台指期夜盤為領先指標。")

# ──────────────────────────── 個股頁 ────────────────────────────
with tab_stock:
    STOCKS = effective_stocks()
    choice = st.selectbox("選擇股票", list(STOCKS.keys()))
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
        c1.metric("最新收盤", f"{last:.2f}", f"{chg:+.2f} ({pct:+.2f}%)")
        c2.metric("最高(近期)", f"{df['High'].max():.2f}")
        c3.metric("最低(近期)", f"{df['Low'].min():.2f}")

        st.plotly_chart(price_fig(df, supports=cfg.get("supports", {})),
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
    records = [r for r in load_records() if r.get("stock") == cfg["code"]]
    if not records:
        st.info("尚無預測紀錄。GitHub Actions 跑過開盤/收盤後會出現。")
    else:
        ordered = sorted(records, key=lambda x: x["date"], reverse=True)
        rate = hit_rate(records)
        if rate is not None:
            st.metric("方向命中率", f"{rate * 100:.0f}%")

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

        rows = []
        for r in ordered:
            p = r.get("prediction") or {}
            rv = r.get("review") or {}
            res = rv.get("results") or {}
            rows.append({
                "日期": r["date"],
                "訊號": p.get("signal", "—"),
                "預測方向": p.get("direction", "—"),
                "信心": p.get("confidence", "—"),
                "實際方向": rv.get("direction_actual", "—"),
                "方向命中": "✅" if res.get("direction") else ("❌" if rv else "—"),
                "收盤": rv.get("actual_close", "—"),
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)
