"""當日頭條（時事第三層）：RSS 解析、失敗靜默、復盤 prompt 接線。"""
from core.headlines import parse_rss_titles, fetch_headlines
from core.review import make_market_review, make_review

_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>feed</title>
<item><title>立法院三讀通過總預算　含無人載具634億 - 中央社</title></item>
<item><title>  記憶體族群重挫\n南亞科一度跌停 - 經濟日報 </title></item>
<item><title>立法院三讀通過總預算　含無人載具634億 - 中央社</title></item>
<item><title></title></item>
<item><title>第四則</title></item>
</channel></rss>"""


def test_parse_rss_titles_dedup_and_clean():
    titles = parse_rss_titles(_RSS)
    assert titles[0].startswith("立法院三讀")
    assert "南亞科一度跌停" in titles[1] and "\n" not in titles[1]
    assert len(titles) == 3          # 重複與空標題被去掉


def test_parse_rss_limit_and_bad_xml():
    assert len(parse_rss_titles(_RSS, limit=1)) == 1
    assert parse_rss_titles("not xml at all") == []


def test_fetch_headlines_swallows_errors():
    assert fetch_headlines(fetcher=lambda: 1 / 0) == []
    assert fetch_headlines(fetcher=lambda: _RSS)[0].startswith("立法院三讀")


def test_reviews_carry_headlines_into_prompt():
    captured = {}

    def fake_llm(system, user, schema):
        captured["sys"], captured["user"] = system, user
        return {"critique": "- ok"}

    judged = {"actual_close": 100, "prev_close": 99, "direction_actual": "漲",
              "results": {"direction": True}, "success": True}
    make_market_review({"direction": "漲"}, judged, llm=fake_llm,
                       headlines=["立法院三讀通過總預算"])
    assert "立法院三讀通過總預算" in captured["user"]
    assert "頭條" in captured["sys"]

    judged2 = dict(judged, results={"direction": True, "hold_ma20": True})
    make_review({"direction": "漲"}, judged2, {"close": 100}, "亞航",
                llm=fake_llm, headlines=["總預算三讀"])
    assert "總預算三讀" in captured["user"]

    # 沒頭條 → prompt 不出現頭條段（不誤導 LLM 以為有附）
    make_review({"direction": "漲"}, judged2, {"close": 100}, "亞航",
                llm=fake_llm, headlines=[])
    assert "當日新聞頭條" not in captured["user"]
