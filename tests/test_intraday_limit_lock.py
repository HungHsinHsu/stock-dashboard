"""漲停/跌停鎖死時 MIS 價格欄位可能回 "0.0000"，不能當成合法價格。"""
import pytest
from core.data import _mis_num, _mis_price


@pytest.mark.parametrize("raw", ["0.0000", "0", "0.00_0.00_", "-0.5"])
def test_zero_or_negative_price_is_rejected(raw):
    assert _mis_price(raw) is None, "價格 ≤0 必須視為無效，否則會算出 −100% 的假跌停"


@pytest.mark.parametrize("raw,want", [("502.0000", 502.0), ("43.70_43.75_", 43.7)])
def test_normal_price_passes_through(raw, want):
    assert _mis_price(raw) == want


@pytest.mark.parametrize("raw", ["-", "", None])
def test_missing_stays_none(raw):
    assert _mis_price(raw) is None


def test_mis_num_still_allows_zero_for_volume():
    # 量為 0（尚未成交）是合法的，所以 _mis_num 不能跟著擋掉 0
    assert _mis_num("0") == 0.0
