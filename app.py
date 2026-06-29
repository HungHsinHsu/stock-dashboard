import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
from datetime import datetime
from dateutil.relativedelta import relativedelta

st.set_page_config(page_title="台股觀察儀表板", layout="centered", page_icon="📊")
st.title("📊 台股觀察儀表板")

# 固定支撐位為手畫的水平支撐；MA20 改為即時計算的動態均線（不再寫死）。
STOCKS = {
    "華邦電 (2344)": {
        "code": "2344",
        "supports": {"支撐1 (短期)": 222, "支撐3 (長期)": 142},
    },
}

# 選股放主畫面頂端：手機側邊欄預設收合，主畫面比較好點。
choice = st.selectbox("選擇股票", list(STOCKS.keys()))
cfg = STOCKS[choice]

HEADERS = {"User-Agent": "Mozilla/5.0 (stock-dashboard)"}


@st.cache_data(ttl=3600)
def load(code):
    frames = []
    today = datetime.today()
    for i in range(6):  # 抓最近6個月
        d = today - relativedelta(months=i)
        ym = d.strftime("%Y%m01")
        url = f"https://www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&date={ym}&stockNo={code}"
        try:
            r = requests.get(url, headers=HEADERS, timeout=10)
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
    df["MA20"] = df["Close"].rolling(20).mean()
    return df


df = load(cfg["code"])

if df.empty:
    st.error("抓不到資料，把這個畫面回報給我。")
else:
    last = df["Close"].iloc[-1]
    prev = df["Close"].iloc[-2] if len(df) >= 2 else last
    chg = last - prev
    pct = (chg / prev * 100) if prev else 0.0
    ma20_last = df["MA20"].iloc[-1]  # 可能為 NaN（資料不足 20 筆）

    c1, c2, c3 = st.columns(3)
    c1.metric("最新收盤", f"{last:.2f}", f"{chg:+.2f} ({pct:+.2f}%)")
    c2.metric("最高(近期)", f"{df['High'].max():.2f}")
    c3.metric("最低(近期)", f"{df['Low'].min():.2f}")

    # K 線 + 成交量副圖，共用 X 軸
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        vertical_spacing=0.04, row_heights=[0.74, 0.26],
    )

    fig.add_trace(go.Candlestick(
        x=df.index, open=df["Open"], high=df["High"],
        low=df["Low"], close=df["Close"], name="K線",
        increasing_line_color="#e63946", decreasing_line_color="#2a9d8f",
    ), row=1, col=1)

    # 動態 MA20 線（會跟著行情移動）
    fig.add_trace(go.Scatter(
        x=df.index, y=df["MA20"], name="MA20",
        line=dict(color="#9b59b6", width=1.5),
    ), row=1, col=1)

    # 固定支撐位水平線
    colors = {"支撐1 (短期)": "orange", "支撐3 (長期)": "magenta"}
    for name, price in cfg["supports"].items():
        fig.add_hline(y=price, line_dash="dash",
                      line_color=colors.get(name, "gray"),
                      annotation_text=f"{name} {price}",
                      annotation_position="top left",
                      annotation_font_size=10,
                      row=1, col=1)

    # 成交量（紅漲綠跌，配合台股習慣）
    vol_colors = ["#e63946" if c >= o else "#2a9d8f"
                  for c, o in zip(df["Close"], df["Open"])]
    fig.add_trace(go.Bar(
        x=df.index, y=df["Volume"], name="成交量",
        marker_color=vol_colors, showlegend=False,
    ), row=2, col=1)

    fig.update_layout(
        height=520,
        margin=dict(l=8, r=8, t=36, b=8),
        xaxis_rangeslider_visible=False,
        legend=dict(orientation="h", yanchor="bottom", y=1.0,
                    xanchor="left", x=0, font=dict(size=11)),
        dragmode="pan",
    )
    fig.update_yaxes(title_text="", row=1, col=1)
    fig.update_yaxes(title_text="量", row=2, col=1, title_font_size=10)
    fig.update_xaxes(rangeslider_visible=False, row=1, col=1)

    st.plotly_chart(
        fig, use_container_width=True,
        config={"displayModeBar": True, "displaylogo": False,
                "scrollZoom": True},
    )

    s = cfg["supports"]
    if last > s["支撐1 (短期)"]:
        st.success("價格在支撐1之上")
    elif pd.notna(ma20_last) and last > ma20_last:
        st.warning(f"⚠️ 真空帶：支撐1已破、MA20({ma20_last:.1f})之上。照紀律：等。")
    elif last > s["支撐3 (長期)"]:
        st.warning("⚠️ 跌破 MA20，接近支撐3，留意收盤是否止穩、量是否縮。")
    else:
        st.error("跌破支撐3，重新評估。")

    st.caption("資料來源：台灣證交所 TWSE（盤後）。進場判斷仍須看收盤確認，本工具僅供觀察。")
