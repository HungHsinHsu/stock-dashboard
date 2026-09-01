"""命中率統計（admin_action=stats）：分組正確、盲猜基準正確、壞紀錄跳過。"""
from jobs.dbadmin import hit_stats


def _rec(date, stock, hit, actual):
    return {"date": date, "stock": stock,
            "review": {"results": {"direction": hit}, "direction_actual": actual}}


def test_hit_stats_groups_and_baseline():
    records = [
        _rec("2026-08-01", "大盤", True, "漲"),
        _rec("2026-08-01", "2330", False, "漲"),
        _rec("2026-08-02", "2330", True, "跌"),
        _rec("2026-09-01", "大盤", False, "跌"),
        # 沒有 review 的紀錄（當天只做了預測、還沒復盤）不能算進分母
        {"date": "2026-09-02", "stock": "2330", "prediction": {"direction": "漲"}},
    ]
    st = hit_stats(records)
    assert st["overall"]["n"] == 4 and st["overall"]["hits"] == 2
    assert st["overall"]["rate"] == 0.5
    # 盲猜漲基準＝實際收漲比例：4 筆中 2 筆漲
    assert st["overall"]["baseline_up"] == 0.5
    assert st["market"] == {"n": 2, "hits": 1, "rate": 0.5, "baseline_up": 0.5}
    assert st["stocks"]["n"] == 2 and st["stocks"]["hits"] == 1
    assert st["by_stock"]["2330"]["n"] == 2
    assert st["by_month"]["2026-08"]["n"] == 3
    assert st["by_month"]["2026-09"] == {
        "n": 1, "hits": 0, "rate": 0.0, "baseline_up": 0.0}


def test_hit_stats_empty():
    st = hit_stats([])
    assert st["overall"]["n"] == 0 and st["overall"]["rate"] is None
