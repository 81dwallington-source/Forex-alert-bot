

import os

import json

import csv

import time

from datetime import datetime, timezone

from pathlib import Path

import requests

import pandas as pd

import yfinance as yf

# =========================================================

# FOREX ALERT BOT — M5 ENTRIES, M15 + H1 CONFIRMATION

# Alerts only. No automatic trading.

# Data source: Yahoo Finance, NOT broker-verified.

# =========================================================

BOT_NAME = "Forex Alert Bot"

DATA_SOURCE = "Yahoo Finance (not broker-verified)"

TIMEFRAME = "5m"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

PAIRS = [

    "EURUSD=X", "GBPUSD=X", "USDJPY=X", "USDCHF=X",

    "USDCAD=X", "AUDUSD=X", "NZDUSD=X",

    "EURGBP=X", "EURJPY=X", "EURCHF=X", "EURAUD=X",

    "EURCAD=X", "EURNZD=X",

    "GBPJPY=X", "GBPCHF=X", "GBPAUD=X", "GBPCAD=X",

    "GBPNZD=X",

    "AUDJPY=X", "AUDCHF=X", "AUDCAD=X", "AUDNZD=X",

    "NZDJPY=X", "NZDCHF=X", "NZDCAD=X",

    "CADJPY=X", "CHFJPY=X",

]

MIN_RR = 1.5

ATR_STOP_MULTIPLIER = 1.2

MIN_ATR_PERCENT = 0.015

MAX_ATR_PERCENT = 0.60

MAX_CANDLE_ATR = 1.8

MIN_SCORE = 5

STATE_FILE = Path("signals_state.json")

LOG_FILE = Path("signal_log.csv")

def log(message):

    print(f"[{datetime.now(timezone.utc).isoformat()}] {message}",

          flush=True)

def telegram_send(message):

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:

        log("Telegram secrets missing. Check repository Actions secrets.")

        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    try:

        response = requests.post(

            url,

            json={

                "chat_id": TELEGRAM_CHAT_ID,

                "text": message,

                "disable_web_page_preview": True,

            },

            timeout=20,

        )

        response.raise_for_status()

        if not response.json().get("ok"):

            log("Telegram API rejected the message.")

            return False

        return True

    except Exception as exc:

        log(f"Telegram send error: {exc}")

        return False

def load_state():

    try:

        if STATE_FILE.exists():

            with STATE_FILE.open("r", encoding="utf-8") as file:

                return json.load(file)

    except Exception as exc:

        log(f"State file could not be read: {exc}")

    return {}

def save_state(state):

    with STATE_FILE.open("w", encoding="utf-8") as file:

        json.dump(state, file, indent=2)

def log_signal(row):

    exists = LOG_FILE.exists()

    with LOG_FILE.open("a", newline="", encoding="utf-8") as file:

        writer = csv.DictWriter(file, fieldnames=list(row.keys()))

        if not exists:

            writer.writeheader()

        writer.writerow(row)

def download_data(ticker, interval, period):

    data = yf.download(

        ticker,

        interval=interval,

        period=period,

        progress=False,

        auto_adjust=False,

        threads=False,

    )

    if data is None or data.empty:

        raise ValueError("No market data returned")

    if isinstance(data.columns, pd.MultiIndex):

        data.columns = data.columns.get_level_values(0)

    required = ["Open", "High", "Low", "Close"]

    if any(column not in data.columns for column in required):

        raise ValueError("Required OHLC columns missing")

    data = data[required].copy()

    data = data.dropna()

    data = data[~data.index.duplicated(keep="last")]

    data = data.sort_index()

    # Exclude the newest candle to avoid acting on an incomplete bar.

    if len(data) < 60:

        raise ValueError("Insufficient candle history")

    data = data.iloc[:-1].copy()

    if len(data) < 55:

        raise ValueError("Insufficient completed candle history")

    return data

def candle_is_fresh(data, max_age_minutes):

    last_time = data.index[-1]

    if last_time.tzinfo is None:

        last_time = last_time.tz_localize("UTC")

    else:

        last_time = last_time.tz_convert("UTC")

    age_minutes = (

        datetime.now(timezone.utc) - last_time.to_pydatetime()

    ).total_seconds() / 60

    return -1 <= age_minutes <= max_age_minutes, age_minutes

def indicators(data):

    df = data.copy()

    df["EMA9"] = df["Close"].ewm(span=9, adjust=False).mean()

    df["EMA21"] = df["Close"].ewm(span=21, adjust=False).mean()

    df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()

    df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()

    change = df["Close"].diff()

    gain = change.clip(lower=0)

    loss = -change.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()

    avg_loss = loss.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, float("nan"))

    df["RSI"] = 100 - (100 / (1 + rs))

    previous_close = df["Close"].shift(1)

    true_range = pd.concat(

        [

            df["High"] - df["Low"],

            (df["High"] - previous_close).abs(),

            (df["Low"] - previous_close).abs(),

        ],

        axis=1,

    ).max(axis=1)

    df["ATR"] = true_range.ewm(

        alpha=1 / 14, min_periods=14, adjust=False

    ).mean()

    return df.dropna()

def trend_direction(data):

    last = data.iloc[-1]

    if last["EMA20"] > last["EMA50"]:

        return "BUY"

    if last["EMA20"] < last["EMA50"]:

        return "SELL"

    return "NONE"

def analyse_pair(ticker):

    m5 = indicators(download_data(ticker, "5m", "5d"))

    m15 = indicators(download_data(ticker, "15m", "5d"))

    h1 = indicators(download_data(ticker, "1h", "1mo"))

    fresh, age = candle_is_fresh(m5, 12)

    if not fresh:

        return None, f"stale M5 data ({age:.1f} minutes old)"

    fresh15, age15 = candle_is_fresh(m15, 40)

    fresh1h, age1h = candle_is_fresh(h1, 150)

    if not fresh15 or not fresh1h:

        return None, "stale confirmation data"

    last = m5.iloc[-1]

    previous = m5.iloc[-2]

    close = float(last["Close"])

    atr = float(last["ATR"])

    rsi = float(last["RSI"])

    if close <= 0 or atr <= 0:

        return None, "invalid price or ATR"

    atr_percent = atr / close * 100

    if not MIN_ATR_PERCENT <= atr_percent <= MAX_ATR_PERCENT:

        return None, "volatility outside limits"

    candle_range = float(last["High"] - last["Low"])

    if candle_range > atr * MAX_CANDLE_ATR:

        return None, "oversized signal candle"

    m15_trend = trend_direction(m15)

    h1_trend = trend_direction(h1)

    if m15_trend == "NONE" or h1_trend == "NONE":

        return None, "unclear higher-timeframe trend"

    if m15_trend != h1_trend:

        return None, "M15/H1 trend mismatch"

    direction = m15_trend

    # Require a recent M5 EMA crossover, within the last 3

    # completed candles, with M15 and H1 trend agreement.

    recent = m5.iloc[-4:]

    if direction == "BUY":

        crossed = (

            (recent["EMA9"].shift(1) <= recent["EMA21"].shift(1))

            & (recent["EMA9"] > recent["EMA21"])

        ).any()

        if not crossed:

            return None, "no fresh M5 bullish crossover"

        if last["EMA9"] <= last["EMA21"]:

            return None, "M5 momentum no longer bullish"

        if not 50 <= rsi <= 68:

            return None, "RSI outside buy range"

        if close <= float(last["EMA21"]):

            return None, "price not holding above EMA21"

    else:

        crossed = (

            (recent["EMA9"].shift(1) >= recent["EMA21"].shift(1))

            & (recent["EMA9"] < recent["EMA21"])

        ).any()

        if not crossed:

            return None, "no fresh M5 bearish crossover"

        if last["EMA9"] >= last["EMA21"]:

            return None, "M5 momentum no longer bearish"

        if not 32 <= rsi <= 50:

            return None, "RSI outside sell range"

        if close >= float(last["EMA21"]):

            return None, "price not holding below EMA21"

    # Simple quality score. A high score is a filter, not

    # proof that the trade has a high probability of winning.

    score = 0

    if m15_trend == direction:

        score += 2

    if h1_trend == direction:

        score += 2

    if (direction == "BUY" and last["EMA9"] > last["EMA21"]) or (

        direction == "SELL" and last["EMA9"] < last["EMA21"]

    ):

        score += 1

    if (direction == "BUY" and 52 <= rsi <= 65) or (

        direction == "SELL" and 35 <= rsi <= 48

    ):

        score += 1

    if score < MIN_SCORE:

        return None, "quality score too low"

    # ATR-based stop and 1:1.5 minimum reward-to-risk.

    stop_distance = atr * ATR_STOP_MULTIPLIER

    if direction == "BUY":

        stop = close - stop_distance

        target = close + stop_distance * MIN_RR

    else:

        stop = close + stop_distance

        target = close - stop_distance * MIN_RR

    # Forex quote precision: JPY pairs usually use 3 decimals.

    pair = ticker.replace("=X", "")

    decimals = 3 if pair.endswith("JPY") else 5

    return {

        "ticker": ticker,

        "pair": pair[:3] + "/" + pair[3:],

        "direction": direction,

        "entry": round(close, decimals),

        "stop": round(stop, decimals),

        "target": round(target, decimals),

        "rr": MIN_RR,

        "rsi": round(rsi, 1),

        "score": score,

        "candle_time": str(m5.index[-1]),

        "atr": round(atr, decimals),

    }, "qualifies"

def main():

    log("Starting forex scan")

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:

        raise RuntimeError(

            "Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID GitHub secret"

        )

    state = load_state()

    checked = 0

    sent = 0

    errors = 0

    rejected = {}

    for ticker in PAIRS:

        checked += 1

        try:

            signal, reason = analyse_pair(ticker)

            if signal is None:

                rejected[reason] = rejected.get(reason, 0) + 1

                log(f"{ticker}: rejected — {reason}")

                continue

            # One alert per pair/direction/completed candle.

            key = (

                f"{signal['ticker']}|{signal['direction']}|"

                f"{signal['candle_time']}"

            )

            if state.get(key):

                log(f"{ticker}: duplicate skipped")

                continue

            message = (

                f"FOREX SETUP — M5\n"

                f"Pair: {signal['pair']}\n"

                f"Direction: {signal['direction']}\n"

                f"Entry reference: {signal['entry']}\n"

                f"Stop-loss: {signal['stop']}\n"

                f"Take-profit: {signal['target']}\n"

                f"Planned risk/reward: 1:{signal['rr']:.2f}\n"

                f"RSI: {signal['rsi']}\n"

                f"Quality filter score: {signal['score']}/6\n"

                f"M15/H1 trend: aligned\n"

                f"Trigger: recent completed M5 EMA 9/21 crossover\n"

                f"Candle time: {signal['candle_time']}\n"

                f"Data: {DATA_SOURCE}\n\n"

                f"Alert only — verify price and spread with your broker "

                f"before considering any trade. Entry is a reference, "

                f"not a guaranteed fill. Risk is not guaranteed."

            )

            if telegram_send(message):

                state[key] = True

                save_state(state)

                log_signal({

                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),

                    "pair": signal["pair"],

                    "direction": signal["direction"],

                    "entry_reference": signal["entry"],

                    "stop_loss": signal["stop"],

                    "take_profit": signal["target"],

                    "planned_rr": signal["rr"],

                    "rsi": signal["rsi"],

                    "score": signal["score"],

                    "candle_time": signal["candle_time"],

                    "data_source": DATA_SOURCE,

                    "outcome": "UNVERIFIED",

                })

                sent += 1

                log(f"Alert sent: {signal['pair']} {signal['direction']}")

            else:

                errors += 1

        except Exception as exc:

            errors += 1

            log(f"{ticker}: analysis error — {type(exc).__name__}: {exc}")

        # Avoid hammering the data service.

        time.sleep(0.3)

    summary = (

        f"Forex scan complete.\n"

        f"Pairs checked: {checked}\n"

        f"Qualifying alerts sent: {sent}\n"

        f"Data/analysis errors: {errors}\n"

        f"Rejected setups:\n"

    )

    if rejected:

        summary += "\n".join(

            f"{reason}: {count}"

            for reason, count in sorted(

                rejected.items(), key=lambda item: item[1], reverse=True

            )

        )

    else:

        summary += "None"

    summary += (

        "\n\nData source: Yahoo Finance; not broker-verified.\n"

        "No signal does not mean a trade should be forced."

    )

    log(summary)

    telegram_send(summary)

if __name__ == "__main__":

    main()
