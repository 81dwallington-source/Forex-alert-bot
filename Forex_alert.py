

import os

import time

from datetime import datetime, timezone

import pandas as pd

import requests

import yfinance as yf

# ==================================================

# STRICT FOREX ALERT BOT

# M5 entry | M15 + H1 confirmation | 1.5R target

# Yahoo Finance data is NOT a broker execution feed.

# ==================================================

PAIRS = [

    "EURUSD", "GBPUSD", "USDJPY", "USDCHF",

    "USDCAD", "AUDUSD", "NZDUSD",

    "EURGBP", "EURJPY", "EURCHF", "EURCAD",

    "EURAUD", "EURNZD",

    "GBPJPY", "GBPCHF", "GBPCAD", "GBPAUD", "GBPNZD",

    "AUDJPY", "AUDCHF", "AUDCAD", "AUDNZD",

    "CADJPY", "CADCHF", "NZDJPY", "NZDCHF", "CHFJPY",

]

MAX_M5_CANDLE_AGE_MINUTES = 7

REWARD_RISK = 1.5

ATR_MULTIPLIER = 1.5

MIN_M15_ADX = 18

MIN_M5_ADX = 16

MIN_ATR_PIPS = 1.0

MAX_ATR_PIPS = 30.0

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

STATS = {

    "checked": 0,

    "data_ok": 0,

    "stale": 0,

    "no_crossover": 0,

    "trend_rejected": 0,

    "momentum_rejected": 0,

    "volatility_rejected": 0,

    "signals": 0,

    "errors": 0,

}

def utc_now():

    return datetime.now(timezone.utc)

def send_telegram(message):

    if not TOKEN or not CHAT_ID:

        raise RuntimeError("Missing Telegram secrets.")

    response = requests.post(

        f"https://api.telegram.org/bot{TOKEN}/sendMessage",

        data={

            "chat_id": CHAT_ID,

            "text": message,

        },

        timeout=20,

    )

    response.raise_for_status()

    result = response.json()

    if not result.get("ok"):

        raise RuntimeError("Telegram rejected the message.")

    print("Telegram delivery confirmed.")

def get_candles(pair, interval, period, minutes):

    frame = yf.Ticker(pair + "=X").history(

        period=period,

        interval=interval,

        auto_adjust=False,

        actions=False,

    )

    if frame is None or frame.empty:

        raise ValueError(f"No {interval} data.")

    frame = frame.copy()

    frame = frame[~frame.index.duplicated(keep="last")]

    frame = frame.sort_index()

    frame = frame.dropna(

        subset=["Open", "High", "Low", "Close"]

    )

    if frame.index.tz is None:

        frame.index = frame.index.tz_localize("UTC")

    else:

        frame.index = frame.index.tz_convert("UTC")

    # Use completed candles only.

    now = pd.Timestamp.now(tz="UTC")

    candle_duration = pd.Timedelta(minutes=minutes)

    frame = frame[

        (frame.index + candle_duration) <= now

    ]

    if frame.empty:

        raise ValueError(f"No completed {interval} candles.")

    return frame

def add_indicators(frame):

    frame = frame.copy()

    close = frame["Close"]

    high = frame["High"]

    low = frame["Low"]

    previous_close = close.shift(1)

    frame["EMA9"] = close.ewm(

        span=9, adjust=False

    ).mean()

    frame["EMA21"] = close.ewm(

        span=21, adjust=False

    ).mean()

    frame["EMA20"] = close.ewm(

        span=20, adjust=False

    ).mean()

    frame["EMA50"] = close.ewm(

        span=50, adjust=False

    ).mean()

    true_range = pd.concat(

        [

            high - low,

            (high - previous_close).abs(),

            (low - previous_close).abs(),

        ],

        axis=1,

    ).max(axis=1)

    frame["ATR"] = true_range.ewm(

        alpha=1 / 14,

        adjust=False,

        min_periods=14,

    ).mean()

    # ADX: a trend-strength filter, not a direction predictor.

    up_move = high.diff()

    down_move = -low.diff()

    plus_dm = up_move.where(

        (up_move > down_move) & (up_move > 0), 0.0

    )

    minus_dm = down_move.where(

        (down_move > up_move) & (down_move > 0), 0.0

    )

    atr_smooth = true_range.ewm(

        alpha=1 / 14,

        adjust=False,

        min_periods=14,

    ).mean()

    plus_di = (

        100 * plus_dm.ewm(

            alpha=1 / 14, adjust=False, min_periods=14

        ).mean() / atr_smooth.replace(0, float("nan"))

    )

    minus_di = (

        100 * minus_dm.ewm(

            alpha=1 / 14, adjust=False, min_periods=14

        ).mean() / atr_smooth.replace(0, float("nan"))

    )

    dx = (

        100 * (plus_di - minus_di).abs()

        / (plus_di + minus_di).replace(0, float("nan"))

    )

    frame["ADX"] = dx.ewm(

        alpha=1 / 14,

        adjust=False,

        min_periods=14,

    ).mean()

    frame["PLUS_DI"] = plus_di

    frame["MINUS_DI"] = minus_di

    # RSI momentum check.

    change = close.diff()

    gain = change.clip(lower=0).ewm(

        alpha=1 / 14, adjust=False, min_periods=14

    ).mean()

    loss = (-change.clip(upper=0)).ewm(

        alpha=1 / 14, adjust=False, min_periods=14

    ).mean()

    rs = gain / loss.replace(0, float("nan"))

    frame["RSI"] = 100 - (100 / (1 + rs))

    return frame

def format_price(pair, value):

    decimals = 3 if pair.endswith("JPY") else 5

    return f"{value:.{decimals}f}"

def scan_pair(pair):

    try:

        m5 = add_indicators(

            get_candles(pair, "5m", "5d", 5)

        )

        m15 = add_indicators(

            get_candles(pair, "15m", "10d", 15)

        )

        h1 = add_indicators(

            get_candles(pair, "1h", "30d", 60)

        )

        STATS["data_ok"] += 1

        if len(m5) < 60 or len(m15) < 60 or len(h1) < 60:

            raise ValueError("Not enough candles for indicators.")

        now = pd.Timestamp.now(tz="UTC")

        last_time = m5.index[-1]

        age = (

            now - (last_time + pd.Timedelta(minutes=5))

        ).total_seconds() / 60

        if age < -0.1 or age > MAX_M5_CANDLE_AGE_MINUTES:

            STATS["stale"] += 1

            print(f"{pair}: rejected; stale M5 candle.")

            return None

        previous = m5.iloc[-2]

        current = m5.iloc[-1]

        trend15 = m15.iloc[-1]

        trend1h = h1.iloc[-1]

        needed = [

            current["EMA9"], current["EMA21"],

            current["EMA50"], current["ATR"],

            current["ADX"], current["RSI"],

            trend15["EMA20"], trend15["EMA50"],

            trend15["ADX"],

            trend1h["EMA20"], trend1h["EMA50"],

        ]

        if any(pd.isna(value) for value in needed):

            raise ValueError("Indicator values unavailable.")

        if current["ATR"] <= 0 or current["Close"] <= 0:

            raise ValueError("Invalid price or ATR.")

        # A new crossover on the latest completed M5 candle.

        buy_cross = (

            previous["EMA9"] <= previous["EMA21"]

            and current["EMA9"] > current["EMA21"]

        )

        sell_cross = (

            previous["EMA9"] >= previous["EMA21"]

            and current["EMA9"] < current["EMA21"]

        )

        if not buy_cross and not sell_cross:

            STATS["no_crossover"] += 1

            return None

        direction = "BUY" if buy_cross else "SELL"

        # Require M15 and H1 trend alignment.

        if direction == "BUY":

            aligned = (

                trend15["EMA20"] > trend15["EMA50"]

                and trend1h["EMA20"] > trend1h["EMA50"]

                and current["Close"] > current["EMA50"]

            )

        else:

            aligned = (

                trend15["EMA20"] < trend15["EMA50"]

                and trend1h["EMA20"] < trend1h["EMA50"]

                and current["Close"] < current["EMA50"]

            )

        if not aligned:

            STATS["trend_rejected"] += 1

            print(f"{pair}: crossover rejected; trend misaligned.")

            return None

        # Avoid weak momentum and low-strength trends.

        if (

            current["ADX"] < MIN_M5_ADX

            or trend15["ADX"] < MIN_M15_ADX

        ):

            STATS["momentum_rejected"] += 1

            print(f"{pair}: rejected; ADX too weak.")

            return None

        rsi = float(current["RSI"])

        if direction == "BUY":

            momentum_ok = (

                50 <= rsi <= 68

                and current["PLUS_DI"] > current["MINUS_DI"]

                and current["Close"] > current["Open"]

            )

        else:

            momentum_ok = (

                32 <= rsi <= 50

                and current["MINUS_DI"] > current["PLUS_DI"]

                and current["Close"] < current["Open"]

            )

        if not momentum_ok:

            STATS["momentum_rejected"] += 1

            print(f"{pair}: rejected; momentum not confirmed.")

            return None

        # Avoid unusually tiny or extreme volatility.

        pip_size = 0.01 if pair.endswith("JPY") else 0.0001

        atr_pips = float(current["ATR"]) / pip_size

        if not MIN_ATR_PIPS <= atr_pips <= MAX_ATR_PIPS:

            STATS["volatility_rejected"] += 1

            print(f"{pair}: rejected; ATR outside allowed range.")

            return None

        # Use only the latest closed candle as the reference entry.

        entry = float(current["Close"])

        distance = float(current["ATR"]) * ATR_MULTIPLIER

        if direction == "BUY":

            stop = entry - distance

            target = entry + distance * REWARD_RISK

        else:

            stop = entry + distance

            target = entry - distance * REWARD_RISK

        STATS["signals"] += 1

        return (

            "FOREX SETUP - 5m\n"

            f"Pair: {pair[:3]}/{pair[3:]}\n"

            f"Direction: {direction}\n"

            f"Entry reference: {format_price(pair, entry)}\n"

            f"Stop-loss: {format_price(pair, stop)}\n"

            f"Take-profit: {format_price(pair, target)}\n"

            f"Planned risk/reward: 1:{REWARD_RISK:.2f}\n"

            "5m trigger: fresh closed-candle EMA 9/21 crossover\n"

            "15m confirmation: EMA 20/50 trend aligned\n"

            "1h confirmation: EMA 20/50 trend aligned\n"

            f"M5 ADX: {current['ADX']:.1f}\n"

            f"M15 ADX: {trend15['ADX']:.1f}\n"

            f"RSI: {rsi:.1f}\n"

            f"ATR: {atr_pips:.1f} pips\n"

            f"Closed candle: {last_time.strftime('%Y-%m-%d %H:%M UTC')}\n"

            f"Alert generated: {utc_now().strftime('%Y-%m-%d %H:%M UTC')}\n"

            "Data source: Yahoo Finance, not a broker feed.\n"

            "Verify current price, spread and entry on your broker."

        )

    except Exception as error:

        STATS["errors"] += 1

        print(f"{pair}: scan/data error: {error}")

        return None

def main():

    print("Starting strict forex scan.")

    try:

        send_telegram("Forex alert bot: strict scan starting.")

    except Exception as error:

        print(f"Telegram start message failed: {error}")

    signals = []

    for pair in PAIRS:

        STATS["checked"] += 1

        signal = scan_pair(pair)

        if signal:

            signals.append(signal)

        time.sleep(0.5)

    for signal in signals:

        try:

            send_telegram(signal)

        except Exception as error:

            print(f"Signal delivery failed: {error}")

    summary = (

        "FOREX SCAN DIAGNOSTICS\n"

        f"Pairs checked: {STATS['checked']}\n"

        f"Pairs with usable data: {STATS['data_ok']}\n"

        f"Stale M5 candles rejected: {STATS['stale']}\n"

        f"No fresh crossover: {STATS['no_crossover']}\n"

        f"Trend rejected: {STATS['trend_rejected']}\n"

        f"Momentum rejected: {STATS['momentum_rejected']}\n"

        f"Volatility rejected: {STATS['volatility_rejected']}\n"

        f"Qualifying signals: {STATS['signals']}\n"

        f"Data/scan errors: {STATS['errors']}\n\n"

        "Strict filters are not proof of profitability.\n"

        "Yahoo Finance is not a broker feed; verify before trading."

    )

    print(summary)

    send_telegram(summary)

if __name__ == "__main__":

    main()
