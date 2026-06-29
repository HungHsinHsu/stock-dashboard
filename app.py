import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go

st.set_page_config(page_title="台股觀察儀表板", layout="wide")
st.title("📊 台股觀察儀表板")

# ── 設定區：之後要加股票或改支撐，改這裡就好 ──
STOCKS = {
    "華邦電 (2344)": {
        "ticker": "2344.TW",
        "supports": {"支撐1 (短期)": 222, "支撐2 (MA20)": 181, "支撐3 (長期)": 142},
    },
}

choice = st.sidebar.selectbox("選擇股票", list(STOCKS.keys()))
cfg = STOCKS[choice]

# ── 抓資料 ──
@st.cache_data(ttl=600)
def load(ticker):
    df = yf.Ticker(ticker).history(period="6mo")
    return df

df = load(cfg["ticker"])

if df.empty:
    st.error("抓不到資料，可能是代號或資料源問題，把這個畫面回報給我。")
else:
    last = df["Close"].iloc[-1]
    prev = df["Close"].iloc[-2]
    chg = last - prev
    pct = chg / prev * 100

    c1, c2, c3 = st.columns(3)
    c1.metric("最新收盤", f"{last:.2f}", f"{chg:+.2f} ({pct:+.2f}%)")
    c2.metric("最高(近6月)", f"{df['High'].max():.2f}")
    c3.metric("最低(近6月)", f"{df['Low'].min():.2f}")

    # ── K線圖 + 支撐線 ──
    fig = go.Figure(data=[go.Candlestick(
        x=df.index, open=df["Open"], high=df["High"],
        low=df["Low"], close=df["Close"], name="K線")])

    colors = {"支撐1 (短期)": "orange", "支撐2 (MA20)": "purple", "支撐3 (長期)": "magenta"}
    for name, price in cfg["supports"].items():
        fig.add_hline(y=price, line_dash="dash",
                      line_color=colors.get(name, "gray"),
                      annotation_text=f"{name} {price}")

    fig.update_layout(height=600, xaxis_rangeslider_visible=False)
    st.plotly_chart(fig, use_container_width=True)

    # ── 狀態判斷 ──
    s = cfg["supports"]
    if last > s["支撐1 (短期)"]:
        st.success("價格在支撐1之上")
    elif last > s["支撐2 (MA20)"]:
        st.warning("⚠️ 真空帶：支撐1已破、支撐2之上。照紀律：等。")
    elif last > s["支撐3 (長期)"]:
        st.warning("⚠️ 接近支撐2，留意收盤是否止穩、量是否縮。")
    else:
        st.error("跌破支撐3，重新評估。")

    st.caption("資料來源：yfinance（盤後/延遲）。進場判斷仍須看收盤確認，本工具僅供觀察。")