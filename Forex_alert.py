

import os

import time

import requests

from datetime import datetime, timezone

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

PAIRS = [

    "EURUSD=X", "GBPUSD=X", "USDJPY=X", "USDCHF=X",

    "USDCAD=X", "AUDUSD=X", "NZDUSD=X", "EURGBP=X",

    "EURJPY=X", "GBPJPY=X", "AUDJPY=X", "CADJPY=X",

    "CHFJPY=X", "EURAUD=X", "EURCAD=X", "GBPCAD=X",

    "GBPAUD=X", "AUDCAD=X", "NZDJPY=X", "AUDNZD=X"

]

session = requests.Session()

session.headers.update({"User-Agent": "Mozilla/5.0"})

def telegram(message):

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:

        print("Telegram secrets are missing.")

        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    response = session.post(

        url,

        json={"chat_id": TELEGRAM_CHAT_ID, "text": message},

        timeout=15

    )

    response.raise_for_status()

def get_candles(pair, interval):

    period = "2d" if interval == "1m" else "5d"

    url = (

        f"https://query1.finance.yahoo.com/v8/finance/chart/{pair}"

        f"?range={period}&interval={interval}"

    )

    response = session.get(url, timeout=20)

    response.raise_for_status()

    result = response.json()["chart"]["result"]

    if not result:

        raise ValueError("No market data returned")

    item = result[0]

    timestamps = item.get("timestamp", [])

    quotes = item["indicators"]["quote"][0]

    candles = []

    for i, stamp in enumerate(timestamps):

        close = quotes["close"][i]

        high = quotes["high"][i]

        low = quotes["low"][i]

        if close is None or high is None or low is None:

            continue

        candles.append({

            "time": stamp,

            "close": float(close),

            "high": float(high),

            "low": float(low)

        })

    if len(candles) < 30:

        raise ValueError("Not enough candles")

    # Exclude the latest candle because it may still be forming.

    return candles[:-1]

def ema(values, period):

    alpha = 2 / (period + 1)

    result = [values[0]]

    for value in values[1:]:

        result.append(alpha * value + (1 - alpha) * result[-1])

    return result

def find_signal(candles):

    closes = [c["close"] for c in candles]

    fast = ema(closes, 9)

    slow = ema(closes, 21)

    # Require a fresh crossover on the most recent closed candle.

    previous = -2

    current = -1

    if fast[previous] <= slow[previous] and fast[current] > slow[current]:

        return "BUY"

    if fast[previous] >= slow[previous] and fast[current] < slow[current]:

        return "SELL"

    return None

def scan_pair(pair, interval):

    candles = get_candles(pair, interval)

    signal = find_signal(candles)

    if not signal:

        return None

    # Confirm the direction with the 5-minute trend.

    if interval == "1m":

        trend_candles = get_candles(pair, "5m")

        closes = [c["close"] for c in trend_candles]

        fast = ema(closes, 9)

        slow = ema(closes, 21)

        if signal == "BUY" and fast[-1] <= slow[-1]:

            return None

        if signal == "SELL" and fast[-1] >= slow[-1]:

            return None

    candle = candles[-1]

    recent = candles[-15:]

    avg_range = sum(c["high"] - c["low"] for c in recent) / len(recent)

    if avg_range <= 0:

        return None

    entry = candle["close"]

    risk = max(avg_range * 1.5, entry * 0.0002)

    if signal == "BUY":

        sl = entry - risk

        tp = entry + risk * 1.5

    else:

        sl = entry + risk

        tp = entry - risk * 1.5

    # Reject excessively wide stops.

    if risk / entry > 0.01:

        return None

    digits = 3 if "JPY" in pair else 5

    name = pair.replace("=X", "").replace("USD", "/USD")

    name = pair.replace("=X", "")

    name = name[:3] + "/" + name[3:]

    candle_time = datetime.fromtimestamp(

        candle["time"], timezone.utc

    ).strftime("%Y-%m-%d %H:%M UTC")

    return (

        f"FOREX SETUP — {interval}\n"

        f"Pair: {name}\n"

        f"Direction: {signal}\n"

        f"Entry: {entry:.{digits}f}\n"

        f"Stop-loss: {sl:.{digits}f}\n"

        f"Take-profit: {tp:.{digits}f}\n"

        f"Planned risk/reward: 1:1.5\n"

        f"Closed candle: {candle_time}\n"

        "Check the live broker price before entering. "

        "This is not a guaranteed trade."

    )

def main():

    print("Forex alert bot started")

    print(datetime.now(timezone.utc).isoformat())

    print("Checking live market data and signal rules")

    telegram("Forex alert bot started. Checking market data.")

    for pair in PAIRS:

        for interval in ("1m", "5m"):

            try:

                message = scan_pair(pair, interval)

                if message:

                    print(message)

                    telegram(message)

                else:

                    print(f"No setup: {pair} {interval}")

            except Exception as error:

                print(f"Scan failed: {pair} {interval}: {error}")

            time.sleep(1)

    print("Scan complete")

if __name__ == "__main__":

    main()
