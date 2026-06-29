# 大盤(加權指數)整合 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`).

**Goal:** 把大盤(TWSE 加權指數 TAIEX)摘要餵進開盤預測與收盤復盤、附在 Telegram 報告,並在 Streamlit 顯示;不增加 Claude 呼叫次數。

**Architecture:** 新增 `core/data.py:fetch_index`(抓指數日線)與 `core/market.py:market_summary`(純函式摘要);morning/evening 多抓一次指數,把摘要塞進原本那一次 Claude 呼叫的 prompt,並存進紀錄、附在報告;app.py 顯示大盤指標。

**Tech Stack:** 同主專案。資料源:TWSE `MI_5MINS_HIST`(已驗證:回傳 [日期,開,高,低,收],值含逗號,無量)。

## Global Constraints

- Python 3.11。TWSE 請求帶 `HEADERS`(已在 core/data.py)。
- 不新增 Claude 呼叫次數:大盤摘要併入既有 make_prediction / make_review 的 prompt。
- 大盤摘要 dict 形狀固定:`{close, prev_close, ma20, above_ma20(bool), direction("漲"/"跌"|None), pct(float|None)}`,值皆 float/bool/None。資料不足/抓不到 → market_summary 回 None。
- 既有紀錄相容:新欄位 `prediction["market"]` / `review["market"]`,讀取一律 `.get` 防呆;舊紀錄沒有也不能壞。
- 注入式設計:morning.run / evening.run 新增 `fetch_idx` 參數(預設真 `fetch_index`),測試注入假的、不打網路。
- 測試 pytest、不依賴網路/API。commit message 遵守 team standard,結尾 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。

---

### Task 1: core/data.py fetch_index + core/market.py market_summary

**Files:**
- Modify: `core/data.py`(新增 parse_index_json + fetch_index)
- Create: `core/market.py`
- Test: `tests/test_market.py`

**Interfaces:**
- Produces:
  - `parse_index_json(j) -> list[dict]`(純函式;欄位 Date/Open/High/Low/Close;非 OK/無 data → [])
  - `fetch_index(months=6, today=None) -> DataFrame`(index=Date,欄位 Open/High/Low/Close/MA20;抓不到回帶 schema 的空 DataFrame)
  - `market_summary(df) -> dict | None`(見 Global Constraints 形狀;df 空或 None → None)

- [ ] **Step 1: 寫失敗測試**

Create `tests/test_market.py`:

```python
import pandas as pd
from core.data import parse_index_json
from core.market import market_summary


def test_parse_index_json_ok():
    j = {"stat": "OK", "data": [
        ["115/06/27", "45,000.00", "45,500.00", "44,800.00", "45,337.91"],
    ]}
    rows = parse_index_json(j)
    assert len(rows) == 1
    r = rows[0]
    assert r["Open"] == 45000.0 and r["Close"] == 45337.91
    assert str(r["Date"].date()) == "2026-06-27"


def test_parse_index_json_not_ok():
    assert parse_index_json({"stat": "x"}) == []


def _df(closes):
    idx = pd.date_range("2026-01-01", periods=len(closes), freq="D")
    return pd.DataFrame({
        "Open": closes, "High": closes, "Low": closes, "Close": closes,
    }, index=idx)


def test_market_summary_up():
    df = _df([float(100 + i) for i in range(30)])
    df["MA20"] = df["Close"].rolling(20).mean()
    m = market_summary(df)
    assert m["close"] == 129.0 and m["prev_close"] == 128.0
    assert m["direction"] == "漲"
    assert m["above_ma20"] is True
    assert m["pct"] == round((129.0 - 128.0) / 128.0 * 100, 2)


def test_market_summary_empty_is_none():
    assert market_summary(pd.DataFrame()) is None
    assert market_summary(None) is None
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_market.py -v`
Expected: FAIL（`ImportError: cannot import name 'parse_index_json'` / `No module named 'core.market'`）

- [ ] **Step 3: 實作**

加到 `core/data.py` 末端:

```python
def parse_index_json(j):
    """TWSE MI_5MINS_HIST 回應 -> list[dict]；非 OK/無 data 回 []。"""
    if j.get("stat") != "OK" or "data" not in j:
        return []
    rows = []
    for row in j["data"]:
        # row: 日期, 開盤指數, 最高指數, 最低指數, 收盤指數
        try:
            parts = row[0].split("/")
            y = int(parts[0]) + 1911
            date = pd.Timestamp(f"{y}-{parts[1]}-{parts[2]}")
            rows.append({
                "Date": date,
                "Open": float(row[1].replace(",", "")),
                "High": float(row[2].replace(",", "")),
                "Low": float(row[3].replace(",", "")),
                "Close": float(row[4].replace(",", "")),
            })
        except (ValueError, IndexError):
            continue
    return rows


def fetch_index(months=6, today=None):
    """抓加權指數近 months 個月日線；回 DataFrame(含 MA20)；抓不到回空(帶 schema)。"""
    today = today or datetime.today()
    frames = []
    for i in range(months):
        d = today - relativedelta(months=i)
        ym = d.strftime("%Y%m01")
        url = (
            "https://www.twse.com.tw/indicesReport/MI_5MINS_HIST"
            f"?response=json&date={ym}"
        )
        try:
            r = requests.get(url, headers=HEADERS, timeout=10)
            frames.extend(parse_index_json(r.json()))
        except Exception:
            continue
    if not frames:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "MA20"])
    df = (
        pd.DataFrame(frames)
        .drop_duplicates("Date").sort_values("Date").set_index("Date")
    )
    df["MA20"] = df["Close"].rolling(20).mean()
    return df
```

Create `core/market.py`:

```python
import pandas as pd


def _last(series):
    if series is None or len(series) == 0:
        return None
    v = series.iloc[-1]
    return None if pd.isna(v) else float(v)


def market_summary(df):
    """加權指數摘要;df 空/None 回 None。值皆 float/bool/None。"""
    if df is None or df.empty:
        return None
    close = df["Close"]
    last_close = _last(close)
    prev_close = _last(close.iloc[:-1]) if len(close) >= 2 else None
    ma20 = _last(df["MA20"]) if "MA20" in df else None
    direction = None
    pct = None
    if last_close is not None and prev_close:
        direction = "漲" if last_close >= prev_close else "跌"
        pct = round((last_close - prev_close) / prev_close * 100, 2)
    return {
        "close": last_close,
        "prev_close": prev_close,
        "ma20": ma20,
        "above_ma20": ma20 is not None and last_close is not None
        and last_close >= ma20,
        "direction": direction,
        "pct": pct,
    }
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_market.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: Commit**

```bash
git add core/data.py core/market.py tests/test_market.py
git commit -m "$(cat <<'EOF'
Feat: 加大盤資料 fetch_index 與 market_summary

- core/data.py parse_index_json + fetch_index(MI_5MINS_HIST)
- core/market.py market_summary 純函式摘要 + 測試

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: 大盤餵進開盤預測(predict + morning)

**Files:**
- Modify: `core/predict.py`(make_prediction 加 market、format_prediction 加大盤行、_SYSTEM 提及大盤)
- Modify: `jobs/morning.py`(多抓指數、傳 market)
- Test: `tests/test_predict.py`(更新:make_prediction 帶 market;morning.run 注入 fetch_idx)

**Interfaces:**
- Consumes: `core.market.market_summary`、`core.data.fetch_index`。
- Produces:
  - `make_prediction(indicators, stock_name, market=None, llm=generate_json) -> dict`(回傳含 `market` 鍵)
  - `format_prediction(stock_name, date, prediction) -> str`(prediction 有 market 時多一行大盤)
  - `morning.run(today=None, llm=generate_json, fetch=fetch_daily, fetch_idx=fetch_index, notify=None) -> dict|None`

- [ ] **Step 1: 更新測試(先讓新行為失敗)**

把 `tests/test_predict.py` 的 `_fake_llm` 保持不變,並改 `make_prediction` 相關測試 + morning 測試:

```python
import pandas as pd
from core.predict import make_prediction, format_prediction, PREDICTION_SCHEMA
import jobs.morning as morning


def _fake_llm(system, user, schema, client=None):
    assert schema is PREDICTION_SCHEMA
    return {
        "signal": "觀望", "direction": "跌", "hold_ma20": False,
        "hold_support1": False, "reason": "量縮跌破MA20",
    }


def test_make_prediction_includes_market():
    ind = {"close": 203.0, "ma20": 186.5}
    market = {"direction": "跌", "pct": -0.7, "above_ma20": False}
    out = make_prediction(ind, "華邦電 (2344)", market=market, llm=_fake_llm)
    assert out["indicators"]["close"] == 203.0
    assert out["market"]["direction"] == "跌"


def test_format_prediction_shows_market():
    pred = {
        "signal": "觀望", "direction": "跌", "hold_ma20": False,
        "hold_support1": False, "reason": "量縮",
        "indicators": {"close": 203.0, "ma20": 186.5},
        "market": {"direction": "跌", "pct": -0.7, "above_ma20": False},
    }
    s = format_prediction("華邦電 (2344)", "2026-06-30", pred)
    assert "大盤" in s and "跌" in s


def test_format_prediction_no_market_ok():
    pred = {
        "signal": "觀望", "direction": "跌", "hold_ma20": False,
        "hold_support1": False, "reason": "x",
        "indicators": {"close": 203.0, "ma20": 186.5}, "market": None,
    }
    s = format_prediction("華邦電 (2344)", "2026-06-30", pred)
    assert "華邦電 (2344)" in s  # 不因缺 market 而壞


def _df_with_ma20(n=30):
    closes = [float(100 + i) for i in range(n)]
    idx = pd.date_range("2026-05-01", periods=n, freq="D")
    df = pd.DataFrame({"Open": closes, "High": closes, "Low": closes,
                       "Close": closes, "Volume": [1000.0] * n}, index=idx)
    df["MA20"] = df["Close"].rolling(20).mean()
    return df


def _idx_df(n=30):
    closes = [float(45000 + i) for i in range(n)]
    idx = pd.date_range("2026-05-01", periods=n, freq="D")
    df = pd.DataFrame({"Open": closes, "High": closes, "Low": closes,
                       "Close": closes}, index=idx)
    df["MA20"] = df["Close"].rolling(20).mean()
    return df


def test_morning_run_writes_with_market(tmp_path, monkeypatch):
    monkeypatch.setattr(morning, "HISTORY_PATH", str(tmp_path / "h.json"))
    sent = {}
    monkeypatch.setattr(morning, "tg", type("T", (), {
        "send": staticmethod(lambda text: sent.setdefault("text", text) or True)
    }))
    rec = morning.run(
        today=pd.Timestamp("2026-06-30"),
        llm=_fake_llm,
        fetch=lambda code, today=None: _df_with_ma20(),
        fetch_idx=lambda today=None: _idx_df(),
    )
    assert rec is not None
    assert rec["prediction"]["market"]["direction"] == "漲"
    assert "大盤" in sent["text"]


def test_morning_run_empty_data_notifies(tmp_path, monkeypatch):
    monkeypatch.setattr(morning, "HISTORY_PATH", str(tmp_path / "h.json"))
    sent = {}
    monkeypatch.setattr(morning, "tg", type("T", (), {
        "send": staticmethod(lambda text: sent.setdefault("text", text) or True)
    }))
    out = morning.run(
        today=pd.Timestamp("2026-06-30"),
        llm=_fake_llm,
        fetch=lambda code, today=None: pd.DataFrame(),
        fetch_idx=lambda today=None: _idx_df(),
    )
    assert out is None
    assert "缺漏" in sent["text"]
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_predict.py -v`
Expected: FAIL（make_prediction 不接受 market / morning.run 不接受 fetch_idx / 報告無大盤行）

- [ ] **Step 3: 改 core/predict.py**

把 `_SYSTEM` 結尾加一句:`"另提供大盤(加權指數)摘要，預測時請把大盤趨勢一併考慮。"`

把 `make_prediction` 改成:

```python
def make_prediction(indicators, stock_name, market=None, llm=generate_json):
    user = (
        f"股票：{stock_name}\n"
        f"技術指標(到昨日收盤為止)：\n{json.dumps(indicators, ensure_ascii=False)}\n"
        f"大盤(加權指數)摘要：\n{json.dumps(market, ensure_ascii=False)}"
    )
    pred = llm(_SYSTEM, user, PREDICTION_SCHEMA)
    pred["indicators"] = indicators
    pred["market"] = market
    return pred
```

在 `format_prediction` 的 return 之前組好大盤行,並把它接到輸出最後:

```python
def format_prediction(stock_name, date, prediction):
    ind = prediction.get("indicators", {})
    ma20 = ind.get("ma20")
    ma20_txt = f"{ma20:.1f}" if isinstance(ma20, (int, float)) else "—"
    lines = (
        f"📈 {stock_name} 開盤預測 {date}\n"
        f"進場訊號：{prediction['signal']}\n"
        f"方向：預期{prediction['direction']}\n"
        f"站穩MA20：{'是' if prediction['hold_ma20'] else '否'}(MA20={ma20_txt})\n"
        f"守住支撐1：{'是' if prediction['hold_support1'] else '否'}\n"
        f"理由：{prediction['reason']}"
    )
    mk = prediction.get("market") or {}
    if mk.get("direction"):
        pct = mk.get("pct")
        pct_txt = f" {pct:+.2f}%" if isinstance(pct, (int, float)) else ""
        ma_txt = "站上" if mk.get("above_ma20") else "跌破"
        lines += f"\n大盤：{mk['direction']}{pct_txt}，{ma_txt}自身MA20"
    return lines
```

- [ ] **Step 4: 改 jobs/morning.py**

import 區把 `fetch_daily, STOCKS` 那行改為也匯入 `fetch_index`,並加 `from core.market import market_summary`。把 `run` 簽章與內文改為:

```python
def run(today=None, llm=generate_json, fetch=fetch_daily,
        fetch_idx=fetch_index, notify=None):
    name, cfg = next(iter(STOCKS.items()))
    df = fetch(cfg["code"], today=today)

    if df.empty:
        tg.send("⚠️ 今日資料缺漏，已跳過開盤預測。")
        return None

    date = str(df.index[-1].date()) if today is None else str(today.date())
    indicators = compute_indicators(df, cfg["supports"])
    market = market_summary(fetch_idx(today=today))
    prediction = make_prediction(indicators, name, market=market, llm=llm)

    record = {
        "date": date, "stock": cfg["code"],
        "prediction": prediction, "review": None,
    }
    records = upsert_record(load_history(HISTORY_PATH), record)
    save_history(records, HISTORY_PATH)

    tg.send(format_prediction(name, date, prediction))
    return record
```

把 morning.py 結尾 dry-run 區的 make_prediction 呼叫也補上 market:

```python
        else:
            ind = compute_indicators(df, cfg["supports"])
            market = market_summary(fetch_index())
            pred = make_prediction(ind, name, market=market)
            print(format_prediction(name, str(df.index[-1].date()), pred))
```

(dry-run 區若沒 import market_summary/fetch_index,確保檔案頂端已匯入。)

- [ ] **Step 5: 跑測試確認通過 + 全套**

Run: `python -m pytest tests/test_predict.py -v`
Expected: PASS
Run: `python -m pytest -q`
Expected: 全部 PASS

- [ ] **Step 6: Commit**

```bash
git add core/predict.py jobs/morning.py tests/test_predict.py
git commit -m "$(cat <<'EOF'
Feat: 大盤摘要餵進開盤預測與報告

- make_prediction 加 market 參數，prompt 帶大盤，紀錄存 prediction.market
- format_prediction 附大盤行；morning.run 多抓指數(可注入 fetch_idx)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: 大盤餵進收盤復盤(review + evening)

**Files:**
- Modify: `core/review.py`(make_review 加 market、format_review 加大盤行、_SYSTEM 提及大盤)
- Modify: `jobs/evening.py`(多抓指數、傳 market、存 review.market)
- Test: `tests/test_review.py`(更新)

**Interfaces:**
- Produces:
  - `make_review(prediction, judged, indicators, stock_name, market=None, llm=generate_json) -> dict`(回傳含 `market` 鍵)
  - `format_review(stock_name, date, review, rate) -> str`(review 有 market 時多一行大盤)
  - `evening.run(today=None, llm=generate_json, fetch=fetch_daily, fetch_idx=fetch_index) -> dict|None`

- [ ] **Step 1: 更新測試(先失敗)**

在 `tests/test_review.py` 調整 make_review 與 evening 測試:

```python
def test_make_review_failure_calls_llm_with_market():
    judged = {"success": False, "results": {}}
    out = make_review({"reason": "r"}, judged, {"rsi14": 42}, "華邦電 (2344)",
                      market={"direction": "跌"},
                      llm=lambda s, u, sc: {"critique": "大盤拖累"})
    assert out["critique"] == "大盤拖累"
    assert out["market"]["direction"] == "跌"


def test_make_review_success_no_critique_keeps_market():
    judged = {"success": True, "results": {}}
    out = make_review({}, judged, {}, "華邦電 (2344)",
                      market={"direction": "漲"},
                      llm=lambda s, u, sc: {"critique": "x"})
    assert out["critique"] is None
    assert out["market"]["direction"] == "漲"


def test_format_review_shows_market():
    review = {
        "actual_close": 201.0, "prev_close": 203.0, "direction_actual": "跌",
        "results": {"direction": True, "hold_ma20": True, "hold_support1": False},
        "success": False, "critique": "大盤拖累",
        "market": {"direction": "跌", "pct": -0.7, "above_ma20": False},
    }
    s = format_review("華邦電 (2344)", "2026-06-30", review, 0.6)
    assert "大盤" in s and "復盤" in s
```

把原本 `test_evening_run_updates_record` 的 evening.run 呼叫補上 `fetch_idx`:

```python
def _idx_df(n=30):
    import pandas as pd
    closes = [float(45000 + i) for i in range(n)]
    idx = pd.date_range("2026-05-01", periods=n, freq="D")
    df = pd.DataFrame({"Open": closes, "High": closes, "Low": closes,
                       "Close": closes}, index=idx)
    df["MA20"] = df["Close"].rolling(20).mean()
    return df
```

並在該測試的 `evening.run(...)` 呼叫加上 `fetch_idx=lambda today=None: _idx_df()`,然後加一條斷言 `assert rec["review"]["market"]["direction"] == "漲"`。

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_review.py -v`
Expected: FAIL（make_review 不接受 market / evening.run 不接受 fetch_idx / 報告無大盤行）

- [ ] **Step 3: 改 core/review.py**

`_SYSTEM` 結尾加:`"檢討時請一併參考當日大盤(加權指數)走勢,例如大盤拖累或大盤帶動。"`

把 `make_review` 改成:

```python
def make_review(prediction, judged, indicators, stock_name,
                market=None, llm=generate_json):
    review = dict(judged)
    review["market"] = market
    if judged["success"]:
        review["critique"] = None
        return review
    user = (
        f"股票：{stock_name}\n"
        f"原預測：{json.dumps(prediction, ensure_ascii=False)}\n"
        f"實際結果：{json.dumps(judged, ensure_ascii=False)}\n"
        f"當日指標：{json.dumps(indicators, ensure_ascii=False)}\n"
        f"當日大盤：{json.dumps(market, ensure_ascii=False)}"
    )
    review["critique"] = llm(_SYSTEM, user, CRITIQUE_SCHEMA)["critique"]
    return review
```

`format_review` 在 critique 行之前(或結尾)加大盤行。把現有函式改成在組完 lines 後、append critique 前插入:

```python
def format_review(stock_name, date, review, rate):
    r = review["results"]

    def mark(ok):
        return "✅" if ok else "❌"

    chg = review["actual_close"] - review["prev_close"]
    lines = [
        f"🔍 {stock_name} 收盤復盤 {date}",
        f"今日收盤：{review['actual_close']:.2f}（{chg:+.2f}）",
        f"方向 實際{review['direction_actual']} {mark(r['direction'])}",
        f"站穩MA20 {mark(r['hold_ma20'])}　守住支撐1 {mark(r['hold_support1'])}",
        f"本日預測：{'命中 ✅' if review['success'] else '未中 ❌'}",
    ]
    mk = review.get("market") or {}
    if mk.get("direction"):
        pct = mk.get("pct")
        pct_txt = f" {pct:+.2f}%" if isinstance(pct, (int, float)) else ""
        lines.append(f"大盤：{mk['direction']}{pct_txt}")
    if rate is not None:
        lines.append(f"歷史方向命中率：{rate * 100:.0f}%")
    if review.get("critique"):
        lines.append(f"檢討：{review['critique']}")
    return "\n".join(lines)
```

- [ ] **Step 4: 改 jobs/evening.py**

import 區加 `fetch_index`(從 core.data)與 `from core.market import market_summary`。把 `run` 改為:

```python
def run(today=None, llm=generate_json, fetch=fetch_daily, fetch_idx=fetch_index):
    name, cfg = next(iter(STOCKS.items()))
    df = fetch(cfg["code"], today=today)
    if df.empty:
        tg.send("⚠️ 今日資料缺漏，無法復盤。")
        return None

    date = str(df.index[-1].date()) if today is None else str(today.date())
    records = load_history(HISTORY_PATH)
    rec = get_record(records, date)
    if rec is None or not rec.get("prediction"):
        tg.send(f"⚠️ 找不到 {date} 的開盤預測，略過復盤。")
        return None

    indicators = compute_indicators(df, cfg["supports"])
    s1 = cfg["supports"]["支撐1 (短期)"]
    judged = judge(
        rec["prediction"],
        today_close=indicators["close"],
        prev_close=indicators["prev_close"],
        today_ma20=indicators["ma20"],
        support1=s1,
    )
    market = market_summary(fetch_idx(today=today))
    review = make_review(rec["prediction"], judged, indicators, name,
                         market=market, llm=llm)
    rec["review"] = review
    records = upsert_record(records, rec)
    save_history(records, HISTORY_PATH)

    tg.send(format_review(name, date, review, hit_rate(records)))
    return rec
```

evening.py 結尾 dry-run 區的 make_review 呼叫補上 market(在算 judged 後):

```python
                ind = compute_indicators(df, cfg["supports"])
                s1 = cfg["supports"]["支撐1 (短期)"]
                judged = judge(rec["prediction"], ind["close"],
                               ind["prev_close"], ind["ma20"], s1)
                market = market_summary(fetch_index())
                review = make_review(rec["prediction"], judged, ind, name,
                                     market=market)
                print(format_review(name, date, review,
                                    hit_rate(load_history(HISTORY_PATH))))
```

(確保 dry-run 用到的 fetch_index / market_summary 已在檔案頂端匯入。)

- [ ] **Step 5: 跑測試確認通過 + 全套**

Run: `python -m pytest tests/test_review.py -v`
Expected: PASS
Run: `python -m pytest -q`
Expected: 全部 PASS

- [ ] **Step 6: Commit**

```bash
git add core/review.py jobs/evening.py tests/test_review.py
git commit -m "$(cat <<'EOF'
Feat: 大盤摘要餵進收盤復盤與檢討

- make_review 加 market，失敗檢討 prompt 帶當日大盤，存 review.market
- format_review 附大盤行；evening.run 多抓指數(可注入 fetch_idx)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: app.py 顯示大盤指標

**Files:**
- Modify: `app.py`

**Interfaces:**
- Consumes: `core.data.fetch_index`、`core.market.market_summary`。

- [ ] **Step 1: 改 app.py**

在 import 區把 `from core.data import STOCKS as CORE_STOCKS, fetch_daily` 改為:

```python
from core.data import STOCKS as CORE_STOCKS, fetch_daily, fetch_index
from core.market import market_summary
```

在現有 `@st.cache_data(ttl=3600) def load(code): ...` 之後新增一個快取的大盤載入函式:

```python
@st.cache_data(ttl=3600)
def load_index():
    return market_summary(fetch_index())
```

在 `st.title(...)` 之後、`choice = st.selectbox(...)` 之前,插入大盤列:

```python
mkt = load_index()
if mkt and mkt.get("close") is not None:
    pct = mkt.get("pct")
    delta = f"{pct:+.2f}%" if isinstance(pct, (int, float)) else None
    st.metric("加權指數(大盤)", f"{mkt['close']:.2f}", delta)
```

- [ ] **Step 2: 語法檢查 + 全套測試**

Run: `python -c "import ast; ast.parse(open('app.py', encoding='utf-8').read()); print('app ok')"`
Expected: `app ok`
Run: `python -m pytest -q`
Expected: 全部 PASS

- [ ] **Step 3: Commit**

```bash
git add app.py
git commit -m "$(cat <<'EOF'
Modify: app.py 顯示加權指數(大盤)指標

- 頂端加大盤收盤 + 漲跌%；快取 1 小時

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## 完成後
1. `python -m pytest -q` 全綠。
2. `python -m jobs.morning --dry-run` 看報告是否多了大盤行。
3. push 到 main(注意 token 已有 workflow 權限,可直接 push)。
