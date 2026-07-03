# stock-dashboard — 專案記憶

## 時區（重要）

- **使用者時區 = 台灣 UTC+8（Asia/Taipei，無日光節約）。**
- **機器人與網頁顯示的所有時間、日期一律用 UTC+8。**
- 伺服器（GitHub Actions、Streamlit Cloud）跑在 **UTC**，所以**禁止**直接用
  `datetime.today()` / `datetime.now()` 產生要顯示或當清單日期的時間。
- 一律透過 `core/tz.py` 取現在／今天：
  - `now_tw()` → 現在的台灣時間（帶時區）
  - `today_tw()` → 今天的台灣日期
- 例外：`core/data.py` 內部走訪 TWSE 查詢日期也用 `now_tw()`，確保「今天是哪個交易日」以台灣日算。
- 看 GitHub Actions 的 run 時間戳記時記得那是 **UTC**，換算台灣要 **+8**。

## 排程（皆為台灣時間 UTC+8）

- 07:40 開盤前預測（主班）、08:10 備援班
- 15:20 收盤復盤、15:35 收盤後選股、18:00 復盤補跑
- 機器人內建備援排程 `jobs/bot.py` `_SCHED_SLOTS` 用 `_tw_now()`（= `now_tw()`）判時。

## 開發慣例

- 一人專案：直接開發、推 `main`，不開 PR。
- 改動後跑 `python -m pytest -q`。
