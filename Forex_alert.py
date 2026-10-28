

import os

import time

import json

from datetime import datetime, timezone

import requests

import yfinance as yf

import pandas as pd

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

PAIRS = [

    "EURUSD=X", "GBPUSD=X", "USDJPY=X", "USDCHF=X",

    "USDCAD=X", "AUDUSD=X", "NZDUSD=X",

    "EURGBP=X", "EURJPY=X", "EURCHF=X", "EURCAD=X",

    "EURAUD=X", "EURNZD=X", "GBPJPY=X", "GBPCHF=X",

    "GBPCAD=X", "GBPAUD=X", "GBPNZD=X", "AUDJPY=X",

    "AUDCHF=X", "AUDCAD=X", "AUDNZD=X", "NZDJPY=X",

    "NZDCHF=X", "NZDCAD=X", "CADJPY=X", "CADCHF=X",

    "CHFJPY=X"

]

STATE_FILE = "signal_state.json"

RISK_REWARD = 1.5

ATR_MULTIPLIER = 1.5

def send_telegram(message):

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:

        raise RuntimeError("Telegram secrets are missing.")

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    response = requests.post(

        url,

        data={"chat_id": TELEGRAM_CHAT_ID, "text": message},

        timeout=15

    )

    response.raise_for_status()

    result = response.json()

    if not result.get("ok"):

        raise RuntimeError("Telegram rejected the message.")

    return True

def load_state():

    try:

        with open(STATE_FILE, "r") as file:

            return json.load(file)

    except (FileNotFoundError, json.JSONDecodeError):

        return {"sent": []}

def save_state(state):

    with open(STATE_FILE, "w") as file:

        json.dump(state, file)

def indicators(df):

    df = df.copy().dropna()

    df["ema9"] = df["Close"].ewm(span=9, adjust=False).mean()

    df["ema21"] = df["Close"].ewm(span=21, adjust=False).mean()

    df["ema20"] = df["Close"].ewm(span=20, adjust=False).mean()

    df["ema50"] = df["Close"].ewm(span=50, adjust=False).mean()

    previous_close = df["Close"].shift(1)

    true_ranges = pd.concat([

        df["High"] - df["Low"],

        (df["High"] - previous_close).abs(),

        (df["Low"] - previous_close).abs()

    ], axis=1)

    df["atr"] = true_ranges.max(axis=1).rolling(14).mean()

    return df.dropna()

def resample(df, period):

    return df.resample(period).agg({

        "Open": "first",

        "High": "max",

        "Low": "min",

        "Close": "last",

        "Volume": "sum"

    }).dropna(subset=["Open", "High", "Low", "Close"])

def analyse(ticker, state):

    try:

        raw = yf.download(

            ticker,

            period="10d",

            interval="5m",

            progress=False,

            auto_adjust=False,

            threads=False

        )

        if raw is None or raw.empty:

            print(f"{ticker}: no data")

            return None

        if isinstance(raw.columns, pd.MultiIndex):

            raw.columns = raw.columns.get_level_values(0)

        required = ["Open", "High", "Low", "Close", "Volume"]

        if not all(c in raw.columns for c in required):

            print(f"{ticker}: missing columns")

            return None

        raw = raw[required].dropna()

        raw.index = pd.to_datetime(raw.index)

        if len(raw) < 100:

            print(f"{ticker}: insufficient data")

            return None

        # Never use the newest possibly unfinished 5m candle.

        five = indicators(raw.iloc[:-1].copy())

        fifteen = indicators(resample(raw, "15min").iloc[:-1].copy())

        hourly = indicators(resample(raw, "1h").iloc[:-1].copy())

        if min(len(five), len(fifteen), len(hourly)) < 50:

            print(f"{ticker}: insufficient timeframe history")

            return None

        last = five.iloc[-1]

        prev = five.iloc[-2]

        t15 = fifteen.iloc[-1]

        t1h = hourly.iloc[-1]

        candle_time = five.index[-1]

        if candle_time.tzinfo is None:

            candle_time = candle_time.tz_localize("UTC")

        age = (

            datetime.now(timezone.utc) - candle_time.to_pydatetime()

        ).total_seconds() / 60

        if age < 0 or age > 20:

            print(f"{ticker}: stale candle data ({age:.1f}m)")

            return None

        buy_cross = (

            prev["ema9"] <= prev["ema21"]

            and last["ema9"] > last["ema21"]

        )

        sell_cross = (

            prev["ema9"] >= prev["ema21"]

            and last["ema9"] < last["ema21"]

        )

        buy_trend = (

            t15["ema20"] > t15["ema50"]

            and t1h["ema20"] > t1h["ema50"]

        )

        sell_trend = (

            t15["ema20"] < t15["ema50"]

            and t1h["ema20"] < t1h["ema50"]

        )

        price = float(last["Close"])

        atr = float(last["atr"])

        if price <= 0 or atr <= 0:

            return None

        direction = None

        if buy_cross and buy_trend and last["Close"] > last["Open"]:

            direction = "BUY"

        elif sell_cross and sell_trend and last["Close"] < last["Open"]:

            direction = "SELL"

        if not direction:

            print(f"{ticker}: no qualifying setup")

            return None

        signal_id = f"{ticker}_{direction}_{candle_time.isoformat()}"

        if signal_id in state["sent"]:

            print(f"{ticker}: duplicate signal skipped")

            return None

        distance = atr * ATR_MULTIPLIER

        if direction == "BUY":

            sl = price - distance

            tp = price + distance * RISK_REWARD

        else:

            sl = price + distance

            tp = price - distance * RISK_REWARD

        pair = ticker.replace("=X", "")

        pair = pair[:3] + "/" + pair[3:]

        message = (

            "FOREX SETUP - 5m\n"

            f"Pair: {pair}\n"

            f"Direction: {direction}\n"

            f"Entry reference: {price:.5f}\n"

            f"Stop-loss: {sl:.5f}\n"

            f"Take-profit: {tp:.5f}\n"

            f"Planned risk/reward: 1:{RISK_REWARD:.2f}\n"

            "5m trigger: closed-candle EMA 9/21 crossover\n"

            "15m confirmation: EMA 20/50 aligned\n"

            "1h confirmation: EMA 20/50 aligned\n"

            f"Candle time (UTC): {candle_time.isoformat()}\n"

            "Data: Yahoo Finance; verify price with your broker.\n"

            "Entry is a reference, not a guaranteed fill."

        )

        send_telegram(message)

        state["sent"].append(signal_id)

        state["sent"] = state["sent"][-500:]

        save_state(state)

        print(f"{ticker}: signal sent")

        return direction

    except Exception as error:

        print(f"{ticker}: ERROR: {error}")

        return None

def main():

    print("Forex alert bot starting.")

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:

        raise RuntimeError("Telegram secrets are missing.")

    state = load_state()

    signals = []

    errors = 0

    for ticker in PAIRS:

        result = analyse(ticker, state)

        if result:

            signals.append(f"{ticker.replace('=X', '')}: {result}")

        time.sleep(1)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if signals:

        summary = (

            f"Forex scan complete - {now}\n"

            f"Qualifying signals sent: {len(signals)}\n"

            + "\n".join(signals)

        )

    else:

        summary = (

            f"Forex scan complete - {now}\n"

            "No qualifying setups found this scan.\n"

            "No trade signal was issued. Waiting for better conditions."

        )

    send_telegram(summary)

    print(summary)

if __name__ == "__main__":

    main()
