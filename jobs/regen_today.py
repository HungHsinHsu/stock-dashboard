"""一次性：用最新(修好『美股/台指期資料日期是否已被台股消化』判斷)的邏輯，
重跑『今天』的開盤預測並覆蓋掉舊的（force=True）。

只在確認今天的預測是『餵到錯/過舊資料』、需要重來時手動觸發。
正常日子不要跑——開盤預測仍遵守『不事後竄改』的鐵律。
"""
from jobs import morning


def run():
    print("[regen_today] 用修正後邏輯重跑今天開盤預測(force 覆蓋)…")
    morning.run(force=True)


if __name__ == "__main__":
    run()
