"""籌碼分布要用量加權，且最高價那根不能被漏掉。"""
import pandas as pd
from jobs.quote import volume_profile


def _df(closes, vols):
    idx = pd.date_range("2026-06-01", periods=len(closes), freq="D")
    return pd.DataFrame({"Close": closes, "Volume": vols}, index=idx)


def test_bands_are_volume_weighted_not_day_counted():
    # 低價 3 天但量小、高價 1 天但量大 → 高價帶的量必須勝出
    df = _df([100.0, 100.0, 100.0, 200.0], [10.0, 10.0, 10.0, 900.0])
    prof = volume_profile(df, days=4, bands=2)
    hi_band, lo_band = prof[0], prof[-1]
    assert hi_band[2] == 900.0 and hi_band[3] == 1
    assert lo_band[2] == 30.0 and lo_band[3] == 3


def test_highest_bar_is_included():
    df = _df([10.0, 20.0, 30.0], [1.0, 1.0, 1.0])
    prof = volume_profile(df, days=3, bands=3)
    assert sum(p[3] for p in prof) == 3          # 三根都要被分到某一段
    assert sum(p[2] for p in prof) == 3.0


def test_flat_or_missing_data_returns_empty():
    assert volume_profile(_df([5.0] * 5, [1.0] * 5)) == []
    assert volume_profile(pd.DataFrame({"Close": [1.0, 2.0]})) == []
