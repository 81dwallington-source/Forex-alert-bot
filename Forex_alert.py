

import os

import time

import math

import requests

from datetime import datetime, timezone

# ============================================================

# FOREX ALERT BOT — QUALITY FILTERS

# Signal-only: no automatic trading.

# Data: Yahoo Finance chart API; not broker-verified.

# ============================================================

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

PAIRS = [

    "EURUSD=X", "GBPUSD=X", "USDJPY=X", "USDCHF=X",

    "USDCAD=X", "AUDUSD=X", "NZDUSD=X",

    "EURGBP=X", "EURJPY=X", "EURCHF=X", "EURCAD=X",

    "EURAUD=X", "EURNZD=X",

    "GBPJPY=X", "GBPCHF=X", "GBPCAD=X", "GBPAUD=X",

    "GBPNZD=X",

    "AUDJPY=X", "AUDCHF=X", "AUDCAD=X", "AUDNZD=X",

    "NZDJPY=X", "NZDCHF=X", "NZDCAD=X",

    "CADJPY=X", "CHFJPY=X",

]

MIN_RR = 1.5

MAX_CANDLE_ATR = 1.8

MIN_ATR_PCT = 0.015

MAX_ATR_PCT = 0.60

MAX_M5_AGE_MINUTES = 15

MIN_ADX = 18

MIN_RSI_BUY = 52

MAX_RSI_BUY = 68

MIN_RSI_SELL = 32

MAX_RSI_SELL = 48

SESSION = requests.Session()

SESSION.headers.update({"User-Agent": "Mozilla/5.0"})

def telegram(message):

    if not BOT_TOKEN or not CHAT_ID:

        print("Telegram secrets missing; message not sent.")

        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    try:

        response = SESSION.post(

            url,

            json={"chat_id": CHAT_ID, "text": message},

            timeout=15,

        )

        response.raise_for_status()

        result = response.json()

        if not result.get("ok"):

            print("Telegram rejected message:", result)

            return False

        return True

    except Exception as exc:

        print("Telegram error:", exc)

        return False

def fetch_candles(symbol, interval, range_value="5d"):

    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

    params = {

        "interval": interval,

        "range": range_value,

        "includePrePost": "false",

    }

    response = SESSION.get(url, params=params, timeout=20)

    response.raise_for_status()

    payload = response.json()

    chart = payload.get("chart", {})

    if chart.get("error"):

        raise ValueError(str(chart["error"]))

    results = chart.get("result")

    if not results:

        raise ValueError("No chart data returned")

    result = results[0]

    timestamps = result.get("timestamp") or []

    quote_list = result.get("indicators", {}).get("quote", [])

    if not quote_list:

        raise ValueError("No OHLC data")

    quote = quote_list[0]

    candles = []

    for i, stamp in enumerate(timestamps):

        try:

            o = quote["open"][i]

            h = quote["high"][i]

            low = quote["low"][i]

            c = quote["close"][i]

            if None in (o, h, low, c):

                continue

            if not all(math.isfinite(float(v)) for v in (o, h, low, c)):

                continue

            if h < low or h < max(o, c) or low > min(o, c):

                continue

            candles.append({

                "time": int(stamp),

                "open": float(o),

                "high": float(h),

                "low": float(low),

                "close": float(c),

            })

        except (IndexError, TypeError, ValueError, KeyError):

            continue

    candles.sort(key=lambda candle: candle["time"])

    # Remove the currently forming candle. Yahoo timestamps mark

    # the candle's opening time, so only use candles whose interval

    # has fully elapsed.

    interval_seconds = {

        "5m": 300,

        "15m": 900,

        "60m": 3600,

        "1h": 3600,

    }.get(interval, 300)

    now = int(time.time())

    candles = [

        candle for candle in candles

        if candle["time"] + interval_seconds <= now

    ]

    if len(candles) < 60:

        raise ValueError(f"Insufficient completed {interval} candles")

    return candles

def ema(values, period):

    if len(values) < period:

        return []

    alpha = 2 / (period + 1)

    result = [None] * (period - 1)

    current = sum(values[:period]) / period

    result.append(current)

    for value in values[period:]:

        current = alpha * value + (1 - alpha) * current

        result.append(current)

    return result

def rsi(values, period=14):

    if len(values) <= period:

        return None

    changes = [values[i] - values[i - 1] for i in range(1, len(values))]

    gains = [max(change, 0) for change in changes]

    losses = [max(-change, 0) for change in changes]

    avg_gain = sum(gains[:period]) / period

    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):

        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period

        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period

    if avg_loss == 0:

        return 100.0

    rs = avg_gain / avg_loss

    return 100 - (100 / (1 + rs))

def atr(candles, period=14):

    if len(candles) <= period:

        return None

    true_ranges = []

    for i in range(1, len(candles)):

        current = candles[i]

        previous_close = candles[i - 1]["close"]

        true_ranges.append(max(

            current["high"] - current["low"],

            abs(current["high"] - previous_close),

            abs(current["low"] - previous_close),

        ))

    value = sum(true_ranges[:period]) / period

    for tr in true_ranges[period:]:

        value = ((value * (period - 1)) + tr) / period

    return value

def adx(candles, period=14):

    if len(candles) < period * 3:

        return None

    trs, plus_dm, minus_dm = [], [], []

    for i in range(1, len(candles)):

        cur, prev = candles[i], candles[i - 1]

        up = cur["high"] - prev["high"]

        down = prev["low"] - cur["low"]

        plus_dm.append(up if up > down and up > 0 else 0.0)

        minus_dm.append(down if down > up and down > 0 else 0.0)

        trs.append(max(

            cur["high"] - cur["low"],

            abs(cur["high"] - prev["close"]),

            abs(cur["low"] - prev["close"]),

        ))

    dx_values = []

    for i in range(period - 1, len(trs)):

        start = i - period + 1

        tr_sum = sum(trs[start:i + 1])

        if tr_sum <= 0:

            continue

        plus = 100 * sum(plus_dm[start:i + 1]) / tr_sum

        minus = 100 * sum(minus_dm[start:i + 1]) / tr_sum

        denominator = plus + minus

        dx_values.append(

            0.0 if denominator == 0

            else 100 * abs(plus - minus) / denominator

        )

    if len(dx_values) < period:

        return None

    value = sum(dx_values[:period]) / period

    for dx in dx_values[period:]:

        value = ((value * (period - 1)) + dx) / period

    return value

def trend_direction(candles):

    closes = [c["close"] for c in candles]

    fast = ema(closes, 20)

    slow = ema(closes, 50)

    if not fast or not slow:

        return None

    if fast[-1] is None or slow[-1] is None:

        return None

    if fast[-1] > slow[-1] and closes[-1] > fast[-1]:

        return "BUY"

    if fast[-1] < slow[-1] and closes[-1] < fast[-1]:

        return "SELL"

    return None

def analyse_pair(symbol):

    m5 = fetch_candles(symbol, "5m", "5d")

    m15 = fetch_candles(symbol, "15m", "10d")

    h1 = fetch_candles(symbol, "60m", "30d")

    # Reject stale M5 data rather than inventing a current price.

    now = int(time.time())

    age_minutes = (now - m5[-1]["time"]) / 60

    if age_minutes > MAX_M5_AGE_MINUTES:

        raise ValueError(f"Stale M5 data ({age_minutes:.1f} min old)")

    closes = [c["close"] for c in m5]

    fast = ema(closes, 9)

    slow = ema(closes, 21)

    if len(closes) < 55 or fast[-2] is None or slow[-2] is None:

        raise ValueError("Not enough indicator history")

    # Require a fresh crossover on the latest completed M5 candle.

    crossed_up = fast[-2] <= slow[-2] and fast[-1] > slow[-1]

    crossed_down = fast[-2] >= slow[-2] and fast[-1] < slow[-1]

    if not crossed_up and not crossed_down:

        return None, "no fresh crossover"

    direction = "BUY" if crossed_up else "SELL"

    if trend_direction(m15) != direction:

        return None, "M15 trend not aligned"

    if trend_direction(h1) != direction:

        return None, "H1 trend not aligned"

    momentum = rsi(closes, 14)

    strength = adx(m5, 14)

    volatility = atr(m5, 14)

    if momentum is None or strength is None or volatility is None:

        return None, "indicator unavailable"

    if strength < MIN_ADX:

        return None, "weak trend strength"

    if direction == "BUY" and not (MIN_RSI_BUY <= momentum <= MAX_RSI_BUY):

        return None, "buy momentum outside range"

    if direction == "SELL" and not (MIN_RSI_SELL <= momentum <= MAX_RSI_SELL):

        return None, "sell momentum outside range"

    entry = m5[-1]["close"]

    candle_range = m5[-1]["high"] - m5[-1]["low"]

    if entry <= 0 or volatility <= 0:

        return None, "invalid price/volatility"

    atr_percent = (volatility / entry) * 100

    if not (MIN_ATR_PCT <= atr_percent <= MAX_ATR_PCT):

        return None, "volatility outside range"

    if candle_range > MAX_CANDLE_ATR * volatility:

        return None, "oversized signal candle"

    # ATR-based protective stop. TP is at least 1:1.5 RR.

    risk_distance = 1.2 * volatility

    reward_distance = risk_distance * MIN_RR

    if direction == "BUY":

        stop = entry - risk_distance

        target = entry + reward_distance

    else:

        stop = entry + risk_distance

        target = entry - reward_distance

    # Format precision based on the currency quote convention.

    # JPY pairs normally use 3 decimals; most others use 5.

    pair_name = symbol.replace("=X", "")

    decimals = 3 if pair_name.endswith("JPY") else 5

    candle_time = datetime.fromtimestamp(

        m5[-1]["time"], timezone.utc

    ).strftime("%Y-%m-%d %H:%M UTC")

    signal = {

        "pair": pair_name[:3] + "/" + pair_name[3:],

        "direction": direction,

        "entry": round(entry, decimals),

        "sl": round(stop, decimals),

        "tp": round(target, decimals),

        "rr": MIN_RR,

        "rsi": round(momentum, 1),

        "adx": round(strength, 1),

        "atr": round(volatility, decimals),

        "candle_time": candle_time,

        "signal_id": f"{symbol}:{direction}:{m5[-1]['time']}",

    }

    return signal, "qualified"

def main():

    print("Forex alert bot scan started.")

    print("Source: Yahoo Finance chart API; not broker-verified.")

    print("Timeframe: M5 entry, M15/H1 trend confirmation.")

    if not BOT_TOKEN or not CHAT_ID:

        print(

            "WARNING: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID "

            "is missing from the environment."

        )

    stats = {

        "checked": 0,

        "signals": 0,

        "reasons": {},

        "errors": 0,

    }

    for symbol in PAIRS:

        stats["checked"] += 1

        try:

            signal, reason = analyse_pair(symbol)

            if signal is None:

                stats["reasons"][reason] = (

                    stats["reasons"].get(reason, 0) + 1

                )

                print(f"{symbol}: rejected — {reason}")

                continue

            message = (

                "FOREX SETUP — 5m\n"

                f"Pair: {signal['pair']}\n"

                f"Direction: {signal['direction']}\n"

                f"Entry reference: {signal['entry']}\n"

                f"Stop-loss: {signal['sl']}\n"

                f"Take-profit: {signal['tp']}\n"

                f"Planned risk/reward: 1:{signal['rr']:.2f}\n"

                "5m trigger: fresh EMA 9/21 crossover\n"

                "15m/1h confirmation: EMA 20/50 trend aligned\n"

                f"RSI: {signal['rsi']} | ADX: {signal['adx']}\n"

                f"Closed candle: {signal['candle_time']}\n"

                "Data source: Yahoo Finance; not broker-verified.\n"

                "Check the live broker price and spread before entry."

            )

            if telegram(message):

                stats["signals"] += 1

                print(f"{signal['pair']} {signal['direction']}: alert sent")

            else:

                print(f"{signal['pair']}: Telegram alert failed")

        except Exception as exc:

            stats["errors"] += 1

            print(f"{symbol}: data/analysis error — {exc}")

    reasons = "\n".join(

        f"{reason}: {count}"

        for reason, count in sorted(stats["reasons"].items())

    ) or "None"

    summary = (

        "Forex scan complete.\n"

        f"Pairs checked: {stats['checked']}\n"

        f"Qualifying signals sent: {stats['signals']}\n"

        f"Data/analysis errors: {stats['errors']}\n"

        f"Rejected setups:\n{reasons}\n\n"

        "Data source: Yahoo Finance; not broker-verified.\n"

        "No signal does not mean a trade should be forced."

    )

    print(summary)

    telegram(summary)

if __name__ == "__main__":

    main()
