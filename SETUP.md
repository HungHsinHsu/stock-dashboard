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
