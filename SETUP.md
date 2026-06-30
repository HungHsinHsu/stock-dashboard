# 設定指南：自動預測 / 復盤 / Telegram 推送

本專案的 LLM 透過 **Claude Agent SDK** 執行，用你的 **Claude Max 訂閱**認證
（不需要、也不要再用 Anthropic API 金鑰）。

## 1. 建 Telegram Bot
1. Telegram 搜尋 `@BotFather` → `/newbot` → 取得 **bot token**。
2. 對你的新 bot 傳任意一句話。
3. 瀏覽 `https://api.telegram.org/bot<你的TOKEN>/getUpdates`，
   在回應 JSON 找 `chat.id` → 這是你的 **chat id**。
   （或搜尋 `@userinfobot`，按 START，它會直接回你的 id。）

## 2. 產生 Claude 訂閱 OAuth token（需要一台電腦）
1. 在電腦裝 Claude Code：`npm install -g @anthropic-ai/claude-code`
   （需 Node.js 18+）。
2. 跑 `claude setup-token` → 用你的 **Max 訂閱**帳號完成授權。
3. 它會印出一個效期約一年的 token，複製起來（指令不會幫你存）。
   - 手機無法執行此步驟；到電腦上操作。
   - token 約一年到期，到期再跑一次 `claude setup-token` 更新即可。

## 3. 設 GitHub Secrets
Repo → Settings → Secrets and variables → Actions → New repository secret，
新增三個（名稱完全一致）：
- `CLAUDE_CODE_OAUTH_TOKEN`（步驟 2 拿到的 token）
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

> 不再需要 `ANTHROPIC_API_KEY`；用量計入你的 Max 訂閱，不另外按 API 計費。

## 4. 本機 dry-run 驗證（可選）
```
pip install -r requirements.txt
export CLAUDE_CODE_OAUTH_TOKEN=...   # 步驟 2 的 token
python -m jobs.morning --dry-run    # 印出開盤預測，不發 Telegram、不存檔
```

## 5. 手動觸發一次
Repo → Actions → stock-predict → Run workflow → 選 morning，
跑完後檢查 Telegram 是否收到，以及 history/predictions.json 是否更新。

## 6. 排程
平日台灣 08:30 自動開盤預測、15:30 自動收盤復盤（見 .github/workflows/predict.yml）。

## 7. 用 Telegram 指令管理股票清單
直接傳指令給你的 bot（只有你的 chat id 能用）：
- `/add 2330` — 加入股票（可帶支撐：`/add 2330 1000 850`）
- `/remove 2330` — 移除
- `/list` — 看目前清單
- `/help` — 說明

由 .github/workflows/bot.yml 每 ~10 分鐘輪詢一次，指令會在數分鐘內生效；
清單存在 watchlist.json，排程預測與儀表板都會自動讀取。

## 想改省訂閱用量？
`core/llm.py` 的 `MODEL` 目前是 `claude-opus-4-8`。改成 `claude-sonnet-4-6`
可較省訂閱額度、速度也較快，台股技術分析多半夠用。
