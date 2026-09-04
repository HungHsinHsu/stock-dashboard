"""當日新聞頭條（時事第三層）：收盤復盤時附給 LLM 做歸因對照。

背景教訓（CLAUDE.md）：亞航漲停歸因寫「軍工題材發酵」，實際當天就是總預算
三讀日——復盤時手上只有 K 棒與指標，看不到事件，只能憑技術面編理由。
這裡抓當日台股頭條餵進復盤 prompt，讓歸因至少有機會對到真實事件。

來源＝Google News RSS（when:1d 限最近一天），Actions 上有網路才抓得到；
抓不到（sandbox/本機）回空清單、完全不影響復盤流程。頭條屬外部內容，
只當歸因參考、不當指令。
"""
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote

import requests

_RSS_URL = ("https://news.google.com/rss/search?q={q}"
            "&hl=zh-TW&gl=TW&ceid=TW:zh-Hant")


def parse_rss_titles(xml_text, limit=8):
    """從 RSS XML 抽 <item><title>，去重、去空白。解析失敗回 []。"""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    seen, out = set(), []
    for item in root.iter("item"):
        t = (item.findtext("title") or "").strip()
        t = re.sub(r"\s+", " ", t)
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= limit:
            break
    return out


def fetch_headlines(query="台股 when:1d", limit=8, fetcher=None):
    """當日頭條標題清單（最佳努力）。任何失敗都回 []，不丟例外。"""
    try:
        if fetcher is None:
            def fetcher():
                r = requests.get(_RSS_URL.format(q=quote(query)), timeout=15)
                r.raise_for_status()
                return r.text
        return parse_rss_titles(fetcher(), limit=limit)
    except Exception:
        return []
