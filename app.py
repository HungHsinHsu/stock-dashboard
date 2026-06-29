import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import requests
from datetime import datetime
from dateutil.relativedelta import relativedelta

st.set_page_config(page_title="台股觀察儀表板", layout="wide")
st.title("📊 台股觀察儀表板")

STOCKS = {
    "華邦電 (2344)": {
        "code": "2344",
        "supports": {"支撐1 (短期)": 222, "支撐2 (MA20)": 181, "支撐3 (長期)": 142},
    },
}

choice = st.sidebar.selectbox("選擇股票", list(STOCKS.keys()))
cfg = STOCKS[choice]

@st.cache_data(ttl=3600)
def load(code):
    frames = []
    today = datetime.today()
    for i in range(6):  # 抓最近6個月
        d = today - relativedelta(months=i)
        ym = d.strftime("%Y%m01")
        url = f"https://www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&date={ym}&stockNo={code}"
        try:
            r = requests.get(url, timeout=10)
            j = r.json()
            if j.get("stat") != "OK" or "data" not in j:
                continue
            for row in j["data"]:
                # row: 日期, 成交股數, 成交金額, 開盤, 最高, 最低, 收盤, 漲跌, 成交筆數
                try:
                    parts = row[0].split("/")
                    y = int(parts[0]) + 1911
                    date = pd.Timestamp(f"{y}-{parts[1]}-{parts[2]}")
                    frames.append({
                        "Date": date,
                        "Open": float(row[3].replace(",", "")),
                        "High": float(row[4].replace(",", "")),
                        "Low": float(row[5].replace(",", "")),
                        "Close": float(row[6].replace(",", "")),
                        "Volume": float(row[1].replace(",", "")),
                    })
                except (ValueError, IndexError):
                    continue
        except Exception:
            continue
    if not frames:
        return pd.DataFrame()
    df = pd.DataFrame(frames).drop_duplicates("Date").sort_values("Date").set_index("Date")
    return df

df = load(cfg["code"])

if df.empty:
    st.error("抓不到資料，把這個畫面回報給我。")
else:
    last = df["Close"].iloc[-1]
    prev = df["Close"].iloc[-2]
    chg = last - prev
    pct = chg / prev * 100

    c1, c2, c3 = st.columns(3)
    c1.metric("最新收盤", f"{last:.2f}", f"{chg:+.2f} ({pct:+.2f}%)")
    c2.metric("最高(近期)", f"{df['High'].max():.2f}")
    c3.metric("最低(近期)", f"{df['Low'].min():.2f}")

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

    s = cfg["supports"]
    if last > s["支撐1 (短期)"]:
        st.success("價格在支撐1之上")
    elif last > s["支撐2 (MA20)"]:
        st.warning("⚠️ 真空帶：支撐1已破、支撐2之上。照紀律：等。")
    elif last > s["支撐3 (長期)"]:
        st.warning("⚠️ 接近支撐2，留意收盤是否止穩、量是否縮。")
    else:
        st.error("跌破支撐3，重新評估。")

    st.caption("資料來源：台灣證交所 TWSE（盤後）。進場判斷仍須看收盤確認，本工具僅供觀察。")
