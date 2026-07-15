from core.rules import (
    signal_ceiling, constrain_signal, entry_setup, exit_setup, is_denied,
)


# ─────────────── 出場紀律 exit_setup（移動停利，跟著均線走）───────────────
def test_exit_hold_above_ma20():
    # 收盤站穩月線之上 → 續抱
    s = exit_setup({"close": 100, "ma20": 95, "ma60": 80})
    assert s["action"] == "續抱"


def test_exit_below_ma60_is_full_exit():
    # 跌破季線 → 全數出場（不管批數）
    s = exit_setup({"close": 78, "ma20": 95, "ma60": 80})
    assert s["action"] == "出場"


def test_exit_below_ma20_unknown_batches_is_trim():
    # 跌破月線、仍在季線上、批數未知（網頁全當持有）→ 減碼警訊
    s = exit_setup({"close": 90, "ma20": 95, "ma60": 80}, batches=None)
    assert s["action"] == "減碼"


def test_exit_below_ma20_but_building_still_holds():
    # 建倉未滿三批：月線是加碼支撐、非減碼 → 續抱
    s = exit_setup({"close": 90, "ma20": 95, "ma60": 80}, batches=1)
    assert s["action"] == "續抱"


def test_exit_below_ma20_full_position_trims():
    # 滿三批後跌破月線 → 轉保護獲利、減碼
    s = exit_setup({"close": 90, "ma20": 95, "ma60": 80}, batches=3)
    assert s["action"] == "減碼"


def test_exit_no_close_returns_none():
    assert exit_setup({"ma20": 95, "ma60": 80})["action"] is None


# 華邦電快照：支撐1≈222、MA20≈181、支撐3≈142。close 用「距 % 」表示位置。

def test_denylist_blocks_entry():
    assert is_denied("3481") and is_denied("00631L")
    ind = {"close": 100, "ma20": 90, "dist_support1_pct": 1.0, "dist_support3_pct": 30}
    assert signal_ceiling(ind, code="3481") == "避開"


def test_below_support3_is_stop_loss_avoid():
    # 收盤跌破支撐3(長期均線) → 停損 → 避開
    ind = {"close": 138, "prev_close": 145, "ma20": 160,
           "dist_support1_pct": -38, "dist_support3_pct": -3.5, "vol_ratio": 1.4}
    assert signal_ceiling(ind) == "避開"


def test_pullback_to_support_with_shrink_volume_allows_entry():
    # 情境一：回檔到支撐1(±2%內)、收盤止穩(close>=prev)、量縮、外資已停手 → 進場
    ind = {"close": 223, "prev_close": 222, "ma20": 181,
           "dist_support1_pct": 0.5, "dist_support3_pct": 57, "vol_ratio": 0.8}
    s = entry_setup(ind, foreign_stopped=True)
    assert s["ceiling"] == "進場" and "支撐1" in s["at_batch"]


def test_rollover_downtrend_demotes_entry_to_watch():
    # 趨勢健康關：短線全到位(到價、止穩、量縮、外資停手)，但月線 MA20 下彎(高檔回落)
    # → 不算上升趨勢中的健康回檔，降為觀望（仁寶、晶豪科那種噴上去又摔下來的情況）
    ind = {"close": 223, "prev_close": 222, "ma20": 181, "ma20_slope5": -4.0,
           "dist_support1_pct": 0.5, "dist_support3_pct": 57, "vol_ratio": 0.8}
    s = entry_setup(ind, foreign_stopped=True)
    assert s["ceiling"] == "觀望" and "月線" in s["reason"]


def test_healthy_pullback_rising_ma20_allows_entry():
    # 月線 MA20 還在往上(斜率>0)＝健康回檔 → 進場
    ind = {"close": 223, "prev_close": 222, "ma20": 181, "ma20_slope5": 3.0,
           "dist_support1_pct": 0.5, "dist_support3_pct": 57, "vol_ratio": 0.8}
    s = entry_setup(ind, foreign_stopped=True)
    assert s["ceiling"] == "進場"


def test_surge_day_near_support_is_not_pullback_entry():
    # 藥華藥式假陽性：當日漲停(+10%)、收盤貼近 MA5(支撐1)、量縮(漲停惜售)、外資停手、月線上彎，
    # 靜態四關全過，但那根是「噴出」不是「回檔」→ 必須夾成觀望，不給進場（不追漲停）。
    ind = {"close": 1320, "prev_close": 1200, "ma20": 1252,
           "dist_support1_pct": 0.2, "dist_support3_pct": 40, "vol_ratio": 0.67,
           "ma20_slope5": 17.8}
    s = entry_setup(ind, foreign_stopped=True)
    assert s["ceiling"] == "觀望"
    assert "大漲" in s["reason"] or "追高" in s["reason"] or "噴出" in s["reason"]


def test_mild_up_pullback_still_allows_entry():
    # 門檻不誤傷正常回檔：小漲(+1.8%)貼近支撐、量縮、外資停手 → 仍是進場
    ind = {"close": 226, "prev_close": 222, "ma20": 181,
           "dist_support1_pct": 0.5, "dist_support3_pct": 57, "vol_ratio": 0.8}
    s = entry_setup(ind, foreign_stopped=True)
    assert s["ceiling"] == "進場"


def test_foreign_unknown_stays_watch_not_entry():
    # 資料闕漏：技術面到位但外資無法確認 → 保守觀望，不給進場
    ind = {"close": 223, "prev_close": 222, "ma20": 181,
           "dist_support1_pct": 0.5, "dist_support3_pct": 57, "vol_ratio": 0.8}
    s = entry_setup(ind)                    # 不傳外資＝未知
    assert s["ceiling"] == "觀望" and "外資" in s["reason"]


def test_at_support_but_volume_not_shrunk_is_watch():
    # 到價但放量(量未縮) → 觀望，等收盤確認
    ind = {"close": 223, "prev_close": 222, "ma20": 181,
           "dist_support1_pct": 0.5, "dist_support3_pct": 57, "vol_ratio": 1.3}
    assert signal_ceiling(ind) == "觀望"


def test_at_support_but_not_stabilised_is_watch():
    # 到價但收盤還在破底(close<prev) → 觀望
    ind = {"close": 218, "prev_close": 230, "ma20": 181,
           "dist_support1_pct": -1.8, "dist_support3_pct": 53, "vol_ratio": 0.7}
    assert signal_ceiling(ind) == "觀望"


def test_vacuum_zone_high_position_is_watch_not_entry():
    # 位置偏高、不在任一支撐(真空帶) → 觀望（不是因為在支撐上方就追進）
    ind = {"close": 206, "prev_close": 204, "ma20": 181,
           "dist_support1_pct": -7.2, "dist_support3_pct": 45, "vol_ratio": 0.8}
    assert signal_ceiling(ind) == "觀望"


def test_scenario_two_reclaim_ma20_with_volume():
    # 情境二：帶量站回上方均線、收盤站穩、非空頭、外資已停手 → 進場
    ind = {"close": 184, "prev_close": 180, "ma20": 181, "ma_align": "糾結",
           "dist_support1_pct": -17, "dist_support3_pct": 29, "vol_ratio": 1.6}
    assert signal_ceiling(ind, foreign_stopped=True) == "進場"


def test_constrain_caps_llm_entry_when_not_setup():
    # LLM 喊進場但位置在真空帶 → 夾回觀望
    ind = {"close": 206, "prev_close": 204, "ma20": 181,
           "dist_support1_pct": -7.2, "dist_support3_pct": 45, "vol_ratio": 0.8}
    final, note = constrain_signal({"signal": "進場"}, ind)
    assert final == "觀望" and note


def test_constrain_watch_when_foreign_unknown():
    # 外資未知（資料闕漏）→ 不放行、夾成觀望（不再當進場）
    ind = {"close": 223, "prev_close": 222, "ma20": 181,
           "dist_support1_pct": 0.5, "dist_support3_pct": 57, "vol_ratio": 0.8}
    final, note = constrain_signal({"signal": "進場"}, ind)
    assert final == "觀望" and "外資" in note


def test_constrain_keeps_entry_when_foreign_stopped():
    ind = {"close": 223, "prev_close": 222, "ma20": 181,
           "dist_support1_pct": 0.5, "dist_support3_pct": 57, "vol_ratio": 0.8}
    final, note = constrain_signal({"signal": "進場"}, ind, foreign_stopped=True)
    assert final == "進場"


_ENTRY_IND = {"close": 223, "prev_close": 222, "ma20": 181,
              "dist_support1_pct": 0.5, "dist_support3_pct": 57, "vol_ratio": 0.8}


def test_foreign_still_selling_blocks_entry():
    # 技術面到位但外資仍賣超 → 夾回觀望
    final, note = constrain_signal({"signal": "進場"}, _ENTRY_IND,
                                   foreign_stopped=False)
    assert final == "觀望" and "外資仍在賣超" in note


def test_foreign_stopped_allows_entry():
    final, note = constrain_signal({"signal": "進場"}, _ENTRY_IND,
                                   foreign_stopped=True)
    assert final == "進場" and "外資已停止倒貨" in note
