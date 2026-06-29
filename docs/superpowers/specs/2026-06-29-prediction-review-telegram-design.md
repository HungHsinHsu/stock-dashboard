# 設計文件:開盤預測 → 收盤復盤 → 檢討 → Telegram 報告

- 日期:2026-06-29
- 專案:stock-dashboard(華邦電 2344 台股觀察工具)
- 狀態:設計待審

## 1. 目標

把現有「只看 K 線圖」的 Streamlit 工具,擴充成一個每天自動運作的進場時機分析助手:

1. **開盤預測**:每個交易日早上,根據技術指標自動產生當日預測(進場訊號、漲跌方向、支撐/均線站穩預測 + 理由),發到 Telegram。
2. **收盤復盤**:收盤後抓當日實際結果,逐項對答案(成功/失敗),累積命中率。
3. **失敗檢討**:預測失敗時,由 Claude 根據當天指標分析「為什麼錯」。
4. **報告推送**:預測與復盤兩份報告都透過 Telegram Bot 主動發送給使用者。
5. Streamlit App 繼續當「隨時打開看」的看板,並新增歷史/命中率檢視。

## 2. 關鍵限制與決策

- **Streamlit Cloud 無法自我排程**:只有人打開網頁才執行。定時自動推送改由 **GitHub Actions cron** 負責(免費、人不在也會跑)。
- **資料源是 TWSE 盤後日線**:盤中拿不到當日價格。因此早上發預測時只有「到昨天收盤為止」的資料。
  - v1 的可驗證宣告定為「**今日收盤 vs 昨日收盤**」(早上可預測、收盤可驗證)。
  - 未來若要更即時,可再接 TWSE 盤中 API 取得當日開盤價(列為 v2,不在本次範圍)。
- **預測/檢討引擎**:規則計算指標打底 + **Claude(Opus 4.8,model id `claude-opus-4-8`)** 產生自然語言預測理由與失敗檢討。用量極低(每日 2 次),成本約 $5/月,以模型常數集中管理可隨時切換。
- **資料持久化**:預測與復盤紀錄存成 repo 內的 JSON 檔(`history/predictions.json`),由 GitHub Actions job commit 回 repo。免額外資料庫,且 Streamlit 可直接讀來顯示歷史。

## 3. 系統架構

```
GitHub Actions(cron 排程器)
  ├─ 08:30 台灣時間  morning job  → 抓TWSE+算指標 → Claude預測 → 存紀錄 → 發Telegram
  └─ 15:30 台灣時間  evening job  → 抓今日收盤 → 對答案 → Claude檢討 → 更新紀錄 → 發Telegram
        │
        │ commit history/predictions.json 回 repo
        ▼
Streamlit App(看板) ── 讀 history/predictions.json ── 顯示原圖 + 預測vs結果歷史/命中率
```

兩個 job 共用同一套 `core/` 邏輯模組。

## 4. 程式結構

```
core/
  data.py        # 抓 TWSE 日線(從現有 app.py 抽出,morning/evening/app 共用)
  indicators.py  # 計算 MA5/MA20/MA60、支撐位距離、量比(vs 20日均量)、RSI(14)、KD → 回傳 dict
  predict.py     # 規則打底 + 呼叫 Claude 產生:進場訊號 / 方向 / 支撐預測 / 理由
  review.py      # 拿預測對今日實際結果,逐項判定成功/失敗,呼叫 Claude 檢討失敗原因
  store.py       # 讀寫 history/predictions.json
  telegram.py    # 發送訊息到 Telegram
  llm.py         # 包 Anthropic API(集中模型常數 MODEL = "claude-opus-4-8")
jobs/
  morning.py     # 進入點:indicators → predict → store → telegram
  evening.py     # 進入點:抓今日 → review → store → telegram
app.py           # Streamlit:原圖 + 新增「預測歷史 / 命中率」頁籤
.github/workflows/predict.yml   # 兩個 cron 排程定義
tests/
  test_indicators.py  # 用固定假資料驗證指標計算
  test_review.py      # 驗證成功/失敗判定邏輯(不依賴網路/API)
requirements.txt      # 追加 anthropic
```

每個模組單一職責、介面清楚:`indicators(df) -> dict`、`predict(indicators) -> Prediction`、`review(prediction, today) -> ReviewResult`,核心邏輯可離線測試。

## 5. 資料模型(history/predictions.json)

陣列,每筆一個交易日:

```json
{
  "date": "2026-06-30",
  "stock": "2344",
  "prediction": {
    "signal": "觀望",
    "direction": "跌",
    "hold_ma20": false,
    "hold_support1": false,
    "reason": "量縮跌破MA20,RSI走弱...",
    "indicators": { "ma20": 186.5, "rsi": 42.1, "vol_ratio": 0.8, "...": "..." }
  },
  "review": {
    "actual_close": 201.0,
    "prev_close": 203.0,
    "direction_actual": "跌",
    "results": { "direction": true, "hold_ma20": true, "signal": true },
    "success": true,
    "critique": null
  }
}
```

`review` 在收盤 job 執行後才補上;失敗時 `critique` 由 Claude 填入檢討文字。

## 6. 預測邏輯(morning)

1. 抓近 6 個月日線(到昨日)。
2. `indicators.py` 算:MA5/20/60、收盤距各支撐位%、量比、RSI(14)、KD。
3. `predict.py`:
   - 規則先算出客觀傾向(例:價在 MA20 上 + 量增 + RSI>50 偏多)。
   - 把指標與規則傾向丟給 Claude,要求輸出結構化 JSON:`signal`(進場/觀望/避開)、`direction`(漲/跌)、`hold_ma20`、`hold_support1`、`reason`。
4. `store.py` 寫入當日預測。
5. `telegram.py` 發「開盤預測」報告。

## 7. 復盤邏輯(evening)

1. 抓今日日線(含今日收盤)。
2. `review.py` 讀當日預測,逐項對答案:
   - `direction`:今日收盤 vs 昨收 的實際漲跌 == 預測方向?
   - `hold_ma20` / `hold_support1`:實際是否站穩?
   - `signal`:以方向結果驗證(進場→需漲、避開→需跌或續弱)。
3. 任一失敗 → 把預測、實際、指標丟給 Claude 產生 `critique`。
4. 計算並附上歷史命中率(方向準確率等)。
5. 更新紀錄、發「收盤復盤」報告。

## 8. 報告格式(Telegram)

**開盤預測**(範例):
```
📈 華邦電(2344) 開盤預測 2026-06-30
進場訊號:觀望
方向:預期下跌(vs 昨收 203.0)
站穩MA20:預期否(MA20=186.5)
理由:量縮、RSI 42 走弱,跌破MA20後反彈無量...
```

**收盤復盤**(範例):
```
🔍 華邦電(2344) 收盤復盤 2026-06-30
今日收盤:201.0(跌 -2.0)
方向 預測跌/實際跌 ✅
站穩MA20 預測否/實際否 ✅
本日預測:命中 ✅
近 20 日方向命中率:65%
```

失敗時追加 Claude 檢討段落。

## 9. 設定(使用者需提供,存 GitHub Actions Secrets)

1. `TELEGRAM_BOT_TOKEN`:向 @BotFather 申請。
2. `TELEGRAM_CHAT_ID`:使用者的 chat id。
3. `ANTHROPIC_API_KEY`:Claude API 金鑰。

實作時提供逐步設定指引。Secrets 不進 repo。

## 10. 錯誤處理

- 抓不到資料 / API 失敗 → 發一則「今日資料缺漏,已跳過」Telegram,job 不靜默失敗。
- 非交易日(假日、今日無新資料)→ 自動跳過,不發無意義報告。
- Claude 回傳非預期格式 → 重試一次,仍失敗則退回純規則版報告並標註「AI 分析暫缺」。

## 11. 測試策略

- `indicators` / `review` 判定邏輯:單元測試 + 固定假資料,不依賴網路與 API。
- `telegram` / `llm`:以 mock 驗證呼叫參數,不實際發送。
- 端對端:提供一個 `--dry-run` 模式,印出報告但不發送、不 commit,供本機驗證。

## 12. 範圍外(未來)

- 盤中即時資料(當日開盤價、即時報價)。
- 多檔股票(目前結構已預留 STOCKS dict,可擴充)。
- 進階回測 / 參數最佳化。

## 13. 實作順序(概要)

1. 抽出 `core/data.py`(從現有 app.py),不改變現有行為。
2. `indicators.py` + 測試。
3. `store.py` + `telegram.py` + `llm.py`(基礎設施)。
4. `predict.py` + `morning.py`(可先 dry-run)。
5. `review.py` + `evening.py` + 測試。
6. `.github/workflows/predict.yml` 兩個 cron。
7. Streamlit 新增歷史/命中率頁籤。
8. 設定 Secrets、實機驗證一輪。
