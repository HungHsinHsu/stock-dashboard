# 開盤預測 / 收盤復盤 / Telegram 報告 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓 stock-dashboard 每個交易日早上自動產生進場預測、收盤後自動復盤對答案並檢討,兩份報告透過 Telegram 推送;Streamlit 顯示歷史與命中率。

**Architecture:** GitHub Actions cron 跑兩支進入點腳本(morning/evening),共用 `core/` 模組;預測/復盤紀錄存成 repo 內 `history/predictions.json`,由 Actions commit 回 repo;Claude(Opus 4.8)負責產生預測理由與失敗檢討;Streamlit 讀紀錄檔顯示。

**Tech Stack:** Python 3.11、pandas、requests、anthropic SDK、plotly/streamlit(既有)、GitHub Actions、Telegram Bot API、TWSE 盤後 API。

## Global Constraints

- Python 版本:3.11(GitHub Actions 與 Streamlit Cloud 皆用 3.11)。
- Claude model id 一律 `claude-opus-4-8`,集中在 `core/llm.py` 的 `MODEL` 常數;呼叫用 `thinking={"type": "adaptive"}`、`output_config={"effort": "high", "format": {...json_schema...}}`、不可傳 `temperature`/`top_p`/`budget_tokens`(會 400)。
- 結構化輸出一律用 `output_config.format` 的 `json_schema`,schema 物件必須 `additionalProperties: false` 且列出 `required`。
- TWSE 請求一律帶 `HEADERS = {"User-Agent": "Mozilla/5.0 (stock-dashboard)"}`。
- 紀錄檔路徑常數 `HISTORY_PATH = "history/predictions.json"`,內容為 JSON 陣列,每筆含 `date`(YYYY-MM-DD 字串)、`stock`、`prediction`、`review`。
- 祕密一律從環境變數讀:`TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID`、`ANTHROPIC_API_KEY`;絕不寫進 repo。
- 股票設定沿用 `app.py` 的 `STOCKS` dict(目前華邦電 2344,支撐1=222、支撐3=142)。
- 測試用 pytest,核心邏輯(indicators/review/store)不可依賴網路或外部 API。
- Git commit message 遵守 team standard:`Feat/Modify/Fix/Test/Docs/Chore: SUBJECT`,結尾加 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。

---

### Task 1: 抽出 core/data.py(TWSE 抓資料 + 指標欄位)

**Files:**
- Create: `core/__init__.py`
- Create: `core/data.py`
- Test: `tests/test_data.py`
- Create: `requirements-dev.txt`

**Interfaces:**
- Produces:
  - `STOCKS: dict` — `{"華邦電 (2344)": {"code": "2344", "supports": {"支撐1 (短期)": 222, "支撐3 (長期)": 142}}}`
  - `fetch_daily(code: str, months: int = 6, today: datetime | None = None) -> pandas.DataFrame` — index 為 Date,欄位 Open/High/Low/Close/Volume,已排序去重並加 `MA20` 欄(`Close.rolling(20).mean()`);抓不到回空 DataFrame。
  - `parse_twse_json(j: dict) -> list[dict]` — 把 TWSE STOCK_DAY 回應的 `data` 轉成列(欄位同上,不含 MA20);非 OK 或無 data 回 `[]`。純函式,給測試用。

- [ ] **Step 1: 建 dev 相依檔**

Create `requirements-dev.txt`:

```
pytest>=8.0
```

- [ ] **Step 2: 寫失敗測試(parse_twse_json 純函式)**

Create `tests/test_data.py`:

```python
from core.data import parse_twse_json, STOCKS


def test_parse_twse_json_ok():
    j = {
        "stat": "OK",
        "data": [
            ["115/06/27", "1,000", "2,000", "200.0", "210.0", "199.0", "205.0", "+1", "50"],
        ],
    }
    rows = parse_twse_json(j)
    assert len(rows) == 1
    r = rows[0]
    assert r["Open"] == 200.0 and r["High"] == 210.0
    assert r["Low"] == 199.0 and r["Close"] == 205.0
    assert r["Volume"] == 1000.0
    assert str(r["Date"].date()) == "2026-06-27"  # 民國115 -> 西元2026


def test_parse_twse_json_not_ok():
    assert parse_twse_json({"stat": "很抱歉，沒有符合條件的資料!"}) == []
    assert parse_twse_json({"stat": "OK"}) == []


def test_stocks_shape():
    assert "華邦電 (2344)" in STOCKS
    assert STOCKS["華邦電 (2344)"]["code"] == "2344"
```

- [ ] **Step 3: 跑測試確認失敗**

Run: `python -m pytest tests/test_data.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'core'`）

- [ ] **Step 4: 實作 core/__init__.py 與 core/data.py**

Create `core/__init__.py`:

```python
```

Create `core/data.py`:

```python
import pandas as pd
import requests
from datetime import datetime
from dateutil.relativedelta import relativedelta

HEADERS = {"User-Agent": "Mozilla/5.0 (stock-dashboard)"}

STOCKS = {
    "華邦電 (2344)": {
        "code": "2344",
        "supports": {"支撐1 (短期)": 222, "支撐3 (長期)": 142},
    },
}


def parse_twse_json(j):
    """把 TWSE STOCK_DAY 回應轉成 list[dict]；非 OK 或無 data 回 []。"""
    if j.get("stat") != "OK" or "data" not in j:
        return []
    rows = []
    for row in j["data"]:
        # row: 日期, 成交股數, 成交金額, 開盤, 最高, 最低, 收盤, 漲跌, 成交筆數
        try:
            parts = row[0].split("/")
            y = int(parts[0]) + 1911
            date = pd.Timestamp(f"{y}-{parts[1]}-{parts[2]}")
            rows.append({
                "Date": date,
                "Open": float(row[3].replace(",", "")),
                "High": float(row[4].replace(",", "")),
                "Low": float(row[5].replace(",", "")),
                "Close": float(row[6].replace(",", "")),
                "Volume": float(row[1].replace(",", "")),
            })
        except (ValueError, IndexError):
            continue
    return rows


def fetch_daily(code, months=6, today=None):
    """抓最近 months 個月日線；回 DataFrame(index=Date, 含 MA20)；抓不到回空。"""
    today = today or datetime.today()
    frames = []
    for i in range(months):
        d = today - relativedelta(months=i)
        ym = d.strftime("%Y%m01")
        url = (
            "https://www.twse.com.tw/exchangeReport/STOCK_DAY"
            f"?response=json&date={ym}&stockNo={code}"
        )
        try:
            r = requests.get(url, headers=HEADERS, timeout=10)
            frames.extend(parse_twse_json(r.json()))
        except Exception:
            continue
    if not frames:
        return pd.DataFrame()
    df = (
        pd.DataFrame(frames)
        .drop_duplicates("Date")
        .sort_values("Date")
        .set_index("Date")
    )
    df["MA20"] = df["Close"].rolling(20).mean()
    return df
```

- [ ] **Step 5: 跑測試確認通過**

Run: `python -m pytest tests/test_data.py -v`
Expected: PASS（3 passed）

- [ ] **Step 6: Commit**

```bash
git add core/__init__.py core/data.py tests/test_data.py requirements-dev.txt
git commit -m "$(cat <<'EOF'
Refactor: 抽出 core/data.py 共用 TWSE 抓資料邏輯

- parse_twse_json 純函式 + fetch_daily(含 MA20)
- 加 tests/test_data.py 與 requirements-dev.txt

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: core/indicators.py(技術指標計算)

**Files:**
- Create: `core/indicators.py`
- Test: `tests/test_indicators.py`

**Interfaces:**
- Consumes: 一個 DataFrame(index=Date,欄位 Open/High/Low/Close/Volume/MA20),如 `fetch_daily` 的輸出。
- Produces: `compute_indicators(df, supports: dict) -> dict` — 回傳所有值皆為 Python float/None 的 dict:
  - `close`(最新收盤)、`prev_close`(前一日收盤,不足回 None)
  - `ma5`、`ma20`、`ma60`(不足回 None)
  - `rsi14`(0~100,不足回 None)
  - `vol`(最新量)、`vol_ratio`(最新量 / 20 日均量,不足回 None)
  - `dist_support1_pct`、`dist_support3_pct`(收盤距各支撐位的百分比 = (close-支撐)/支撐*100)
  - 鍵名固定,morning/review/report 都靠這份 dict。

- [ ] **Step 1: 寫失敗測試**

Create `tests/test_indicators.py`:

```python
import pandas as pd
from core.indicators import compute_indicators, rsi


def _df(closes):
    idx = pd.date_range("2026-01-01", periods=len(closes), freq="D")
    return pd.DataFrame(
        {
            "Open": closes,
            "High": [c + 1 for c in closes],
            "Low": [c - 1 for c in closes],
            "Close": closes,
            "Volume": [1000.0] * len(closes),
        },
        index=idx,
    )


def test_rsi_all_up_is_100():
    s = pd.Series([float(i) for i in range(1, 30)])
    assert round(rsi(s, 14).iloc[-1], 1) == 100.0


def test_compute_indicators_basic():
    df = _df([float(100 + i) for i in range(60)])  # 穩定上漲 60 天
    df["MA20"] = df["Close"].rolling(20).mean()
    ind = compute_indicators(df, {"支撐1 (短期)": 100, "支撐3 (長期)": 90})
    assert ind["close"] == 159.0
    assert ind["prev_close"] == 158.0
    assert ind["ma5"] is not None and ind["ma60"] is not None
    assert 0 <= ind["rsi14"] <= 100
    assert ind["dist_support1_pct"] == round((159.0 - 100) / 100 * 100, 2)


def test_compute_indicators_short_history():
    df = _df([100.0])
    df["MA20"] = df["Close"].rolling(20).mean()
    ind = compute_indicators(df, {"支撐1 (短期)": 100, "支撐3 (長期)": 90})
    assert ind["prev_close"] is None
    assert ind["ma20"] is None
    assert ind["rsi14"] is None
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_indicators.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'core.indicators'`）

- [ ] **Step 3: 實作 core/indicators.py**

Create `core/indicators.py`:

```python
import pandas as pd


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss
    out = 100 - (100 / (1 + rs))
    out = out.where(loss != 0, 100.0)  # 全漲：loss=0 -> RSI=100
    return out


def _last(series):
    if series is None or len(series) == 0:
        return None
    v = series.iloc[-1]
    return None if pd.isna(v) else float(v)


def compute_indicators(df: pd.DataFrame, supports: dict) -> dict:
    close = df["Close"]
    last_close = _last(close)
    prev_close = float(close.iloc[-2]) if len(close) >= 2 else None
    ma20 = _last(df["MA20"]) if "MA20" in df else _last(close.rolling(20).mean())
    vol = _last(df["Volume"])
    vol_ma20 = _last(df["Volume"].rolling(20).mean())
    s1 = supports.get("支撐1 (短期)")
    s3 = supports.get("支撐3 (長期)")

    def dist(level):
        if last_close is None or not level:
            return None
        return round((last_close - level) / level * 100, 2)

    return {
        "close": last_close,
        "prev_close": prev_close,
        "ma5": _last(close.rolling(5).mean()),
        "ma20": ma20,
        "ma60": _last(close.rolling(60).mean()),
        "rsi14": _last(rsi(close, 14)),
        "vol": vol,
        "vol_ratio": round(vol / vol_ma20, 2) if vol and vol_ma20 else None,
        "dist_support1_pct": dist(s1),
        "dist_support3_pct": dist(s3),
    }
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_indicators.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add core/indicators.py tests/test_indicators.py
git commit -m "$(cat <<'EOF'
Feat: 加 core/indicators.py 技術指標計算

- MA5/20/60、RSI14、量比、距支撐位百分比
- 資料不足回 None；附單元測試

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: core/store.py(紀錄讀寫)

**Files:**
- Create: `core/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Produces:
  - `HISTORY_PATH = "history/predictions.json"`
  - `load_history(path=HISTORY_PATH) -> list[dict]` — 檔案不存在回 `[]`。
  - `save_history(records, path=HISTORY_PATH) -> None` — 寫入(自動建目錄,UTF-8,`ensure_ascii=False`,`indent=2`)。
  - `get_record(records, date: str) -> dict | None` — 依 `date` 找。
  - `upsert_record(records, record) -> list[dict]` — 依 `date` 覆蓋或新增,回新 list(依 date 排序)。

- [ ] **Step 1: 寫失敗測試**

Create `tests/test_store.py`:

```python
from core.store import load_history, save_history, get_record, upsert_record


def test_load_missing_returns_empty(tmp_path):
    assert load_history(tmp_path / "nope.json") == []


def test_save_then_load_roundtrip(tmp_path):
    p = tmp_path / "h.json"
    recs = [{"date": "2026-06-29", "stock": "2344"}]
    save_history(recs, p)
    assert load_history(p) == recs


def test_upsert_overwrites_same_date():
    recs = [{"date": "2026-06-29", "stock": "2344", "v": 1}]
    out = upsert_record(recs, {"date": "2026-06-29", "stock": "2344", "v": 2})
    assert len(out) == 1 and out[0]["v"] == 2


def test_upsert_adds_and_sorts():
    out = upsert_record([{"date": "2026-06-29"}], {"date": "2026-06-28"})
    assert [r["date"] for r in out] == ["2026-06-28", "2026-06-29"]


def test_get_record():
    recs = [{"date": "2026-06-29", "stock": "2344"}]
    assert get_record(recs, "2026-06-29")["stock"] == "2344"
    assert get_record(recs, "2026-01-01") is None
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_store.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'core.store'`）

- [ ] **Step 3: 實作 core/store.py**

Create `core/store.py`:

```python
import json
import os

HISTORY_PATH = "history/predictions.json"


def load_history(path=HISTORY_PATH):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_history(records, path=HISTORY_PATH):
    d = os.path.dirname(str(path))
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


def get_record(records, date):
    return next((r for r in records if r.get("date") == date), None)


def upsert_record(records, record):
    out = [r for r in records if r.get("date") != record["date"]]
    out.append(record)
    return sorted(out, key=lambda r: r["date"])
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_store.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: Commit**

```bash
git add core/store.py tests/test_store.py
git commit -m "$(cat <<'EOF'
Feat: 加 core/store.py 預測紀錄讀寫

- load/save/get/upsert，依 date 去重排序，UTF-8 不轉義

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: core/telegram.py(推送)

**Files:**
- Create: `core/telegram.py`
- Test: `tests/test_telegram.py`

**Interfaces:**
- Produces: `send(text: str, token: str | None = None, chat_id: str | None = None) -> bool` — 用 Telegram Bot API `sendMessage` 送出;`token`/`chat_id` 預設從環境變數 `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` 讀;缺金鑰回 False 不丟例外;HTTP 成功回 True、失敗回 False。

- [ ] **Step 1: 寫失敗測試(用 monkeypatch 攔截 requests.post)**

Create `tests/test_telegram.py`:

```python
import core.telegram as tg


class _Resp:
    status_code = 200


def test_send_missing_token_returns_false(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert tg.send("hi") is False


def test_send_posts_with_env(monkeypatch):
    calls = {}

    def fake_post(url, data=None, timeout=None):
        calls["url"] = url
        calls["data"] = data
        return _Resp()

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TOK")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.setattr(tg.requests, "post", fake_post)

    assert tg.send("hello") is True
    assert "TOK" in calls["url"]
    assert calls["data"]["chat_id"] == "123"
    assert calls["data"]["text"] == "hello"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_telegram.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'core.telegram'`）

- [ ] **Step 3: 實作 core/telegram.py**

Create `core/telegram.py`:

```python
import os
import requests


def send(text, token=None, chat_id=None):
    token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(
            url, data={"chat_id": chat_id, "text": text}, timeout=10
        )
        return r.status_code == 200
    except Exception:
        return False
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_telegram.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: Commit**

```bash
git add core/telegram.py tests/test_telegram.py
git commit -m "$(cat <<'EOF'
Feat: 加 core/telegram.py 推送訊息

- sendMessage，金鑰讀環境變數，缺金鑰/失敗回 False 不丟例外

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: core/llm.py(Claude 包裝,結構化輸出)

**Files:**
- Create: `core/llm.py`
- Test: `tests/test_llm.py`
- Modify: `requirements.txt`（追加 `anthropic>=0.92`）

**Interfaces:**
- Produces:
  - `MODEL = "claude-opus-4-8"`
  - `generate_json(system: str, user: str, schema: dict, client=None) -> dict` — 呼叫 Claude,強制結構化輸出,回 parsed dict;Claude 回 refusal 或解析失敗時丟 `LLMError`。`client` 可注入(測試用 fake)。
  - `class LLMError(Exception)`。

- [ ] **Step 1: 追加相依**

Edit `requirements.txt`，在最後加一行:

```
anthropic>=0.92
```

- [ ] **Step 2: 寫失敗測試(注入 fake client,不打網路)**

Create `tests/test_llm.py`:

```python
import json
import pytest
from core.llm import generate_json, LLMError, MODEL


class _Block:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _Resp:
    def __init__(self, text, stop_reason="end_turn"):
        self.content = [_Block(text)]
        self.stop_reason = stop_reason


class _Messages:
    def __init__(self, resp):
        self._resp = resp
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self._resp


class _Client:
    def __init__(self, resp):
        self.messages = _Messages(resp)


SCHEMA = {
    "type": "object",
    "properties": {"signal": {"type": "string"}},
    "required": ["signal"],
    "additionalProperties": False,
}


def test_generate_json_parses():
    client = _Client(_Resp(json.dumps({"signal": "觀望"})))
    out = generate_json("sys", "user", SCHEMA, client=client)
    assert out == {"signal": "觀望"}
    assert client.messages.kwargs["model"] == MODEL
    assert "temperature" not in client.messages.kwargs


def test_generate_json_refusal_raises():
    client = _Client(_Resp("", stop_reason="refusal"))
    with pytest.raises(LLMError):
        generate_json("sys", "user", SCHEMA, client=client)
```

- [ ] **Step 3: 跑測試確認失敗**

Run: `python -m pytest tests/test_llm.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'core.llm'`）

- [ ] **Step 4: 實作 core/llm.py**

Create `core/llm.py`:

```python
import json

MODEL = "claude-opus-4-8"


class LLMError(Exception):
    pass


def _default_client():
    import anthropic
    return anthropic.Anthropic()


def generate_json(system, user, schema, client=None):
    """呼叫 Claude 並強制結構化 JSON 輸出，回 parsed dict。"""
    client = client or _default_client()
    resp = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        thinking={"type": "adaptive"},
        system=system,
        output_config={
            "effort": "high",
            "format": {"type": "json_schema", "schema": schema},
        },
        messages=[{"role": "user", "content": user}],
    )
    if getattr(resp, "stop_reason", None) == "refusal":
        raise LLMError("Claude refused the request")
    text = next(
        (b.text for b in resp.content if getattr(b, "type", None) == "text"),
        None,
    )
    if not text:
        raise LLMError("No text content in response")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise LLMError(f"Invalid JSON: {e}") from e
```

- [ ] **Step 5: 跑測試確認通過**

Run: `python -m pytest tests/test_llm.py -v`
Expected: PASS（2 passed）

- [ ] **Step 6: Commit**

```bash
git add core/llm.py tests/test_llm.py requirements.txt
git commit -m "$(cat <<'EOF'
Feat: 加 core/llm.py 包裝 Claude 結構化輸出

- MODEL=claude-opus-4-8、adaptive thinking、output_config.format
- generate_json 回 parsed dict，refusal/解析失敗丟 LLMError
- requirements.txt 追加 anthropic

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: core/predict.py + jobs/morning.py(開盤預測)

**Files:**
- Create: `core/predict.py`
- Create: `jobs/__init__.py`
- Create: `jobs/morning.py`
- Test: `tests/test_predict.py`

**Interfaces:**
- Consumes: `compute_indicators` 的 dict、`generate_json`(可注入)。
- Produces:
  - `core/predict.py`:
    - `PREDICTION_SCHEMA: dict`(json_schema:`signal`∈進場/觀望/避開、`direction`∈漲/跌、`hold_ma20`bool、`hold_support1`bool、`reason`str;全 required、`additionalProperties:false`)
    - `make_prediction(indicators: dict, stock_name: str, llm=generate_json) -> dict` — 組 prompt、呼叫 llm、回含上述欄位 + `indicators` 的 dict。
    - `format_prediction(stock_name, date, prediction) -> str`(Telegram 文字)。
  - `jobs/morning.py`:
    - `run(today=None, llm=generate_json, fetch=fetch_daily, notify=tg.send) -> dict | None` — 抓資料→算指標→預測→upsert 存檔→推送;非交易日/抓不到資料回 None 並推「資料缺漏」。
    - `if __name__ == "__main__": run()`。

- [ ] **Step 1: 寫失敗測試(注入 fake llm 與 fake fetch)**

Create `tests/test_predict.py`:

```python
import pandas as pd
from core.predict import make_prediction, format_prediction, PREDICTION_SCHEMA
from core.store import HISTORY_PATH
import jobs.morning as morning


def _fake_llm(system, user, schema, client=None):
    assert schema is PREDICTION_SCHEMA
    return {
        "signal": "觀望",
        "direction": "跌",
        "hold_ma20": False,
        "hold_support1": False,
        "reason": "量縮跌破MA20",
    }


def test_make_prediction_includes_indicators_and_fields():
    ind = {"close": 203.0, "ma20": 186.5, "rsi14": 42.0}
    out = make_prediction(ind, "華邦電 (2344)", llm=_fake_llm)
    assert out["signal"] == "觀望"
    assert out["direction"] == "跌"
    assert out["indicators"]["close"] == 203.0


def test_format_prediction_contains_key_text():
    pred = {
        "signal": "觀望", "direction": "跌", "hold_ma20": False,
        "hold_support1": False, "reason": "量縮",
        "indicators": {"close": 203.0, "ma20": 186.5},
    }
    s = format_prediction("華邦電 (2344)", "2026-06-30", pred)
    assert "華邦電 (2344)" in s and "觀望" in s and "2026-06-30" in s


def _df_with_ma20(n=30):
    closes = [float(100 + i) for i in range(n)]
    idx = pd.date_range("2026-05-01", periods=n, freq="D")
    df = pd.DataFrame(
        {"Open": closes, "High": closes, "Low": closes,
         "Close": closes, "Volume": [1000.0] * n}, index=idx)
    df["MA20"] = df["Close"].rolling(20).mean()
    return df


def test_morning_run_writes_and_notifies(tmp_path, monkeypatch):
    monkeypatch.setattr(morning, "HISTORY_PATH", str(tmp_path / "h.json"))
    sent = {}
    monkeypatch.setattr(morning, "tg", type("T", (), {
        "send": staticmethod(lambda text: sent.setdefault("text", text) or True)
    }))
    rec = morning.run(
        today=pd.Timestamp("2026-06-30"),
        llm=_fake_llm,
        fetch=lambda code, today=None: _df_with_ma20(),
    )
    assert rec is not None
    assert rec["prediction"]["signal"] == "觀望"
    assert "觀望" in sent["text"]


def test_morning_run_empty_data_notifies_and_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(morning, "HISTORY_PATH", str(tmp_path / "h.json"))
    sent = {}
    monkeypatch.setattr(morning, "tg", type("T", (), {
        "send": staticmethod(lambda text: sent.setdefault("text", text) or True)
    }))
    out = morning.run(
        today=pd.Timestamp("2026-06-30"),
        llm=_fake_llm,
        fetch=lambda code, today=None: pd.DataFrame(),
    )
    assert out is None
    assert "缺漏" in sent["text"]
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_predict.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'core.predict'`）

- [ ] **Step 3: 實作 core/predict.py**

Create `core/predict.py`:

```python
import json
from core.llm import generate_json

PREDICTION_SCHEMA = {
    "type": "object",
    "properties": {
        "signal": {"type": "string", "enum": ["進場", "觀望", "避開"]},
        "direction": {"type": "string", "enum": ["漲", "跌"]},
        "hold_ma20": {"type": "boolean"},
        "hold_support1": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["signal", "direction", "hold_ma20", "hold_support1", "reason"],
    "additionalProperties": False,
}

_SYSTEM = (
    "你是台股技術分析助手。根據提供的技術指標，對指定股票做出當日預測。"
    "可驗證宣告以『今日收盤 vs 昨日收盤』為準。"
    "務必同時給：進場訊號(進場/觀望/避開)、方向(漲/跌)、是否站穩MA20、"
    "是否守住支撐1，以及白話理由。理由要引用具體指標。"
)


def make_prediction(indicators, stock_name, llm=generate_json):
    user = (
        f"股票：{stock_name}\n"
        f"技術指標(到昨日收盤為止)：\n{json.dumps(indicators, ensure_ascii=False)}"
    )
    pred = llm(_SYSTEM, user, PREDICTION_SCHEMA)
    pred["indicators"] = indicators
    return pred


def format_prediction(stock_name, date, prediction):
    ind = prediction.get("indicators", {})
    ma20 = ind.get("ma20")
    ma20_txt = f"{ma20:.1f}" if isinstance(ma20, (int, float)) else "—"
    return (
        f"📈 {stock_name} 開盤預測 {date}\n"
        f"進場訊號：{prediction['signal']}\n"
        f"方向：預期{prediction['direction']}\n"
        f"站穩MA20：{'是' if prediction['hold_ma20'] else '否'}(MA20={ma20_txt})\n"
        f"守住支撐1：{'是' if prediction['hold_support1'] else '否'}\n"
        f"理由：{prediction['reason']}"
    )
```

- [ ] **Step 4: 實作 jobs/__init__.py 與 jobs/morning.py**

Create `jobs/__init__.py`:

```python
```

Create `jobs/morning.py`:

```python
from core.data import fetch_daily, STOCKS
from core.indicators import compute_indicators
from core.predict import make_prediction, format_prediction, PREDICTION_SCHEMA  # noqa: F401
from core.llm import generate_json
from core.store import load_history, save_history, upsert_record, HISTORY_PATH
import core.telegram as tg


def run(today=None, llm=generate_json, fetch=fetch_daily, notify=None):
    name, cfg = next(iter(STOCKS.items()))
    df = fetch(cfg["code"], today=today)

    if df.empty:
        tg.send("⚠️ 今日資料缺漏，已跳過開盤預測。")
        return None

    date = str(df.index[-1].date()) if today is None else str(today.date())
    indicators = compute_indicators(df, cfg["supports"])
    prediction = make_prediction(indicators, name, llm=llm)

    record = {
        "date": date,
        "stock": cfg["code"],
        "prediction": prediction,
        "review": None,
    }
    records = upsert_record(load_history(HISTORY_PATH), record)
    save_history(records, HISTORY_PATH)

    tg.send(format_prediction(name, date, prediction))
    return record


if __name__ == "__main__":
    run()
```

- [ ] **Step 5: 跑測試確認通過**

Run: `python -m pytest tests/test_predict.py -v`
Expected: PASS（4 passed）

- [ ] **Step 6: Commit**

```bash
git add core/predict.py jobs/__init__.py jobs/morning.py tests/test_predict.py
git commit -m "$(cat <<'EOF'
Feat: 加開盤預測 core/predict.py 與 jobs/morning.py

- PREDICTION_SCHEMA + make_prediction + format_prediction
- morning.run：抓資料→算指標→Claude預測→存檔→Telegram
- 抓不到資料推「資料缺漏」並回 None

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: core/review.py + jobs/evening.py(收盤復盤)

**Files:**
- Create: `core/review.py`
- Create: `jobs/evening.py`
- Test: `tests/test_review.py`

**Interfaces:**
- Consumes: 當日預測 record、今日 DataFrame、`compute_indicators`、`generate_json`(可注入)。
- Produces:
  - `core/review.py`:
    - `CRITIQUE_SCHEMA: dict`(`critique`str;required、`additionalProperties:false`)
    - `judge(prediction: dict, today_close: float, prev_close: float, today_ma20: float | None, support1: float) -> dict` — 純函式,逐項對答案,回 `{"actual_close","prev_close","direction_actual","results":{"direction","hold_ma20","hold_support1"},"success"}`;`success` = 三項皆 True。
    - `hit_rate(records: list[dict]) -> float | None` — 已復盤紀錄的方向命中率(0~1),無資料回 None。
    - `make_review(prediction, judged, indicators, stock_name, llm=generate_json) -> dict` — judged 失敗時呼叫 llm 產 `critique`,成功則 `critique=None`;回 judged + `critique`。
    - `format_review(stock_name, date, review, rate) -> str`。
  - `jobs/evening.py`:
    - `run(today=None, llm=generate_json, fetch=fetch_daily) -> dict | None` — 找當日預測→抓今日→judge→make_review→更新 record→推送;找不到當日預測或抓不到資料回 None 並推說明。
    - `if __name__ == "__main__": run()`。

- [ ] **Step 1: 寫失敗測試**

Create `tests/test_review.py`:

```python
import pandas as pd
from core.review import judge, hit_rate, make_review, format_review, CRITIQUE_SCHEMA
import jobs.evening as evening
from core.store import save_history


def test_judge_all_hit():
    pred = {"direction": "跌", "hold_ma20": False, "hold_support1": False,
            "signal": "觀望"}
    j = judge(pred, today_close=201.0, prev_close=203.0,
              today_ma20=205.0, support1=222)
    assert j["direction_actual"] == "跌"
    assert j["results"]["direction"] is True
    assert j["results"]["hold_ma20"] is False  # 201 < 205 -> 沒站穩，預測否 -> 命中
    # 命中判定：實際是否站穩 == 預測
    assert j["success"] is True


def test_judge_direction_miss():
    pred = {"direction": "漲", "hold_ma20": True, "hold_support1": True}
    j = judge(pred, today_close=201.0, prev_close=203.0,
              today_ma20=190.0, support1=222)
    assert j["results"]["direction"] is False
    assert j["success"] is False


def test_hit_rate():
    recs = [
        {"review": {"results": {"direction": True}}},
        {"review": {"results": {"direction": False}}},
        {"review": None},
    ]
    assert hit_rate(recs) == 0.5
    assert hit_rate([{"review": None}]) is None


def test_make_review_success_no_critique():
    judged = {"success": True, "results": {}}
    out = make_review({}, judged, {}, "華邦電 (2344)",
                      llm=lambda s, u, sc: {"critique": "x"})
    assert out["critique"] is None


def test_make_review_failure_calls_llm():
    judged = {"success": False, "results": {}}
    out = make_review({"reason": "r"}, judged, {"rsi14": 42}, "華邦電 (2344)",
                      llm=lambda s, u, sc: {"critique": "量背離"})
    assert out["critique"] == "量背離"


def test_evening_run_updates_record(tmp_path, monkeypatch):
    hp = str(tmp_path / "h.json")
    save_history([{
        "date": "2026-06-30", "stock": "2344",
        "prediction": {"direction": "跌", "hold_ma20": False,
                       "hold_support1": False, "signal": "觀望",
                       "indicators": {}},
        "review": None,
    }], hp)
    monkeypatch.setattr(evening, "HISTORY_PATH", hp)
    sent = {}
    monkeypatch.setattr(evening, "tg", type("T", (), {
        "send": staticmethod(lambda text: sent.setdefault("t", text) or True)}))

    idx = pd.date_range("2026-05-01", periods=30, freq="D")
    closes = [203.0] * 29 + [201.0]
    df = pd.DataFrame({"Open": closes, "High": closes, "Low": closes,
                       "Close": closes, "Volume": [1000.0] * 30}, index=idx)
    df.index = list(idx[:-1]) + [pd.Timestamp("2026-06-30")]
    df["MA20"] = df["Close"].rolling(20).mean()

    rec = evening.run(today=pd.Timestamp("2026-06-30"),
                      llm=lambda s, u, sc: {"critique": "x"},
                      fetch=lambda code, today=None: df)
    assert rec is not None
    assert rec["review"]["actual_close"] == 201.0
    assert "復盤" in sent["t"]
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_review.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'core.review'`）

- [ ] **Step 3: 實作 core/review.py**

Create `core/review.py`:

```python
import json
from core.llm import generate_json

CRITIQUE_SCHEMA = {
    "type": "object",
    "properties": {"critique": {"type": "string"}},
    "required": ["critique"],
    "additionalProperties": False,
}

_SYSTEM = (
    "你是台股技術分析助手。早盤的預測在收盤後被驗證為失敗。"
    "請根據當天的技術指標，分析『為什麼預測會錯』，"
    "例如量價背離、假突破、大盤拖累等，給出具體檢討。"
)


def judge(prediction, today_close, prev_close, today_ma20, support1):
    direction_actual = "漲" if today_close >= prev_close else "跌"
    hold_ma20_actual = today_ma20 is not None and today_close >= today_ma20
    hold_s1_actual = today_close >= support1
    results = {
        "direction": prediction.get("direction") == direction_actual,
        "hold_ma20": prediction.get("hold_ma20") == hold_ma20_actual,
        "hold_support1": prediction.get("hold_support1") == hold_s1_actual,
    }
    return {
        "actual_close": today_close,
        "prev_close": prev_close,
        "direction_actual": direction_actual,
        "results": results,
        "success": all(results.values()),
    }


def hit_rate(records):
    vals = [
        r["review"]["results"]["direction"]
        for r in records
        if r.get("review") and "results" in r["review"]
    ]
    if not vals:
        return None
    return round(sum(1 for v in vals if v) / len(vals), 2)


def make_review(prediction, judged, indicators, stock_name, llm=generate_json):
    review = dict(judged)
    if judged["success"]:
        review["critique"] = None
        return review
    user = (
        f"股票：{stock_name}\n"
        f"原預測：{json.dumps(prediction, ensure_ascii=False)}\n"
        f"實際結果：{json.dumps(judged, ensure_ascii=False)}\n"
        f"當日指標：{json.dumps(indicators, ensure_ascii=False)}"
    )
    review["critique"] = llm(_SYSTEM, user, CRITIQUE_SCHEMA)["critique"]
    return review


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
    if rate is not None:
        lines.append(f"歷史方向命中率：{rate * 100:.0f}%")
    if review.get("critique"):
        lines.append(f"檢討：{review['critique']}")
    return "\n".join(lines)
```

- [ ] **Step 4: 實作 jobs/evening.py**

Create `jobs/evening.py`:

```python
from core.data import fetch_daily, STOCKS
from core.indicators import compute_indicators
from core.review import judge, make_review, format_review, hit_rate
from core.llm import generate_json
from core.store import load_history, save_history, upsert_record, get_record, HISTORY_PATH
import core.telegram as tg


def run(today=None, llm=generate_json, fetch=fetch_daily):
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
    review = make_review(rec["prediction"], judged, indicators, name, llm=llm)
    rec["review"] = review
    records = upsert_record(records, rec)
    save_history(records, HISTORY_PATH)

    tg.send(format_review(name, date, review, hit_rate(records)))
    return rec


if __name__ == "__main__":
    run()
```

- [ ] **Step 5: 跑測試確認通過**

Run: `python -m pytest tests/test_review.py -v`
Expected: PASS（6 passed）

- [ ] **Step 6: 跑全部測試**

Run: `python -m pytest -v`
Expected: PASS（全部）

- [ ] **Step 7: Commit**

```bash
git add core/review.py jobs/evening.py tests/test_review.py
git commit -m "$(cat <<'EOF'
Feat: 加收盤復盤 core/review.py 與 jobs/evening.py

- judge 逐項對答案、hit_rate 命中率、失敗時 Claude 檢討
- evening.run：找當日預測→對答案→更新→Telegram

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: GitHub Actions 排程

**Files:**
- Create: `.github/workflows/predict.yml`

**Interfaces:**
- Consumes: `jobs/morning.py`、`jobs/evening.py`、repo secrets。
- 行為:每交易日早上跑 morning、下午跑 evening,各自 commit `history/predictions.json` 回 repo。

> **排程時間換算**:台灣 = UTC+8。早 08:30 → UTC 00:30;晚 15:30 → UTC 07:30。cron 用 UTC,且只在週一~週五跑。

- [ ] **Step 1: 建 workflow**

Create `.github/workflows/predict.yml`:

```yaml
name: stock-predict

on:
  schedule:
    - cron: "30 0 * * 1-5"   # 台灣 08:30 開盤預測
    - cron: "30 7 * * 1-5"   # 台灣 15:30 收盤復盤
  workflow_dispatch:
    inputs:
      job:
        description: "morning or evening"
        required: true
        default: "morning"

permissions:
  contents: write

jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -r requirements.txt
      - name: Decide job
        id: which
        run: |
          if [ "${{ github.event_name }}" = "workflow_dispatch" ]; then
            echo "job=${{ github.event.inputs.job }}" >> "$GITHUB_OUTPUT"
          elif [ "${{ github.event.schedule }}" = "30 0 * * 1-5" ]; then
            echo "job=morning" >> "$GITHUB_OUTPUT"
          else
            echo "job=evening" >> "$GITHUB_OUTPUT"
          fi
      - name: Run job
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        run: python -m jobs.${{ steps.which.outputs.job }}
      - name: Commit history
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add history/predictions.json
          if git diff --staged --quiet; then
            echo "No changes"
          else
            git commit -m "Chore: 更新預測/復盤紀錄 [skip ci]"
            git push
          fi
```

- [ ] **Step 2: 本機驗證 YAML 可解析**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/predict.yml', encoding='utf-8')); print('yaml ok')"`
Expected: 印出 `yaml ok`（若無 pyyaml 則 `pip install pyyaml` 後再跑）

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/predict.yml
git commit -m "$(cat <<'EOF'
Chore: 加 GitHub Actions 排程(開盤/收盤)

- 早 08:30 morning、晚 15:30 evening(台灣時間，週一至週五)
- 跑完 commit history/predictions.json 回 repo
- workflow_dispatch 可手動觸發

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Streamlit 新增「預測歷史 / 命中率」頁籤

**Files:**
- Modify: `app.py`

**Interfaces:**
- Consumes: `core.store.load_history`、`core.review.hit_rate`、`core.data.STOCKS`。
- 行為:在現有圖表下方用 `st.tabs` 加「預測歷史」分頁,顯示命中率與每日預測 vs 結果表格。

- [ ] **Step 1: 改 app.py 改用 core.data.STOCKS 並加歷史頁籤**

在 `app.py` 頂部 import 區(`from dateutil.relativedelta import relativedelta` 之後)加:

```python
from core.data import STOCKS as CORE_STOCKS, fetch_daily
from core.store import load_history
from core.review import hit_rate
```

把 `app.py` 既有的 `STOCKS = {...}` 整段替換成:

```python
STOCKS = CORE_STOCKS
```

並刪掉 `app.py` 內原本的 `@st.cache_data ... def load(code): ...` 函式,改為:

```python
@st.cache_data(ttl=3600)
def load(code):
    return fetch_daily(code)
```

（`fetch_daily` 已回傳含 MA20 的 DataFrame,行為等價。）

- [ ] **Step 2: 在 app.py 結尾 `st.caption(...)` 之後加歷史頁籤**

在檔案最後加:

```python
st.divider()
tab_hist, = st.tabs(["📒 預測歷史"])
with tab_hist:
    records = [r for r in load_history() if r.get("stock") == cfg["code"]]
    if not records:
        st.info("尚無預測紀錄。GitHub Actions 跑過開盤/收盤後會出現。")
    else:
        rate = hit_rate(records)
        if rate is not None:
            st.metric("方向命中率", f"{rate * 100:.0f}%")
        rows = []
        for r in sorted(records, key=lambda x: x["date"], reverse=True):
            p = r.get("prediction") or {}
            rv = r.get("review") or {}
            res = rv.get("results") or {}
            rows.append({
                "日期": r["date"],
                "訊號": p.get("signal", "—"),
                "預測方向": p.get("direction", "—"),
                "實際方向": rv.get("direction_actual", "—"),
                "方向命中": "✅" if res.get("direction") else (
                    "❌" if rv else "—"),
                "收盤": rv.get("actual_close", "—"),
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)
```

- [ ] **Step 3: 語法檢查 + 確認既有測試未壞**

Run: `python -c "import ast; ast.parse(open('app.py', encoding='utf-8').read()); print('app ok')"`
Expected: 印出 `app ok`

Run: `python -m pytest -q`
Expected: 全部 PASS

- [ ] **Step 4: Commit**

```bash
git add app.py
git commit -m "$(cat <<'EOF'
Modify: app.py 改用 core 模組並加預測歷史頁籤

- STOCKS/抓資料改用 core.data，與排程共用
- 新增「預測歷史」分頁：命中率 + 每日預測vs結果表

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: dry-run 驗證 + 設定說明文件

**Files:**
- Create: `SETUP.md`
- Modify: `jobs/morning.py`、`jobs/evening.py`（加 `--dry-run`）

**Interfaces:**
- Produces:`python -m jobs.morning --dry-run` / `python -m jobs.evening --dry-run` — 用真資料 + 真 Claude 跑,但**不存檔、不推 Telegram**,把報告印到 stdout(供本機驗證一輪)。
- `SETUP.md` — 逐步說明如何建 Telegram bot、拿 chat id、設 GitHub Secrets。

- [ ] **Step 1: morning.py / evening.py 加 dry-run 入口**

把 `jobs/morning.py` 結尾的 `if __name__ == "__main__": run()` 改成:

```python
if __name__ == "__main__":
    import sys
    if "--dry-run" in sys.argv:
        name, cfg = next(iter(STOCKS.items()))
        df = fetch_daily(cfg["code"])
        if df.empty:
            print("資料缺漏")
        else:
            ind = compute_indicators(df, cfg["supports"])
            pred = make_prediction(ind, name)
            print(format_prediction(name, str(df.index[-1].date()), pred))
    else:
        run()
```

把 `jobs/evening.py` 結尾改成:

```python
if __name__ == "__main__":
    import sys
    if "--dry-run" in sys.argv:
        name, cfg = next(iter(STOCKS.items()))
        df = fetch_daily(cfg["code"])
        if df.empty:
            print("資料缺漏")
        else:
            from core.store import load_history, get_record, HISTORY_PATH
            date = str(df.index[-1].date())
            rec = get_record(load_history(HISTORY_PATH), date)
            if not rec:
                print(f"找不到 {date} 的預測，無法 dry-run 復盤")
            else:
                ind = compute_indicators(df, cfg["supports"])
                s1 = cfg["supports"]["支撐1 (短期)"]
                judged = judge(rec["prediction"], ind["close"],
                               ind["prev_close"], ind["ma20"], s1)
                review = make_review(rec["prediction"], judged, ind, name)
                print(format_review(name, date, review,
                                    hit_rate(load_history(HISTORY_PATH))))
    else:
        run()
```

- [ ] **Step 2: 語法檢查**

Run: `python -c "import ast; [ast.parse(open(f, encoding='utf-8').read()) for f in ('jobs/morning.py','jobs/evening.py')]; print('jobs ok')"`
Expected: 印出 `jobs ok`

- [ ] **Step 3: 寫 SETUP.md**

Create `SETUP.md`:

```markdown
# 設定指南：自動預測 / 復盤 / Telegram 推送

## 1. 建 Telegram Bot
1. Telegram 搜尋 `@BotFather` → `/newbot` → 取得 **bot token**。
2. 對你的新 bot 傳任意一句話。
3. 瀏覽 `https://api.telegram.org/bot<你的TOKEN>/getUpdates`，
   在回應 JSON 找 `chat.id` → 這是你的 **chat id**。

## 2. 設 GitHub Secrets
Repo → Settings → Secrets and variables → Actions → New repository secret，
新增三個：
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `ANTHROPIC_API_KEY`

## 3. 本機 dry-run 驗證（可選）
```
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-...
python -m jobs.morning --dry-run    # 印出開盤預測，不發 Telegram、不存檔
```

## 4. 手動觸發一次
Repo → Actions → stock-predict → Run workflow → 選 morning，
跑完後檢查 Telegram 是否收到，以及 history/predictions.json 是否更新。

## 5. 排程
平日台灣 08:30 自動開盤預測、15:30 自動收盤復盤（見 .github/workflows/predict.yml）。
```

- [ ] **Step 4: Commit**

```bash
git add SETUP.md jobs/morning.py jobs/evening.py
git commit -m "$(cat <<'EOF'
Docs: 加 SETUP.md 與 jobs dry-run 模式

- dry-run：真資料+真Claude，但不存檔不推送，供本機驗證
- SETUP.md：Telegram bot/chat id/GitHub Secrets 設定步驟

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## 實作完成後

1. 跑 `python -m pytest -v` 確認全綠。
2. 依 `SETUP.md` 設好三個 Secrets。
3. 本機 `python -m jobs.morning --dry-run` 看報告長相。
4. push 後到 Actions 手動 Run workflow（先 morning，隔日或改測試資料後 evening）。
5. 確認 Telegram 收到、`history/predictions.json` 有被 commit、Streamlit 歷史頁籤顯示。
6. 一切正常後讓排程自動接手。
