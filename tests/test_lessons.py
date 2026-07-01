from core.lessons import add_lesson, load_lessons, recent_misses, lessons_prompt


def _miss(date, stock, pred_dir, actual, crit):
    return {"date": date, "stock": stock,
            "prediction": {"direction": pred_dir},
            "review": {"success": False, "direction_actual": actual,
                       "results": {"direction": False}, "critique": crit}}


def _hit(date, stock, d):
    return {"date": date, "stock": stock, "prediction": {"direction": d},
            "review": {"success": True, "direction_actual": d,
                       "results": {"direction": True}}}


def test_recent_misses_filters_stock_and_hits():
    recs = [
        _miss("2026-06-20", "2344", "漲", "跌", "量縮誤判止穩"),
        _hit("2026-06-21", "2344", "跌"),               # 命中→不算教訓
        _miss("2026-06-22", "2330", "漲", "跌", "別股"),  # 別的股
    ]
    m = recent_misses(recs, "2344", 3)
    assert len(m) == 1 and m[0]["date"] == "2026-06-20"


def test_add_lesson_dedup_and_cap(tmp_path):
    p = str(tmp_path / "l.json")
    add_lesson("2344", "2026-06-20", "量縮誤判", path=p)
    add_lesson("2344", "2026-06-20", "量縮誤判", path=p)   # 同股同日→去重
    assert len(load_lessons(p)) == 1
    add_lesson("2330", "2026-06-21", "追高", path=p)
    assert len(load_lessons(p)) == 2
    add_lesson("", "", "", path=p)                          # 空檢討不寫
    assert len(load_lessons(p)) == 2


def _hit_crit(date, stock, d, crit):
    """猜對且有檢討（例如漲幅不如預期）。"""
    return {"date": date, "stock": stock, "prediction": {"direction": d},
            "review": {"success": True, "direction_actual": d,
                       "results": {"direction": True}, "critique": crit}}


def test_lessons_prompt_includes_rate_and_critique(tmp_path):
    recs = [
        _miss("2026-06-20", "2344", "漲", "跌", "量縮誤判止穩"),
        _hit("2026-06-21", "2344", "跌"),
    ]
    txt = lessons_prompt(recs, "2344", path=str(tmp_path / "none.json"))
    assert "過去復盤回饋" in txt and "量縮誤判止穩" in txt and "命中率" in txt


def test_lessons_prompt_feeds_back_correct_prediction_critique(tmp_path):
    # 猜對但有隱憂的檢討，現在也要回饋（不再只餵猜錯的）
    recs = [
        _hit_crit("2026-06-21", "2344", "漲", "漲幅遠不如預期、盤中開高走低"),
    ]
    txt = lessons_prompt(recs, "2344", path=str(tmp_path / "none.json"))
    assert "漲幅遠不如預期" in txt and "命中✅" in txt


def test_lessons_prompt_empty_when_nothing(tmp_path):
    assert lessons_prompt([], "2344", path=str(tmp_path / "none.json")) == ""
