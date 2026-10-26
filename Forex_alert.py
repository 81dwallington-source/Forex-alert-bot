

import os

import time

from datetime import datetime, timezone

import requests

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

PAIRS = [

    "EURUSD=X", "GBPUSD=X", "USDJPY=X", "USDCHF=X",

    "USDCAD=X", "AUDUSD=X", "NZDUSD=X", "EURGBP=X",

    "EURJPY=X", "GBPJPY=X", "AUDJPY=X", "CADJPY=X",

    "CHFJPY=X", "EURAUD=X", "EURCAD=X", "GBPCAD=X",

    "GBPAUD=X", "AUDCAD=X", "NZDJPY=X", "AUDNZD=X",

]

session = requests.Session()

session.headers.update({"User-Agent": "Mozilla/5.0"})

def telegram(message):

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:

        raise ValueError("Telegram secrets are missing")

    url = (

        f"https://api.telegram.org/bot"

        f"{TELEGRAM_TOKEN}/sendMessage"

    )

    response = session.post(

        url,

        json={"chat_id": TELEGRAM_CHAT_ID, "text": message},

        timeout=15,

    )

    response.raise_for_status()

    if not response.json().get("ok"):

        raise ValueError("Telegram rejected the message")

def get_candles(pair, interval):

    if interval not in ("1m", "5m"):

        raise ValueError("Unsupported candle interval")

    period = "2d" if interval == "1m" else "5d"

    url = (

        f"https://query1.finance.yahoo.com/v8/finance/chart/"

        f"{pair}?range={period}&interval={interval}"

    )

    response = session.get(url, timeout=20)

    response.raise_for_status()

    data = response.json()

    chart = data.get("chart", {})

    results = chart.get("result") or []

    if not results:

        raise ValueError("No market data returned")

    item = results[0]

    timestamps = item.get("timestamp") or []

    quote_list = item.get("indicators", {}).get("quote", [])

    if not timestamps or not quote_list:

        raise ValueError("Market candles are missing")

    quotes = quote_list[0]

    closes = quotes.get("close") or []

    highs = quotes.get("high") or []

    lows = quotes.get("low") or []

    candles = []

    for i, stamp in enumerate(timestamps):

        if i >= len(closes) or i >= len(highs) or i >= len(lows):

            continue

        close = closes[i]

        high = highs[i]

        low = lows[i]

        if close is None or high is None or low is None:

            continue

        if not (0 < float(low) <= float(high)):

            continue

        if not (float(low) <= float(close) <= float(high)):

            continue

        candles.append({

            "time": int(stamp),

            "close": float(close),

            "high": float(high),

            "low": float(low),

        })

    if len(candles) < 31:

        raise ValueError("Not enough valid candles")

    # Reject stale data before removing the possibly forming candle.

    latest_age = (

        datetime.now(timezone.utc).timestamp()

        - candles[-1]["time"]

    )

    max_age = 180 if interval == "1m" else 600

    if latest_age < -60 or latest_age > max_age:

        raise ValueError("Market data is stale - no signal allowed")

    # Exclude the latest candle because it may still be forming.

    return candles[:-1]

def ema(values, period):

    if len(values) < period:

        raise ValueError("Not enough values for EMA")

    alpha = 2 / (period + 1)

    result = [values[0]]

    for value in values[1:]:

        result.append(

            alpha * value + (1 - alpha) * result[-1]

        )

    return result

def find_signal(candles):

    closes = [c["close"] for c in candles]

    fast = ema(closes, 9)

    slow = ema(closes, 21)

    if fast[-2] <= slow[-2] and fast[-1] > slow[-1]:

        return "BUY"

    if fast[-2] >= slow[-2] and fast[-1] < slow[-1]:

        return "SELL"

    return None

def pair_name(pair):

    symbol = pair.replace("=X", "")

    return symbol[:3] + "/" + symbol[3:]

def scan_pair(pair, interval):

    candles = get_candles(pair, interval)

    signal = find_signal(candles)

    if not signal:

        return None

    # Confirm 1-minute entries with the 5-minute trend.

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

    avg_range = sum(

        c["high"] - c["low"] for c in recent

    ) / len(recent)

    if avg_range <= 0:

        return None

    entry = candle["close"]

    risk = max(avg_range * 1.5, entry * 0.0002)

    if risk / entry > 0.01:

        return None

    if signal == "BUY":

        sl = entry - risk

        tp = entry + risk * 1.5

    else:

        sl = entry + risk

        tp = entry - risk * 1.5

    digits = 3 if "JPY" in pair else 5

    candle_time = datetime.fromtimestamp(

        candle["time"], timezone.utc

    ).strftime("%Y-%m-%d %H:%M UTC")

    return (

        f"FOREX SETUP - {interval}\n"

        f"Pair: {pair_name(pair)}\n"

        f"Direction: {signal}\n"

        f"Entry reference: {entry:.{digits}f}\n"

        f"Stop-loss: {sl:.{digits}f}\n"

        f"Take-profit: {tp:.{digits}f}\n"

        f"Planned risk/reward: 1:1.5\n"

        f"Closed candle: {candle_time}\n"

        "Data source: Yahoo Finance, not broker-verified.\n"

        "Check MT4 price and spread before considering entry. "

        "No trade is guaranteed."

    )

def main():

    print("Forex alert bot started")

    print(datetime.now(timezone.utc).isoformat())

    print("Checking market data and signal rules")

    telegram(

        "Forex alert bot started. "

        "Checking market data and signal rules."

    )

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

                print(

                    f"Scan failed: {pair} {interval}: {error}"

                )

            time.sleep(1)

    print("Scan complete")

if __name__ == "__main__":

    main()
