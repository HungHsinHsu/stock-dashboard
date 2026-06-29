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
