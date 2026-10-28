

import os

import time

from datetime import datetime, timezone

import pandas as pd

import requests

import yfinance as yf

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

PAIRS = [

    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD",

    "AUDUSD", "NZDUSD", "EURGBP", "EURJPY", "EURCHF",

    "EURCAD", "EURAUD", "EURNZD", "GBPJPY", "GBPCHF",

    "GBPCAD", "GBPAUD", "GBPNZD", "AUDJPY", "AUDCHF",

    "AUDCAD", "AUDNZD", "NZDJPY", "NZDCHF", "NZDCAD",

    "CADJPY", "CADCHF",

]

MAX_AGE_MINUTES = 10

ATR_MULTIPLIER = 1.5

REWARD_RISK = 1.5

def telegram(message):

    if not TOKEN or not CHAT_ID:

        raise RuntimeError(

            "Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID secret"

        )

    response = requests.post(

        f"https://api.telegram.org/bot{TOKEN}/sendMessage",

        data={"chat_id": CHAT_ID, "text": message},

        timeout=20,

    )

    response.raise_for_status()

    result = response.json()

    if not result.get("ok"):

        raise RuntimeError(

            "Telegram rejected message: "

            + str(result.get("description", "unknown error"))

        )

    print("TELEGRAM DELIVERY CONFIRMED")

def get_candles(pair, interval, period, minutes):

    data = yf.download(

        f"{pair}=X",

        period=period,

        interval=interval,

        auto_adjust=False,

        progress=False,

        threads=False,

    )

    if data is None or data.empty:

        raise ValueError("Yahoo Finance returned no candles")

    if isinstance(data.columns, pd.MultiIndex):

        data.columns = data.columns.get_level_values(0)

    data = data.dropna(subset=["Open", "High", "Low", "Close"])

    data = data.sort_index()

    index = pd.DatetimeIndex(data.index)

    if index.tz is None:

        index = index.tz_localize("UTC")

    else:

        index = index.tz_convert("UTC")

    data.index = index

    now = pd.Timestamp.now(tz="UTC")

    data = data.loc[

        data.index + pd.Timedelta(minutes=minutes) <= now

    ].copy()

    if len(data) < 55:

        raise ValueError("Insufficient completed candles")

    return data

def add_indicators(data):

    data = data.copy()

    close = data["Close"]

    data["ema9"] = close.ewm(span=9, adjust=False).mean()

    data["ema21"] = close.ewm(span=21, adjust=False).mean()

    data["ema20"] = close.ewm(span=20, adjust=False).mean()

    data["ema50"] = close.ewm(span=50, adjust=False).mean()

    previous_close = close.shift(1)

    true_range = pd.concat(

        [

            data["High"] - data["Low"],

            (data["High"] - previous_close).abs(),

            (data["Low"] - previous_close).abs(),

        ],

        axis=1,

    ).max(axis=1)

    data["atr"] = true_range.rolling(14).mean()

    return data

def scan_pair(pair):

    m5 = get_candles(pair, "5m", "5d", 5)

    m15 = get_candles(pair, "15m", "30d", 15)

    h1 = get_candles(pair, "1h", "60d", 60)

    candle_time = m5.index[-1]

    def candle_age():

        now = pd.Timestamp.now(tz="UTC")

        close_time = candle_time + pd.Timedelta(minutes=5)

        return (now - close_time).total_seconds() / 60

    age = candle_age()

    if age < 0 or age > MAX_AGE_MINUTES:

        raise ValueError(f"Stale M5 candle ({age:.1f} minutes old)")

    m5 = add_indicators(m5)

    m15 = add_indicators(m15)

    h1 = add_indicators(h1)

    previous = m5.iloc[-2]

    last = m5.iloc[-1]

    trend15 = m15.iloc[-1]

    trend1h = h1.iloc[-1]

    atr = float(last["atr"])

    if pd.isna(atr) or atr <= 0:

        raise ValueError("ATR unavailable")

    buy_cross = (

        previous["ema9"] <= previous["ema21"]

        and last["ema9"] > last["ema21"]

    )

    sell_cross = (

        previous["ema9"] >= previous["ema21"]

        and last["ema9"] < last["ema21"]

    )

    buy_confirmed = (

        trend15["ema20"] > trend15["ema50"]

        and trend1h["ema20"] > trend1h["ema50"]

    )

    sell_confirmed = (

        trend15["ema20"] < trend15["ema50"]

        and trend1h["ema20"] < trend1h["ema50"]

    )

    if buy_cross and buy_confirmed:

        direction = "BUY"

    elif sell_cross and sell_confirmed:

        direction = "SELL"

    else:

        return None

    entry = float(last["Close"])

    risk = atr * ATR_MULTIPLIER

    if direction == "BUY":

        sl = entry - risk

        tp = entry + risk * REWARD_RISK

    else:

        sl = entry + risk

        tp = entry - risk * REWARD_RISK

    if candle_age() < 0 or candle_age() > MAX_AGE_MINUTES:

        raise ValueError("Candle became stale during scan")

    return (

        "FOREX SETUP - 5m\n"

        f"Pair: {pair}\n"

        f"Direction: {direction}\n"

        f"Entry reference: {entry:.5f}\n"

        f"Stop-loss: {sl:.5f}\n"

        f"Take-profit: {tp:.5f}\n"

        "Planned risk/reward: 1:1.50\n"

        "5m trigger: closed-candle EMA 9/21 crossover\n"

        "15m confirmation: EMA 20/50 aligned\n"

        "1h confirmation: EMA 20/50 aligned\n"

        f"Closed candle: {candle_time.isoformat()}\n"

        "Data source: Yahoo Finance; verify with your broker\n"

        "Entry is a reference, not a guaranteed fill."

    )

def main():

    print("Forex bot starting")

    print("UTC time:", datetime.now(timezone.utc).isoformat())

    if not TOKEN or not CHAT_ID:

        raise RuntimeError("Telegram secrets are missing")

    # Test Telegram first so delivery problems are visible.

    telegram("Forex alert bot: scan starting.")

    alerts = []

    errors = 0

    for pair in PAIRS:

        try:

            signal = scan_pair(pair)

            if signal:

                alerts.append(signal)

                print(f"{pair}: qualifying setup found")

            else:

                print(f"{pair}: no qualifying setup")

        except Exception as exc:

            errors += 1

            print(f"{pair}: scan rejected/error: {exc}")

        time.sleep(0.3)

    for signal in alerts:

        telegram(signal)

        time.sleep(1)

    summary = (

        "Forex scan complete.\n"

        f"Pairs checked: {len(PAIRS)}\n"

        f"Qualifying signals: {len(alerts)}\n"

        f"Pairs with data/errors: {errors}\n"

        "Stale M5 candles are rejected.\n"

        "Data source: Yahoo Finance, not a broker feed."

    )

    telegram(summary)

    print(summary)

if __name__ == "__main__":

    main()
