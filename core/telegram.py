import os
import requests


def send(text, token=None, chat_id=None):
    token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Telegram: 缺 TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(
            url, data={"chat_id": chat_id, "text": text}, timeout=10
        )
        if r.status_code != 200:
            # 不印 token；只印 Telegram 回的錯誤，方便定位（401=token錯、
            # 400 chat not found=沒先對 bot 按 START）
            print(f"Telegram 發送失敗：HTTP {r.status_code} {r.text[:300]}")
        return r.status_code == 200
    except Exception as e:
        print(f"Telegram 發送例外：{e}")
        return False
