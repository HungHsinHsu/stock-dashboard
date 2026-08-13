"""臨時／診斷用：撈指定股票的收盤價與三條均線（支撐1/2/3 = MA5/MA20/MA60），
供人工決定「回檔承接法」的分批掛單價。

跑在 GitHub Actions（乾淨 IP）避開 TWSE 對雲端 IP（Streamlit/本機 sandbox）的限流。

用法：
  python -m jobs.quote                    # 預設四檔（南亞科/玉山金/凱基金/富邦金）
  QUOTE_CODES=2330,2454 python -m jobs.quote
  QUOTE_MONTHS=24 python -m jobs.quote    # 拉長回溯期，用來確認「前高／前低到底在哪」
"""
import os
from core.data import fetch_daily, fetch_foreign_flow, fetch_intraday
from core.fundamentals import fetch_valuation, valuation_notes
from core.indicators import compute_indicators
from core.holdings import position_pct, HIGH_POS_PCT
from core.tz import now_tw

DEFAULT = ["2408", "2884", "2883", "2881"]


def _print_intraday(codes):
    """盤中即時報價（唯一的盤中資料來源）。⚠️ 只供人工看『現在到哪了』——
    紀律判斷一律看收盤，盤中影線會把均線規則洗成雜訊。"""
    print(f"===== 盤中即時（查詢時間 {now_tw():%Y-%m-%d %H:%M:%S} 台灣）=====")
    quotes = fetch_intraday(codes)
    if not quotes:
        print("  抓不到盤中報價（非交易時段、或 MIS 未回應）")
        return
    for c in codes:
        q = quotes.get(str(c))
        if not q:
            print(f"  {c}: 無報價")
            continue
        chg = f"{q['chg_pct']:+.2f}%" if q.get("chg_pct") is not None else "—"
        print(f"  {q.get('name') or c} ({c}): {q.get('price')} {chg}"
              f"（昨收 {q.get('prev_close')}｜開 {q.get('open')} 高 {q.get('high')} "
              f"低 {q.get('low')}｜量 {q.get('volume')}｜{q.get('at')}｜{q.get('source')}）")
    print()


def _best_trade(df, since):
    """區間內『買在最低、之後賣在最高』的最佳單筆。回 dict 或 None。

    關鍵：賣必須晚於買。若最高點出現在最低點之前，那組合根本做不到（除非放空），
    所以用「每個日期之後的最高價」去配，不能直接拿區間 max 減 min。

    同時給兩個版本：
      ・盤中(High/Low)：理論極限，實務上抓不到
      ・收盤價：至少是收盤後看得到的價，比較接近人做得到的上限
    """
    d = df[df.index >= since]
    if len(d) < 2:
        return None
    out = {"first": str(d.index[0].date()), "last": str(d.index[-1].date()), "n": len(d)}
    for tag, lo_col, hi_col in (("盤中", "Low", "High"), ("收盤", "Close", "Close")):
        if lo_col not in d.columns or hi_col not in d.columns:
            continue
        best = None
        for i in range(len(d) - 1):
            buy = float(d[lo_col].iloc[i])
            if buy <= 0:
                continue
            after = d[hi_col].iloc[i + 1:]
            j = int(after.values.argmax())
            sell = float(after.iloc[j])
            gain = sell / buy - 1
            if best is None or gain > best["gain"]:
                best = {"gain": gain, "buy": buy, "sell": sell,
                        "buy_date": str(d.index[i].date()),
                        "sell_date": str(after.index[j].date())}
        if best:
            out[tag] = best
    return out


def _print_extremes(df):
    """整段抓到的資料裡的最高／最低（含日期）。

    教訓：只抓 5 個月就斷言「1130 是前高、1200 算創新高」——那只是視窗內的高點，
    不是歷史高點。談壓力/前高之前，先把回溯期印出來，讓「這是多久以來的高點」
    是看得到的事實，而不是預設值造成的錯覺。"""
    if df is None or getattr(df, "empty", True):
        return
    print(f"  回溯期   : {df.index[0].date()} ~ {df.index[-1].date()}"
          f"（{len(df)} 個交易日）")
    for tag, col, fn in (("最高", "High", "idxmax"), ("最低", "Low", "idxmin")):
        if col not in df.columns:
            continue
        i = getattr(df[col], fn)()
        print(f"    區間{tag}(盤中) {float(df[col].loc[i]):.2f} @ {i.date()}")
    if "Close" in df.columns:
        hi, lo = df["Close"].idxmax(), df["Close"].idxmin()
        print(f"    區間最高(收盤) {float(df['Close'].loc[hi]):.2f} @ {hi.date()}"
              f"　最低(收盤) {float(df['Close'].loc[lo]):.2f} @ {lo.date()}")


def volume_profile(df, days=45, bands=8):
    """簡易籌碼分布：把近 days 天依收盤價切成 bands 段，統計每段的成交量與天數。

    為什麼要用量而不是天數：判斷「上方壓力多重」時，我一開始用「幾天收在這個價格帶」
    當代理，但那把成交 2 萬張的一天跟 8 萬張的一天算成一樣重——套牢的張數差 4 倍。
    壓力的本質是「有多少張被套在那裡」，所以要用量加權。

    回 [(下界, 上界, 量, 天數), ...]（由高價到低價）；資料不足回 []。"""
    cols = getattr(df, "columns", [])
    if "Close" not in cols or "Volume" not in cols:
        return []
    d = df.tail(days)
    closes, vols = d["Close"].astype(float), d["Volume"].astype(float)
    lo, hi = float(closes.min()), float(closes.max())
    if not (hi > lo):
        return []
    step = (hi - lo) / bands
    out = []
    for b in range(bands - 1, -1, -1):
        b_lo = lo + step * b
        # 最高那一段用閉區間，否則最高價那根會落在所有區間之外被漏掉
        b_hi = lo + step * (b + 1)
        sel = (closes >= b_lo) & (closes <= b_hi if b == bands - 1 else closes < b_hi)
        out.append((b_lo, b_hi, float(vols[sel].sum()), int(sel.sum())))
    return out


def _print_volume_profile(df, days=45, bands=8):
    prof = volume_profile(df, days=days, bands=bands)
    if not prof:
        return
    cur = float(df["Close"].iloc[-1])
    total = sum(p[2] for p in prof) or 1.0
    print(f"  籌碼分布  : 近 {min(days, len(df))} 天依收盤價分 {bands} 段"
          f"（量加權；現價 {cur:.1f}）")
    peak = max(p[2] for p in prof)
    for b_lo, b_hi, vol, n in prof:
        bar = "█" * max(1, round(vol / peak * 20)) if vol else ""
        here = " ←現價" if b_lo <= cur <= b_hi else ""
        # 單位是「張」不是「股」：Volume 欄位存的是股數（TWSE 原生給股，TPEx 的張已在
        # core/tpex.py 換算成股），這裡除 1000 才是張。原本直接把股數標成「張」，
        # 印出來的數字大了 1000 倍——看起來很合理（就是個大數字），所以一直沒被發現。
        print(f"    {b_lo:7.1f}~{b_hi:7.1f}  {vol / 1000:9.0f} 張 "
              f"({vol / total * 100:4.1f}%) {n:2d}天 {bar}{here}")


def _print_recent_volume(df, n=12):
    """近 n 根的收盤與量——判斷『這波反彈是帶量攻擊還是量縮反彈』的最直接證據。"""
    cols = getattr(df, "columns", [])
    if "Close" not in cols or "Volume" not in cols:
        return
    d = df.tail(n)
    avg = float(df["Volume"].tail(60).mean()) if len(df) >= 20 else None
    print(f"  近{len(d)}日量  : （括號＝與近60日均量的倍數）")
    parts = []
    for dt, c, v in zip(d.index, d["Close"].astype(float), d["Volume"].astype(float)):
        r = f"×{v / avg:.2f}" if avg else "—"
        parts.append(f"{dt.date()} 收{c:.0f} 量{v:.0f}({r})")
    for i in range(0, len(parts), 3):
        print("             " + "、".join(parts[i:i + 3]))


def ma20_roll(df, n=8, window=20):
    """月線斜率接下來會翻正還是續跌——列出「即將滾出 window 日窗口的舊價」。

    MA20 明天的變化 ＝ (明天收盤 − 即將滾出的那根收盤) / 20。所以只要看那根舊價
    比現價高還低，就知道均線會被帶上去還是壓下來，不必等它自己翻。
    這正是 CLAUDE.md 那條教訓要問的：是價格漲過去、還是均線追上來？

    回 (現價, [(日期, 舊價, 會不會拉升), ...])；資料不足回 (None, [])。

    ⚠️ 索引方向踩過坑：目前窗口涵蓋最後 window 根，其中最舊的那根（index len-window）
    明天first掉出去，後天換 len-window+1……所以要 **+k 往未來走**。寫成 −k 會列出
    「過去幾天已經掉出去的」，方向剛好相反，看起來卻一樣合理。"""
    if "Close" not in getattr(df, "columns", []):
        return None, []
    closes = df["Close"].dropna()
    if len(closes) <= window:
        return None, []
    cur = float(closes.iloc[-1])
    rows = []
    for k in range(n):
        i = len(closes) - window + k      # 第 k+1 天後將掉出窗口的那根
        if i >= len(closes):
            break
        old = float(closes.iloc[i])
        rows.append((closes.index[i].date(), old, cur > old))
    return cur, rows


def _print_ma20_roll(df, n=8):
    cur, rows = ma20_roll(df, n=n)
    if not rows:
        return
    print(f"  月線換手  : 未來 {len(rows)} 天要滾出 20 日窗口的舊價"
          f"（比現價 {cur:.2f} 低＝滾掉後月線被帶上去；比現價高＝月線被壓下來）")
    print("             " + "、".join(
        f"{d}={old:.1f}({'↑拉升' if up else '↓拖累'})" for d, old, up in rows))


def fill_odds(df, limit, n=20):
    """掛一張限價買單在 limit，最近 n 天有幾天真的碰得到？

    承接法的掛單價來自「支撐 × 1.02」，而支撐常常低於現價——於是「掛在對的位置」
    跟「掛得到」天生互相拉扯。使用者會直覺想調高掛單價，但調高會同時放大停損距離
    （停損價是固定的支撐，買越貴賠率越差），所以這件事不能憑感覺，要用數字談。

    這裡只回答其中一半：**這個價位最近多常被摸到**。另一半（買貴了賠率剩多少）
    要拿 limit 跟停損價自己算，兩邊合起來才是「該掛多少」。

    限價買單成交條件用「當日最低 ≤ limit」近似：開盤集合競價若開盤價 ≤ limit 也會
    成交，而開盤價 ≥ 當日最低，所以這個條件已經涵蓋開盤那一撮。

    回 (命中天數, 統計天數, [(日期, 前收, 開盤, 最低, 是否命中, 最低相對前收%), ...])；
    資料不足回 (0, 0, [])。
    """
    cols = set(getattr(df, "columns", []))
    if not {"Low", "Close"} <= cols or limit is None:
        return 0, 0, []
    d = df.tail(n + 1)                     # 多取一根當第一天的「前收」
    if len(d) < 2:
        return 0, 0, []
    closes = [float(x) for x in d["Close"]]
    lows = [float(x) for x in d["Low"]]
    opens = [float(x) for x in d["Open"]] if "Open" in cols else [None] * len(d)
    dates = [x.date() for x in d.index]
    rows = []
    for i in range(1, len(d)):
        prev = closes[i - 1]
        rows.append((dates[i], prev, opens[i], lows[i], lows[i] <= limit,
                     (lows[i] / prev - 1) * 100 if prev else None))
    return sum(1 for r in rows if r[4]), len(rows), rows


def _print_fill_odds(df, limit, n=20):
    hits, total, rows = fill_odds(df, limit, n=n)
    if not total:
        return
    print(f"  掛單命中  : 限價 {limit:.2f} 在最近 {total} 天有 {hits} 天碰得到"
          f"（{hits / total * 100:.0f}%）；判準＝當日最低 ≤ 掛單價")
    deltas = sorted(r[5] for r in rows if r[5] is not None)
    if deltas:
        mid = deltas[len(deltas) // 2]
        print(f"             當日最低相對前收：中位數 {mid:+.2f}%、"
              f"最深 {deltas[0]:+.2f}%、最淺 {deltas[-1]:+.2f}%")
        print("             （用前收 × (1+x%) 就能把上面這排換算成任一掛單價的命中率）")


def _print_market(months=12):
    """大盤（加權指數）現在站在哪裡——決定『整體加減碼』時的共同背景。

    個股的位階只回答「這一檔貴不貴」，回答不了「現在該不該持有這麼多股票」。
    後者要看大盤自己的位階與均線結構，否則會出現「每一檔都說可以買，但整體
    已經在高檔」的組合——十檔各自合理，加起來就是滿倉買在頭部。

    順手印台指期與美股隔夜，因為台股開盤前的方向主要由這兩個決定。
    """
    from core.data import fetch_index, fetch_taifex_detail, fetch_us_overnight
    print("===== 大盤 =====")
    try:
        df = fetch_index(months=months)
    except Exception as e:
        print(f"  加權指數抓取失敗：{type(e).__name__} {e}")
        df = None
    if df is not None and not getattr(df, "empty", True):
        ind = compute_indicators(df, {})
        print(f"  資料日   : {df.index[-1].date()}")
        print(f"  收盤/前收: {ind.get('close')} / {ind.get('prev_close')}")
        print(f"  月線 MA20: {ind.get('ma20')}　季線 MA60: {ind.get('ma60')}")
        print(f"  排列={ind.get('ma_align')} 月線斜率(ma20_slope5)={ind.get('ma20_slope5')}")
        pos = position_pct(df)
        if pos is not None:
            print(f"  位階     : {pos:.1f}%（近120日收盤區間）")
        _print_extremes(df)
        tail = df["Close"].tail(10)
        print("  近10日收盤:", ", ".join(f"{d.date()}={v:,.0f}" for d, v in tail.items()))
        # 月線換手：判斷「月線何時可能上穿季線」的唯一直接依據。少了它只能說
        # 「方向確定、時間不知道」——而『可以加碼』這條線正是綁在這個交叉上。
        _print_ma20_roll(df)
    else:
        print("  加權指數：抓不到（TWSE 未回應）")
    try:
        tx = fetch_taifex_detail()
        print(f"  台指期   : {tx}" if tx else "  台指期   : 抓不到")
    except Exception as e:
        print(f"  台指期抓取失敗：{type(e).__name__} {e}")
    try:
        us, us_date = fetch_us_overnight(with_date=True)
        print(f"  美股隔夜 : {us}（最後交易日 {us_date}）")
    except Exception as e:
        print(f"  美股隔夜抓取失敗：{type(e).__name__} {e}")


def _print_window(code, df, since):
    r = _best_trade(df, since)
    if not r:
        print(f"  區間分析：{since} 起資料不足")
        return
    print(f"  區間 {r['first']} ~ {r['last']}（{r['n']} 個交易日）")
    for tag in ("盤中", "收盤"):
        b = r.get(tag)
        if b:
            print(f"    最佳單筆({tag})：{b['buy_date']} 買 {b['buy']:.2f} → "
                  f"{b['sell_date']} 賣 {b['sell']:.2f}　＝ {b['gain'] * 100:+.1f}%")


def run(codes=None):
    if codes is None:
        env = [c.strip() for c in os.environ.get("QUOTE_CODES", "").split(",") if c.strip()]
        codes = env or DEFAULT
    _print_intraday(codes)
    try:
        months = max(1, int(os.environ.get("QUOTE_MONTHS", "").strip() or 5))
    except ValueError:
        months = 5
    # 大盤背景：QUOTE_MARKET=0 可關掉（只想看個股時省幾秒）
    if os.environ.get("QUOTE_MARKET", "1").strip() != "0":
        _print_market(months=months)
    # 估值：每日「選一檔最優秀標的」時要同時看基本面，逐檔查太慢也容易被限流，
    # 一次抓全市場再查表。抓不到就留空——寧可標明沒有，也不要給看起來像基本面的猜測。
    try:
        valuation = fetch_valuation()
    except Exception as e:
        print(f"[valuation] 取得失敗，本次不附估值：{type(e).__name__} {e}")
        valuation = {}
    for c in codes:
        try:
            df = fetch_daily(c, months=months)
        except Exception as e:
            print(f"{c}: 抓取失敗 {type(e).__name__}: {e}")
            continue
        if df is None or getattr(df, "empty", True):
            # 上櫃已於 core/tpex.py 接上（TWSE 抓不到會自動改問 TPEx），所以走到這裡
            # 代表兩邊都沒有——原本那句「上櫃永遠會是空的」現在是錯的，留著會把人
            # 引去查早就修好的方向。剩下的可能性只有這三個。
            print(f"{c}: 上市(TWSE)與上櫃(TPEx)都抓不到日線。可能是代號錯、"
                  f"已下市/暫停交易，或兩邊同時限流——隔幾分鐘再試一次可分辨。")
            continue
        ind = compute_indicators(df, {})
        print(f"===== {c} =====")
        _print_extremes(df)
        # 起算日沿用 admin_date 這個既有輸入欄，避免為一次性分析去改 workflow
        since = os.environ.get("ADMIN_DATE", "").strip()
        if since:
            _print_window(c, df, since)
        print(f"  資料日   : {df.index[-1].date()}")
        print(f"  收盤/前收: {ind.get('close')} / {ind.get('prev_close')}")
        print(f"  支撐1 MA5 : {ind.get('ma5')}")
        print(f"  支撐2 MA20: {ind.get('ma20')}")
        print(f"  支撐3 MA60: {ind.get('ma60')}")
        print(f"  量比={ind.get('vol_ratio')} 排列={ind.get('ma_align')} "
              f"月線斜率(ma20_slope5)={ind.get('ma20_slope5')}")
        # 位階：判斷「貴不貴」的關鍵。同樣的進場訊號，位階 30% 跟 85% 的風險報酬天差地遠，
        # 所以決定掛單價時一定要跟均線一起看（≥HIGH_POS_PCT 時承接法連加碼都會擋下來）。
        pos = position_pct(df)
        if pos is None:
            print("  位階     : 無法計算（資料不足 120 日，可能是新上市）")
        else:
            print(f"  位階     : {pos:.1f}%（近120日收盤區間；≥{HIGH_POS_PCT:.0f}% 算中上緣）")
        # 最近 N 根收盤，看近期走勢(噴高→回落 or 續強)。
        # 預設 7 根夠看「昨天到今天」，但要判斷底部/頭部型態（W 底的第一隻腳在哪、
        # 是不是破底後 V 轉）7 根遠遠不夠——少幾根就只能猜。用 QUOTE_TAIL 拉長。
        try:
            ntail = max(2, int(os.environ.get("QUOTE_TAIL", "").strip() or 7))
        except ValueError:
            ntail = 7
        tail = df.tail(ntail)
        print(f"  近{ntail}日收盤:", ", ".join(f"{d.date()}={round(float(v), 2)}"
              for d, v in tail["Close"].items()))
        _print_recent_volume(df)
        _print_volume_profile(df, days=ntail)
        _print_ma20_roll(df)
        # 掛單命中率：QUOTE_LIMIT 給定就用它，否則用支撐1×1.02（承接法的預設掛法），
        # 讓「這個掛單價到底掛不掛得到」有數字可談，而不是各自憑感覺猜。
        try:
            limit = float(os.environ.get("QUOTE_LIMIT", "").strip() or 0) or None
        except ValueError:
            limit = None
        if limit is None and ind.get("ma5"):
            limit = float(ind["ma5"]) * 1.02
        if limit:
            _print_fill_odds(df, limit)
        v = valuation.get(str(c))
        notes = valuation_notes(v)
        print(f"  估值     : {'｜'.join(notes) if notes else '（無資料：當日尚未發布、或非上市股）'}")
        # 外資買賣超(T86)：判斷賣壓有沒有停、是承接法第四關
        try:
            fo = fetch_foreign_flow(c)
        except Exception as e:
            fo = {"err": f"{type(e).__name__}: {e}"}
        print(f"  外資T86: {fo}")


if __name__ == "__main__":
    run()
