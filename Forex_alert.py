

import os

import time

import json

from datetime import datetime, timezone

import requests

import yfinance as yf

import pandas as pd

# ================= SETTINGS =================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

PAIRS = [

    "EURUSD=X", "GBPUSD=X", "USDJPY=X", "USDCHF=X",

    "USDCAD=X", "AUDUSD=X", "NZDUSD=X",

    "EURGBP=X", "EURJPY=X", "EURCHF=X", "EURCAD=X",

    "EURAUD=X", "EURNZD=X",

    "GBPJPY=X", "GBPCHF=X", "GBPCAD=X", "GBPAUD=X",

    "GBPNZD=X",

    "AUDJPY=X", "AUDCHF=X", "AUDCAD=X", "AUDNZD=X",

    "NZDJPY=X", "NZDCHF=X", "NZDCAD=X",

    "CADJPY=X", "CADCHF=X", "CHFJPY=X"

]

STATE_FILE = "signal_state.json"

RISK_REWARD = 1.5

ATR_MULTIPLIER = 1.5

MIN_ATR_PERCENT = 0.015

# ================= TELEGRAM =================

def send_telegram(message):

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:

        print("ERROR: Telegram secrets are missing.")

        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

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

        return True

    except Exception as error:

        print(f"Telegram error: {error}")

        return False

# ================= STATE =================

def load_state():

    try:

        with open(STATE_FILE, "r") as file:

            return json.load(file)

    except (FileNotFoundError, json.JSONDecodeError):

        return {"sent": []}

def save_state(state):

    with open(STATE_FILE, "w") as file:

        json.dump(state, file)

# ================= INDICATORS =================

def add_indicators(df):

    df = df.copy()

    df = df.dropna()

    df["ema9"] = df["Close"].ewm(span=9, adjust=False).mean()

    df["ema21"] = df["Close"].ewm(span=21, adjust=False).mean()

    df["ema20"] = df["Close"].ewm(span=20, adjust=False).mean()

    df["ema50"] = df["Close"].ewm(span=50, adjust=False).mean()

    previous_close = df["Close"].shift(1)

    ranges = pd.concat([

        df["High"] - df["Low"],

        (df["High"] - previous_close).abs(),

        (df["Low"] - previous_close).abs()

    ], axis=1)

    df["atr"] = ranges.max(axis=1).rolling(14).mean()

    return df.dropna()

def resample_candles(df, timeframe):

    result = df.resample(timeframe).agg({

        "Open": "first",

        "High": "max",

        "Low": "min",

        "Close": "last",

        "Volume": "sum"

    })

    return result.dropna(subset=["Open", "High", "Low", "Close"])

def pair_name(ticker):

    return ticker.replace("=X", "").replace("USD", "/USD", 1)

# ================= SIGNAL CHECK =================

def analyse_pair(ticker, state):

    try:

        raw = yf.download(

            ticker,

            period="5d",

            interval="5m",

            progress=False,

            auto_adjust=False,

            threads=False

        )

        if raw is None or raw.empty:

            print(f"{ticker}: no data returned")

            return

        if isinstance(raw.columns, pd.MultiIndex):

            raw.columns = raw.columns.get_level_values(0)

        required = ["Open", "High", "Low", "Close", "Volume"]

        if not all(column in raw.columns for column in required):

            print(f"{ticker}: missing candle data")

            return

        raw = raw[required].dropna()

        raw.index = pd.to_datetime(raw.index)

        # Exclude the newest candle because it may still be forming.

        if len(raw) < 100:

            print(f"{ticker}: insufficient candles")

            return

        candles_5m = add_indicators(raw.iloc[:-1].copy())

        candles_15m = add_indicators(

            resample_candles(raw, "15min").iloc[:-1].copy()

        )

        candles_1h = add_indicators(

            resample_candles(raw, "1h").iloc[:-1].copy()

        )

        if min(len(candles_5m), len(candles_15m), len(candles_1h)) < 55:

            print(f"{ticker}: insufficient higher-timeframe history")

            return

        last = candles_5m.iloc[-1]

        previous = candles_5m.iloc[-2]

        trend15 = candles_15m.iloc[-1]

        trend1h = candles_1h.iloc[-1]

        candle_time = candles_5m.index[-1]

        now = datetime.now(timezone.utc)

        if candle_time.tzinfo is None:

            candle_time = candle_time.tz_localize("UTC")

        age_minutes = (now - candle_time.to_pydatetime()).total_seconds() / 60

        # Reject obviously stale or future-dated candle data.

        if age_minutes < 0 or age_minutes > 20:

            print(f"{ticker}: candle data too old ({age_minutes:.1f} minutes)")

            return

        # Fresh 5-minute EMA crossover only.

        bullish_cross = (

            previous["ema9"] <= previous["ema21"]

            and last["ema9"] > last["ema21"]

        )

        bearish_cross = (

            previous["ema9"] >= previous["ema21"]

            and last["ema9"] < last["ema21"]

        )

        # Higher-timeframe direction must agree.

        bullish_trend = (

            trend15["ema20"] > trend15["ema50"]

            and trend1h["ema20"] > trend1h["ema50"]

        )

        bearish_trend = (

            trend15["ema20"] < trend15["ema50"]

            and trend1h["ema20"] < trend1h["ema50"]

        )

        price = float(last["Close"])

        atr = float(last["atr"])

        if price <= 0 or atr <= 0:

            return

        atr_percent = (atr / price) * 100

        if atr_percent < MIN_ATR_PERCENT:

            print(f"{ticker}: volatility too low")

            return

        direction = None

        if bullish_cross and bullish_trend and last["Close"] > last["Open"]:

            direction = "BUY"

        elif bearish_cross and bearish_trend and last["Close"] < last["Open"]:

            direction = "SELL"

        if direction is None:

            print(f"{ticker}: no qualifying setup")

            return

        signal_id = f"{ticker}_{direction}_{candle_time.isoformat()}"

        if signal_id in state["sent"]:

            print(f"{ticker}: this candle's signal was already sent")

            return

        distance = atr * ATR_MULTIPLIER

        if direction == "BUY":

            stop_loss = price - distance

            take_profit = price + (distance * RISK_REWARD)

        else:

            stop_loss = price + distance

            take_profit = price - (distance * RISK_REWARD)

        message = (

            "FOREX SETUP - 5m\n"

            f"Pair: {pair_name(ticker)}\n"

            f"Direction: {direction}\n"

            f"Entry reference: {price:.5f}\n"

            f"Stop-loss: {stop_loss:.5f}\n"

            f"Take-profit: {take_profit:.5f}\n"

            f"Planned risk/reward: 1: {RISK_REWARD:.2f}\n"

            "5m trigger: closed-candle EMA 9/21 crossover\n"

            "15m confirmation: EMA 20/50 aligned\n"

            "1h confirmation: EMA 20/50 aligned\n"

            f"Closed candle: {candle_time.isoformat()}\n"

            "Data source: Yahoo Finance; verify with your broker\n"

            "Note: Entry is a reference, not a guaranteed fill."

        )

        if send_telegram(message):

            state["sent"].append(signal_id)

            state["sent"] = state["sent"][-500:]

            save_state(state)

            print(f"{ticker}: signal sent")

        else:

            print(f"{ticker}: signal not sent")

    except Exception as error:

        print(f"{ticker}: analysis error: {error}")

# ================= MAIN =================

def main():

    print("Forex alert bot starting...")

    print("Scanning 5-minute setups with 15m and 1h confirmation.")

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:

        raise RuntimeError("Telegram secrets are missing in GitHub Actions.")

    state = load_state()

    for ticker in PAIRS:

        analyse_pair(ticker, state)

        time.sleep(1)

    print("Scan finished.")

if __name__ == "__main__":

    main()
