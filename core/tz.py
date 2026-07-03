"""台灣時區（UTC+8）統一時鐘。

伺服器（GitHub Actions、Streamlit Cloud）多跑 UTC，直接用 datetime.today()/now()
會拿到 UTC 時間，導致顯示時間、清單日期對不上。全站一律用這裡取「現在／今天」，
確保機器人與網頁顯示的所有時間都是台灣時間（UTC+8，Asia/Taipei，台灣無日光節約）。
"""
from datetime import datetime, timezone, timedelta

TW = timezone(timedelta(hours=8))   # 台灣時間 UTC+8


def now_tw():
    """現在的台灣時間（帶時區）。"""
    return datetime.now(TW)


def today_tw():
    """今天的台灣日期（UTC+8）。"""
    return now_tw().date()
