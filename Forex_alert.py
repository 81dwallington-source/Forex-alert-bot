

import os

import time

from datetime import datetime, timezone

import pandas as pd

import requests

import yfinance as yf

# =========================

# CONFIGURATION

# =========================

PAIRS = [

    "EURUSD", "GBPUSD", "USDJPY", "USDCHF",

    "USDCAD", "AUDUSD", "NZDUSD",

    "EURGBP", "EURJPY", "EURCHF", "EURCAD",

    "EURAUD", "EURNZD",

    "GBPJPY", "GBPCHF", "GBPCAD", "GBPAUD",

    "GBPNZD",

    "AUDJPY", "AUDCHF", "AUDCAD", "AUDNZD",

    "CADJPY", "CADCHF", "NZDJPY", "NZDCHF",

    "CHFJPY",

]

MAX_M5_AGE_MINUTES = 10

ATR_PERIOD = 14

ATR_MULTIPLIER = 1.5

REWARD_RISK = 1.5

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# =========================

# TELEGRAM

# =========================

def send_telegram(message):

    if not TOKEN or not CHAT_ID:

        raise RuntimeError("Telegram secrets are missing.")

    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

    response = requests.post(

        url,

        data={"chat_id": CHAT_ID, "text": message},

        timeout=20,

    )

    response.raise_for_status()

    result = response.json()

    if not result.get("ok"):

        raise RuntimeError("Telegram did not confirm delivery.")

    print("TELEGRAM DELIVERY CONFIRMED")

# =========================

# MARKET DATA

# =========================

def get_candles(pair, interval, period):

    ticker = pair + "=X"

    frame = yf.Ticker(ticker).history(

        period=period,

        interval=interval,

        auto_adjust=False,

        actions=False,

    )

    if frame is None or frame.empty:

        raise ValueError(f"No {interval} candles returned.")

    frame = frame.copy()

    frame = frame[~frame.index.duplicated(keep="last")]

    frame = frame.sort_index()

    required = ["Open", "High", "Low", "Close"]

    if any(column not in frame.columns for column in required):

        raise ValueError(f"Missing OHLC data for {interval}.")

    frame = frame.dropna(subset=required)

    # Exclude the candle that may still be forming.

    now = pd.Timestamp.now(tz="UTC")

    if frame.index.tz is None:

        frame.index = frame.index.tz_localize("UTC")

    else:

        frame.index = frame.index.tz_convert("UTC")

    duration = pd.Timedelta(interval)

    frame = frame[(frame.index + duration) <= now]

    if frame.empty:

        raise ValueError(f"No completed {interval} candles.")

    return frame

def add_indicators(frame):

    frame = frame.copy()

    frame["EMA9"] = frame["Close"].ewm(

        span=9, adjust=False

    ).mean()

    frame["EMA21"] = frame["Close"].ewm(

        span=21, adjust=False

    ).mean()

    frame["EMA20"] = frame["Close"].ewm(

        span=20, adjust=False

    ).mean()

    frame["EMA50"] = frame["Close"].ewm(

        span=50, adjust=False

    ).mean()

    previous_close = frame["Close"].shift(1)

    true_range = pd.concat(

        [

            frame["High"] - frame["Low"],

            (frame["High"] - previous_close).abs(),

            (frame["Low"] - previous_close).abs(),

        ],

        axis=1,

    ).max(axis=1)

    frame["ATR"] = true_range.ewm(

        alpha=1 / ATR_PERIOD,

        adjust=False,

        min_periods=ATR_PERIOD,

    ).mean()

    return frame

# =========================

# DIAGNOSTICS

# =========================

stats = {

    "pairs_checked": 0,

    "m5_data_loaded": 0,

    "stale_m5": 0,

    "m5_crossovers": 0,

    "m15_h1_aligned": 0,

    "signals": 0,

    "errors": 0,

}

# =========================

# SIGNAL SCAN

# =========================

def scan_pair(pair):

    try:

        m5 = add_indicators(get_candles(pair, "5m", "5d"))

        m15 = add_indicators(get_candles(pair, "15m", "5d"))

        h1 = add_indicators(get_candles(pair, "60m", "30d"))

        stats["m5_data_loaded"] += 1

        now = pd.Timestamp.now(tz="UTC")

        last_m5_time = m5.index[-1]

        age_minutes = (now - last_m5_time).total_seconds() / 60

        if age_minutes > MAX_M5_AGE_MINUTES:

            stats["stale_m5"] += 1

            print(f"{pair}: rejected; M5 candle is stale.")

            return None

        if len(m5) < 3 or len(m15) < 55 or len(h1) < 55:

            raise ValueError("Insufficient candles for indicators.")

        previous = m5.iloc[-2]

        current = m5.iloc[-1]

        if pd.isna(current["ATR"]) or current["ATR"] <= 0:

            raise ValueError("ATR is unavailable.")

        # Fresh crossover on the most recently closed M5 candle.

        bullish_cross = (

            previous["EMA9"] <= previous["EMA21"]

            and current["EMA9"] > current["EMA21"]

        )

        bearish_cross = (

            previous["EMA9"] >= previous["EMA21"]

            and current["EMA9"] < current["EMA21"]

        )

        if not bullish_cross and not bearish_cross:

            return None

        stats["m5_crossovers"] += 1

        m15_last = m15.iloc[-1]

        h1_last = h1.iloc[-1]

        if bullish_cross:

            direction = "BUY"

            aligned = (

                m15_last["EMA20"] > m15_last["EMA50"]

                and h1_last["EMA20"] > h1_last["EMA50"]

            )

        else:

            direction = "SELL"

            aligned = (

                m15_last["EMA20"] < m15_last["EMA50"]

                and h1_last["EMA20"] < h1_last["EMA50"]

            )

        if not aligned:

            print(f"{pair}: crossover rejected; M15/H1 not aligned.")

            return None

        stats["m15_h1_aligned"] += 1

        entry = float(current["Close"])

        risk_distance = float(current["ATR"]) * ATR_MULTIPLIER

        if direction == "BUY":

            stop_loss = entry - risk_distance

            take_profit = entry + (risk_distance * REWARD_RISK)

        else:

            stop_loss = entry + risk_distance

            take_profit = entry - (risk_distance * REWARD_RISK)

        stats["signals"] += 1

        return (

            f"FOREX SETUP - 5m\n"

            f"Pair: {pair[:3]}/{pair[3:]}\n"

            f"Direction: {direction}\n"

            f"Entry reference: {entry:.5f}\n"

            f"Stop-loss: {stop_loss:.5f}\n"

            f"Take-profit: {take_profit:.5f}\n"

            f"Planned risk/reward: 1:{REWARD_RISK:.2f}\n"

            f"5m trigger: fresh closed-candle EMA 9/21 crossover\n"

            f"15m confirmation: EMA 20/50 aligned\n"

            f"1h confirmation: EMA 20/50 aligned\n"

            f"Closed candle: {last_m5_time.strftime('%Y-%m-%d %H:%M UTC')}\n"

            f"Data source: Yahoo Finance; verify on broker."

        )

    except Exception as error:

        stats["errors"] += 1

        print(f"{pair}: data/scan error: {error}")

        return None

# =========================

# MAIN

# =========================

def main():

    print("Starting forex scan.")

    signals = []

    for pair in PAIRS:

        stats["pairs_checked"] += 1

        signal = scan_pair(pair)

        if signal:

            signals.append(signal)

        # Avoid hammering the data provider.

        time.sleep(0.5)

    # Send any qualifying signals.

    for signal in signals:

        send_telegram(signal)

    summary = (

        "FOREX SCAN DIAGNOSTICS\n"

        f"Pairs checked: {stats['pairs_checked']}\n"

        f"M5 data loaded: {stats['m5_data_loaded']}\n"

        f"Stale M5 candles rejected: {stats['stale_m5']}\n"

        f"Fresh M5 crossovers: {stats['m5_crossovers']}\n"

        f"M15/H1 aligned after crossover: "

        f"{stats['m15_h1_aligned']}\n"

        f"Qualifying signals: {stats['signals']}\n"

        f"Data/scan errors: {stats['errors']}\n\n"

        "Data source: Yahoo Finance, not a broker feed.\n"

        "Confirm prices and entry conditions on your broker."

    )

    print(summary)

    send_telegram(summary)

if __name__ == "__main__":

    main()
