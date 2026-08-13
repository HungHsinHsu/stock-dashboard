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


def test_tech_ready_true_for_entry_and_foreign_gated_watch():
    # 技術面健康回檔到位：外資停手→進場、外資賣超/未知→觀望，三者都 tech_ready＝激進版可當天接
    ind = {"close": 223, "prev_close": 222, "ma20": 181,
           "dist_support1_pct": 0.5, "dist_support3_pct": 57, "vol_ratio": 0.8}
    assert entry_setup(ind, foreign_stopped=True)["tech_ready"] is True    # 進場
    assert entry_setup(ind, foreign_stopped=False)["tech_ready"] is True   # 外資賣超→觀望
    assert entry_setup(ind)["tech_ready"] is True                           # 外資未知→觀望


def test_tech_ready_false_for_surge_vacuum_and_rollover():
    # 漲停噴出(非回檔)、真空帶、月線下彎轉弱 → 都不是健康回檔 → tech_ready False(激進版也不接)
    surge = {"close": 1320, "prev_close": 1200, "ma20": 1252,
             "dist_support1_pct": 0.2, "dist_support3_pct": 40, "vol_ratio": 0.67,
             "ma20_slope5": 17.8}
    assert entry_setup(surge, foreign_stopped=True)["tech_ready"] is False
    vacuum = {"close": 206, "prev_close": 204, "ma20": 181,
              "dist_support1_pct": -7.2, "dist_support3_pct": 45, "vol_ratio": 0.8}
    assert entry_setup(vacuum, foreign_stopped=True)["tech_ready"] is False
    rollover = {"close": 223, "prev_close": 222, "ma20": 181, "ma20_slope5": -4.0,
                "dist_support1_pct": 0.5, "dist_support3_pct": 57, "vol_ratio": 0.8}
    assert entry_setup(rollover, foreign_stopped=True)["tech_ready"] is False


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


def test_below_season_reason_is_entry_wording_not_exit_order():
    """跌破季線的『避開』理由不能寫成出場指令。

    實例（2026-08-04 華邦電）：漲停 +9.8%、外資投信雙買，收盤仍在季線下。
    持股頁的 exit_setup 有漲停守門、判「續抱」；選股清單若寫「全數出場」，
    同一檔同一天就會一個叫抱、一個叫賣。避開＝別買，不是叫人賣。
    """
    from core.rules import entry_setup
    ind = {"close": 157.0, "prev_close": 143.0, "ma20": 156.68, "ma60": 162.77,
           "dist_support1_pct": 15.7, "dist_support3_pct": -3.5, "vol_ratio": 1.02}
    r = entry_setup(ind, code="2344")
    assert r["ceiling"] == "避開"
    assert "全數出場" not in r["reason"]      # 進場判斷不下出場指令
    assert "不買進" in r["reason"]
    assert "162.8" in r["reason"]             # 標明季線價位
    assert "+9.8%" in r["reason"]             # 標明當日漲跌，否則看不出今天是漲停


def test_below_season_but_season_above_ma20_says_not_yet_recovered_not_turning_bearish():
    """季線高過月線＝60 日窗口含崩跌前舊高，理由不能講成「趨勢轉空」。

    2026-08-13 同一天三檔全中：
      00735      收 103.6　MA20  95.89　MA60 105.53
      世界先進    收 163.0　MA20 154.80　MA60 169.95
      廣達       收 325.0　MA20 312.73　MA60 346.68
    三檔的季線都高過月線約 10%，但都是價 > MA5 > MA20 的多頭排列——是崩跌後
    修復、還沒漲回 6 月舊高，不是趨勢轉空。上限仍是避開（門檻不放寬），
    但要叫人去盯月線斜率，不是盯一條被舊高墊高的季線。
    """
    from core.rules import entry_setup
    ind = {"close": 163.0, "prev_close": 159.5, "ma20": 154.80, "ma60": 169.95,
           "dist_support1_pct": 4.29, "dist_support3_pct": -4.09, "vol_ratio": 0.72}
    r = entry_setup(ind, code="5347")
    assert r["ceiling"] == "避開"              # 門檻一格都沒放寬
    assert "不是趨勢轉空" in r["reason"]        # 明說它不是轉空，別讓人誤讀
    assert "還沒漲回前高" in r["reason"]
    assert "高過月線" in r["reason"]
    assert "ma20_slope5" in r["reason"]        # 指向沒被汙染的訊號
    assert "154.8" in r["reason"]              # 標出月線價位，好對照
    assert "不買進" in r["reason"]             # 與既有契約一致：避開＝別買


def test_below_season_with_normal_ma_order_keeps_the_old_wording():
    """正常排列（季線在月線下）時維持原本措辭，不要因為新分支改變既有行為。"""
    from core.rules import entry_setup
    ind = {"close": 90.0, "prev_close": 92.0, "ma20": 100.0, "ma60": 95.0,
           "dist_support1_pct": -8.0, "dist_support3_pct": -5.3, "vol_ratio": 1.1}
    r = entry_setup(ind, code="2330")
    assert r["ceiling"] == "避開"
    assert "等收盤站回季線再看" in r["reason"]
    assert "高過月線" not in r["reason"]


def test_missing_ma20_falls_back_to_the_plain_wording():
    """缺 ma20 時不能炸，也不該走新分支。"""
    from core.rules import entry_setup
    r = entry_setup({"close": 90.0, "ma60": 95.0, "dist_support3_pct": -5.3}, code="2330")
    assert r["ceiling"] == "避開" and "等收盤站回季線再看" in r["reason"]
