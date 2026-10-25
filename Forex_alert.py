

import os

import requests

from datetime import datetime, timezone

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_telegram(message):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:

        print("Telegram credentials are not configured.")

        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    response = requests.post(

        url,

        json={"chat_id": TELEGRAM_CHAT_ID, "text": message},

        timeout=15,

    )

    response.raise_for_status()

def main():

    print("Forex alert bot started.")

    print("UTC time:", datetime.now(timezone.utc).isoformat())

    print("Waiting for live market-data and signal rules.")

    send_telegram("Forex alert bot connected and running.")

if __name__ == "__main__":

    main()
