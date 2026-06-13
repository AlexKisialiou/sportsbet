"""
Telegram notification job.
Run: python notify.py
Or with custom message: python notify.py "Your text here"
"""

import os
import sys
import requests
from datetime import datetime

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]


def send_message(text: str) -> None:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    resp = requests.post(url, json={"chat_id": CHAT_ID, "text": text}, timeout=10)
    resp.raise_for_status()
    print(f"Sent: {resp.json()['result']['message_id']}")


if __name__ == "__main__":
    message = sys.argv[1] if len(sys.argv) > 1 else f"Hello from Render! {datetime.utcnow():%Y-%m-%d %H:%M} UTC"
    send_message(message)
