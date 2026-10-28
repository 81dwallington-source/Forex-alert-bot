

import os

import time

import requests

import pandas as pd

import yfinance as yf

from datetime import datetime, timezone, timedelta

# ================= SETTINGS =================

MAX_CANDLE_AGE_MINUTES = 10

MIN_RISK_REWARD = 1.5

ATR_MULTIPLIER = 1.5

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

PAIRS = [

    "EURUSD", "GBPUSD", "USDJPY", "USDCHF",

    "USDCAD", "AUDUSD", "NZDUSD",

    "EURGBP", "EURJPY", "EURCHF", "EURCAD",

    "EURAUD", "EURNZD", "GBPJPY", "GBPCHF",

    "GBPCAD", "GBPAUD", "GBPNZD", "AUDJPY",

    "AUDCHF", "AUDCAD", "AUDNZD", "NZDJPY",

    "NZDCHF", "NZDCAD", "CADJPY", "CADCHF",

]

def telegram(message):

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:

        print("ERROR: Telegram secrets are missing.")

        return

    url = (

        f"https://api.telegram.org/bot"

        f"{TELEGRAM_TOKEN}/sendMessage"

    )

    try:

        response = requests.post(

            url,

            data={

                "chat_id": TELEGRAM_CHAT_ID,

                "text": message

            },

            timeout=15

        )

        response.raise_for_status()

        result = response.json()

        if not result.get("ok"):

            print("Telegram rejected message:", result)

    except Exception as exc:

        print("Telegram error:", exc)

def get_candles(pair, interval, period):

    ticker = pair + "=X"

    try:

        df = yf.download(

            ticker,

            period=period,

            interval=interval,

            auto_adjust=False,

            progress=False,

            threads=False

        )

        if df is None or df.empty:

            print(f"{pair} {interval}: no data")

            return None

        if isinstance(df.columns, pd.MultiIndex):

            df.columns = df.columns.get_level_values(0)

        df = df[~df.index.duplicated(keep="last")].copy()

        df = df.dropna(subset=["Open", "High", "Low", "Close"])

        df = df.sort_index()

        # Make candle timestamps timezone-aware in UTC.

        idx = pd.DatetimeIndex(df.index)

        if idx.tz is None:

            idx = idx.tz_localize("UTC")

        else:

            idx = idx.tz_convert("UTC")

        df.index = idx

        # Exclude candles that have not yet closed.

        minutes = int(interval.replace("m", "").replace("h", "")) 

        if interval.endswith("h"):

            minutes *= 60

        now = pd.Timestamp.now(tz="UTC")

        candle_end = df.index + pd.Timedelta(minutes=minutes)

        df = df.loc[candle_end <= now].copy()

        if df.empty:

            print(f"{pair} {interval}: no completed candles")

            return None

        return df

    except Exception as exc:

        print(f"{pair} {interval} data error: {exc}")

        return None

def add_indicators(df):

    df = df.copy()

    df["ema9"] = df["Close"].ewm(span=9, adjust=False).mean()

    df["ema21"] = df["Close"].ewm(span=21, adjust=False).mean()

    df["ema20"] = df["Close"].ewm(span=20, adjust=False).mean()

    df["ema50"] = df["Close"].ewm(span=50, adjust=False).mean()

    previous_close = df["Close"].shift(1)

    previous_ema = df["Close"].ewm(

        span=14, adjust=False

    ).mean().shift(1)

    # ATR from true range.

    tr = pd.concat([

        df["High"] - df["Low"],

        (df["High"] - previous_close).abs(),

        (df["Low"] - previous_close).abs()

    ], axis=1).max(axis=1)

    df["atr"] = tr.ewm(span=14, adjust=False).mean()

    return df

def scan_pair(pair):

    m5 = get_candles(pair, "5m", "2d")

    m15 = get_candles(pair, "15m", "5d")

    h1 = get_candles(pair, "1h", "10d")

    if any(x is None for x in (m5, m15, h1)):

        return None

    if len(m5) < 60 or len(m15) < 60 or len(h1) < 60:

        print(f"{pair}: insufficient candles")

        return None

    # Reject stale 5-minute data.

    latest_start = m5.index[-1]

    latest_close_time = latest_start + pd.Timedelta(minutes=5)

    now = pd.Timestamp.now(tz="UTC")

    age_minutes = (now - latest_close_time).total_seconds() / 60

    if age_minutes < 0 or age_minutes > MAX_CANDLE_AGE_MINUTES:

        print(

            f"{pair}: REJECTED stale M5 data "

            f"({age_minutes:.1f} minutes old)"

        )

        return None

    m5 = add_indicators(m5)

    m15 = add_indicators(m15)

    h1 = add_indicators(h1)

    last = m5.iloc[-1]

    prev = m5.iloc[-2]

    trend15 = m15.iloc[-1]

    trend1h = h1.iloc[-1]

    if pd.isna(last["atr"]) or last["atr"] <= 0:

        return None

    # Only a fresh, completed-candle EMA crossover is eligible.

    bullish_cross = (

        prev["ema9"] <= prev["ema21"]

        and last["ema9"] > last["ema21"]

    )

    bearish_cross = (

        prev["ema9"] >= prev["ema21"]

        and last["ema9"] < last["ema21"]

    )

    direction = None

    if (

        bullish_cross

        and trend15["ema20"] > trend15["ema50"]

        and trend1h["ema20"] > trend1h["ema50"]

    ):

        direction = "BUY"

    elif (

        bearish_cross

        and trend15["ema20"] < trend15["ema50"]

        and trend1h["ema20"] < trend1h["ema50"]

    ):

        direction = "SELL"

    if direction is None:

        return None

    entry = float(last["Close"])

    risk = float(last["atr"]) * ATR_MULTIPLIER

    if direction == "BUY":

        sl = entry - risk

        tp = entry + risk * MIN_RISK_REWARD

    else:

        sl = entry + risk

        tp = entry - risk * MIN_RISK_REWARD

    # Final safety check immediately before creating the alert.

    now = pd.Timestamp.now(tz="UTC")

    age_minutes = (

        now - (m5.index[-1] + pd.Timedelta(minutes=5))

    ).total_seconds() / 60

    if age_minutes < 0 or age_minutes > MAX_CANDLE_AGE_MINUTES:

        print(f"{pair}: rejected at final freshness check")

        return None

    timestamp = m5.index[-1].strftime("%Y-%m-%d %H:%M UTC")

    message = (

        "FOREX SETUP - 5m\n"

        f"Pair: {pair}\n"

        f"Direction: {direction}\n"

        f"Entry reference: {entry:.5f}\n"

        f"Stop-loss: {sl:.5f}\n"

        f"Take-profit: {tp:.5f}\n"

        "Planned risk/reward: 1:1.50\n"

        "5m trigger: fresh closed-candle EMA 9/21 crossover\n"

        "15m confirmation: EMA 20/50 aligned\n"

        "1h confirmation: EMA 20/50 aligned\n"

        f"Closed candle: {timestamp}\n"

        "Data source: Yahoo Finance; verify with your broker\n"

        "Note: Entry is a reference, not a guaranteed fill."

    )

    return message

def main():

    print("Forex bot starting...")

    print("Checking data freshness before considering signals.")

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:

        print("ERROR: Configure Telegram GitHub Actions secrets.")

        raise SystemExit(1)

    alerts = []

    rejected_or_empty = 0

    for pair in PAIRS:

        try:

            signal = scan_pair(pair)

            if signal:

                alerts.append(signal)

            else:

                rejected_or_empty += 1

        except Exception as exc:

            print(f"{pair}: scan error: {exc}")

        time.sleep(0.5)

    for alert in alerts:

        telegram(alert)

        time.sleep(1)

    summary = (

        "Forex scan complete.\n"

        f"Pairs checked: {len(PAIRS)}\n"

        f"Signals found: {len(alerts)}\n"

        f"No signal / rejected data: {rejected_or_empty}\n"

        f"Scan time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"

        f"Stale M5 candles older than {MAX_CANDLE_AGE_MINUTES} "

        "minutes are rejected.\n"

        "Yahoo Finance data is not a guaranteed live broker feed."

    )

    telegram(summary)

    print(summary)

if __name__ == "__main__":

    main()
