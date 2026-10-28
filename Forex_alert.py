

import os

import time

import math

import logging

from datetime import datetime, timezone

from urllib.parse import quote

import requests

# Forex Alert Bot — improved diagnostics

# Yahoo Finance data only. Not a broker feed.

# Alerts only: this script does not place trades.

PAIRS = [

    "EURUSD=X", "GBPUSD=X", "USDJPY=X", "USDCHF=X",

    "USDCAD=X", "AUDUSD=X", "NZDUSD=X",

    "EURGBP=X", "EURJPY=X", "EURCHF=X", "EURCAD=X",

    "EURAUD=X", "EURNZD=X", "GBPJPY=X", "GBPCHF=X",

    "GBPCAD=X", "GBPAUD=X", "GBPNZD=X",

    "AUDJPY=X", "AUDCHF=X", "AUDCAD=X", "AUDNZD=X",

    "NZDJPY=X", "NZDCHF=X", "NZDCAD=X",

    "CADJPY=X", "CADCHF=X"

]

MAX_M5_AGE_MINUTES = 7

MIN_M5_BARS = 80

MIN_M15_BARS = 60

MIN_H1_BARS = 60

MIN_ADX_M5 = 16.0

MIN_ADX_M15 = 18.0

MIN_ATR_PIPS = 1.5

MAX_ATR_PIPS = 35.0

RISK_REWARD = 1.5

ATR_SL_MULTIPLIER = 1.5

REQUEST_TIMEOUT = 15

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

SESSION = requests.Session()

SESSION.headers.update({

    "User-Agent": "Mozilla/5.0 ForexAlertBot/1.0"

})

logging.basicConfig(

    level=logging.INFO,

    format="%(asctime)s UTC %(levelname)s: %(message)s"

)

def telegram_send(message):

    if not BOT_TOKEN or not CHAT_ID:

        raise RuntimeError(

            "Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID secrets"

        )

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    response = SESSION.post(

        url,

        data={"chat_id": CHAT_ID, "text": message},

        timeout=REQUEST_TIMEOUT

    )

    response.raise_for_status()

    result = response.json()

    if not result.get("ok"):

        raise RuntimeError("Telegram rejected the message")

def fetch_candles(symbol, interval, range_value):

    url = (

        "https://query1.finance.yahoo.com/v8/finance/chart/"

        + quote(symbol, safe="")

    )

    response = SESSION.get(

        url,

        params={

            "interval": interval,

            "range": range_value,

            "includePrePost": "true"

        },

        timeout=REQUEST_TIMEOUT

    )

    response.raise_for_status()

    payload = response.json()

    chart = payload.get("chart", {})

    if chart.get("error"):

        raise RuntimeError(str(chart["error"]))

    results = chart.get("result") or []

    if not results:

        raise RuntimeError("Yahoo returned no chart data")

    result = results[0]

    timestamps = result.get("timestamp") or []

    quote_data = (

        result.get("indicators", {}).get("quote") or [{}]

    )[0]

    opens = quote_data.get("open") or []

    highs = quote_data.get("high") or []

    lows = quote_data.get("low") or []

    closes = quote_data.get("close") or []

    bars = []

    for i, stamp in enumerate(timestamps):

        if any(i >= len(values) for values in

               (opens, highs, lows, closes)):

            continue

        values = (opens[i], highs[i], lows[i], closes[i])

        if any(value is None for value in values):

            continue

        try:

            o, h, l, c = map(float, values)

        except (TypeError, ValueError):

            continue

        if not all(math.isfinite(v) for v in (o, h, l, c)):

            continue

        if min(o, h, l, c) <= 0 or h < l:

            continue

        bars.append({

            "time": int(stamp),

            "open": o,

            "high": h,

            "low": l,

            "close": c

        })

    seconds = {"5m": 300, "15m": 900, "60m": 3600}[interval]

    now = int(time.time())

    # Only use candles whose full interval has ended.

    bars = [

        bar for bar in bars

        if bar["time"] + seconds <= now

    ]

    if len(bars) < 3:

        raise RuntimeError(

            f"Too few completed valid candles: {len(bars)}"

        )

    return bars

def ema(values, period):

    if len(values) < period:

        return [None] * len(values)

    result = [None] * (period - 1)

    current = sum(values[:period]) / period

    result.append(current)

    alpha = 2.0 / (period + 1.0)

    for value in values[period:]:

        current = alpha * value + (1.0 - alpha) * current

        result.append(current)

    return result

def wilder_average(values, period):

    if len(values) < period:

        return [None] * len(values)

    result = [None] * (period - 1)

    current = sum(values[:period]) / period

    result.append(current)

    for value in values[period:]:

        current = (

            current * (period - 1) + value

        ) / period

        result.append(current)

    return result

def indicators(bars):

    closes = [b["close"] for b in bars]

    highs = [b["high"] for b in bars]

    lows = [b["low"] for b in bars]

    tr = []

    plus_dm = []

    minus_dm = []

    for i in range(len(bars)):

        if i == 0:

            tr.append(highs[i] - lows[i])

            plus_dm.append(0.0)

            minus_dm.append(0.0)

            continue

        tr.append(max(

            highs[i] - lows[i],

            abs(highs[i] - closes[i - 1]),

            abs(lows[i] - closes[i - 1])

        ))

        up = highs[i] - highs[i - 1]

        down = lows[i - 1] - lows[i]

        plus_dm.append(

            up if up > down and up > 0 else 0.0

        )

        minus_dm.append(

            down if down > up and down > 0 else 0.0

        )

    atr = wilder_average(tr, 14)

    sm_tr = wilder_average(tr, 14)

    sm_plus = wilder_average(plus_dm, 14)

    sm_minus = wilder_average(minus_dm, 14)

    dx = []

    for i in range(len(bars)):

        if (

            sm_tr[i] is None

            or sm_plus[i] is None

            or sm_minus[i] is None

            or sm_tr[i] == 0

        ):

            dx.append(None)

            continue

        pdi = 100 * sm_plus[i] / sm_tr[i]

        mdi = 100 * sm_minus[i] / sm_tr[i]

        denom = pdi + mdi

        dx.append(

            100 * abs(pdi - mdi) / denom if denom else 0.0

        )

    # Smooth valid DX values without treating missing values as zero.

    valid_dx = [v for v in dx if v is not None]

    adx_values = wilder_average(valid_dx, 14)

    adx = [None] * (len(dx) - len(adx_values)) + adx_values

    gains = [0.0]

    losses = [0.0]

    for i in range(1, len(closes)):

        change = closes[i] - closes[i - 1]

        gains.append(max(change, 0.0))

        losses.append(max(-change, 0.0))

    avg_gain = wilder_average(gains, 14)

    avg_loss = wilder_average(losses, 14)

    rsi = []

    for gain, loss in zip(avg_gain, avg_loss):

        if gain is None or loss is None:

            rsi.append(None)

        elif loss == 0:

            rsi.append(100.0)

        else:

            rs = gain / loss

            rsi.append(100.0 - 100.0 / (1.0 + rs))

    return {

        "close": closes,

        "ema9": ema(closes, 9),

        "ema21": ema(closes, 21),

        "ema20": ema(closes, 20),

        "ema50": ema(closes, 50),

        "atr": atr,

        "adx": adx,

        "rsi": rsi,

        "pdi": [

            100 * p / t if p is not None and t else None

            for p, t in zip(sm_plus, sm_tr)

        ],

        "mdi": [

            100 * m / t if m is not None and t else None

            for m, t in zip(sm_minus, sm_tr)

        ]

    }

def last_valid(values):

    for value in reversed(values):

        if value is not None:

            return value

    return None

def pip_size(symbol):

    pair = symbol.split("=")[0]

    return 0.01 if pair.endswith("JPY") else 0.0001

def analyse_pair(symbol, stats):

    stats["checked"] += 1

    try:

        m5 = fetch_candles(symbol, "5m", "5d")

        stats["m5_received"] += 1

        age = (

            time.time() - (m5[-1]["time"] + 300)

        ) / 60.0

        if age > MAX_M5_AGE_MINUTES:

            stats["stale"] += 1

            logging.info("%s rejected: stale M5 candle (%.1f min)",

                         symbol, age)

            return None

        if len(m5) < MIN_M5_BARS:

            stats["short_history"] += 1

            return None

        m15 = fetch_candles(symbol, "15m", "5d")

        h1 = fetch_candles(symbol, "60m", "1mo")

        if (

            len(m15) < MIN_M15_BARS

            or len(h1) < MIN_H1_BARS

        ):

            stats["short_history"] += 1

            return None

        stats["usable"] += 1

        i5 = indicators(m5)

        i15 = indicators(m15)

        i1 = indicators(h1)

        e9, e21 = i5["ema9"], i5["ema21"]

        if any(v is None for v in

               (e9[-1], e9[-2], e21[-1], e21[-2])):

            stats["indicator_errors"] += 1

            return None

        bullish = e9[-2] <= e21[-2] and e9[-1] > e21[-1]

        bearish = e9[-2] >= e21[-2] and e9[-1] < e21[-1]

        if not bullish and not bearish:

            stats["no_cross"] += 1

            return None

        direction = "BUY" if bullish else "SELL"

        close = i5["close"][-1]

        atr = last_valid(i5["atr"])

        adx5 = last_valid(i5["adx"])

        adx15 = last_valid(i15["adx"])

        rsi = last_valid(i5["rsi"])

        m15c = i15["close"][-1]

        m15e20 = last_valid(i15["ema20"])

        m15e50 = last_valid(i15["ema50"])

        h1c = i1["close"][-1]

        h1e20 = last_valid(i1["ema20"])

        h1e50 = last_valid(i1["ema50"])

        e50 = last_valid(i5["ema50"])

        pdi = last_valid(i5["pdi"])

        mdi = last_valid(i5["mdi"])

        if any(v is None for v in (

            atr, adx5, adx15, rsi, m15e20, m15e50,

            h1e20, h1e50, e50, pdi, mdi

        )):

            stats["indicator_errors"] += 1

            return None

        atr_pips = atr / pip_size(symbol)

        if not MIN_ATR_PIPS <= atr_pips <= MAX_ATR_PIPS:

            stats["volatility_rejected"] += 1

            return None

        if adx5 < MIN_ADX_M5 or adx15 < MIN_ADX_M15:

            stats["momentum_rejected"] += 1

            return None

        if direction == "BUY":

            trend_ok = (

                m15c > m15e20 > m15e50

                and h1c > h1e20 > h1e50

                and close > e50

            )

            momentum_ok = 50 <= rsi <= 68 and pdi > mdi

        else:

            trend_ok = (

                m15c < m15e20 < m15e50

                and h1c < h1e20 < h1e50

                and close < e50

            )

            momentum_ok = 32 <= rsi <= 50 and mdi > pdi

        if not trend_ok:

            stats["trend_rejected"] += 1

            return None

        if not momentum_ok:

            stats["momentum_rejected"] += 1

            return None

        last_bar = m5[-1]

        candle_range = last_bar["high"] - last_bar["low"]

        if candle_range > 1.8 * atr:

            stats["candle_rejected"] += 1

            return None

        risk = ATR_SL_MULTIPLIER * atr

        if direction == "BUY":

            sl = close - risk

            tp = close + risk * RISK_REWARD

        else:

            sl = close + risk

            tp = close - risk * RISK_REWARD

        pair = symbol.replace("=X", "")

        candle_time = datetime.fromtimestamp(

            m5[-1]["time"], timezone.utc

        ).strftime("%Y-%m-%d %H:%M UTC")

        stats["signals"] += 1

        return (

            f"FOREX SETUP — 5m\n"

            f"Pair: {pair}\n"

            f"Direction: {direction}\n"

            f"Entry reference: {close:.5f}\n"

            f"Stop-loss: {sl:.5f}\n"

            f"Take-profit: {tp:.5f}\n"

            f"Planned risk/reward: 1:{RISK_REWARD:.2f}\n"

            f"Trigger: completed M5 EMA 9/21 crossover\n"

            f"M15/H1 trend: aligned\n"

            f"RSI: {rsi:.1f}; ADX M5/M15: {adx5:.1f}/{adx15:.1f}\n"

            f"Closed candle: {candle_time}\n"

            f"Data: Yahoo Finance, not broker-verified. "

            f"Confirm price and spread on MT4."

        )

    except requests.RequestException as exc:

        stats["fetch_errors"] += 1

        logging.warning("%s request failed: %s", symbol, exc)

    except (ValueError, KeyError, IndexError, TypeError, RuntimeError) as exc:

        stats["data_errors"] += 1

        logging.warning("%s data error: %s", symbol, exc)

    except Exception:

        stats["unexpected_errors"] += 1

        logging.exception("Unexpected error for %s", symbol)

    return None

def main():

    if not BOT_TOKEN or not CHAT_ID:

        raise RuntimeError(

            "Add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID "

            "to GitHub Actions secrets"

        )

    telegram_send(

        "Forex alert bot scan started. "

        "Strict M5/M15/H1 filters active."

    )

    stats = {key: 0 for key in [

        "checked", "m5_received", "usable", "stale",

        "short_history", "no_cross", "trend_rejected",

        "momentum_rejected", "volatility_rejected",

        "candle_rejected", "indicator_errors",

        "fetch_errors", "data_errors", "unexpected_errors",

        "signals"

    ]}

    signals = []

    for symbol in PAIRS:

        result = analyse_pair(symbol, stats)

        if result:

            signals.append(result)

        time.sleep(0.15)

    for signal in signals:

        telegram_send(signal)

        time.sleep(0.4)

    summary = (

        "Forex scan complete.\n"

        f"Pairs checked: {stats['checked']}\n"

        f"M5 data received: {stats['m5_received']}\n"

        f"Pairs with usable M5/M15/H1 data: {stats['usable']}\n"

        f"Qualifying signals: {stats['signals']}\n"

        f"Rejected — stale M5: {stats['stale']}\n"

        f"Rejected — short history: {stats['short_history']}\n"

        f"Rejected — no fresh crossover: {stats['no_cross']}\n"

        f"Rejected — trend: {stats['trend_rejected']}\n"

        f"Rejected — momentum: {stats['momentum_rejected']}\n"

        f"Rejected — volatility: {stats['volatility_rejected']}\n"

        f"Rejected — oversized candle: {stats['candle_rejected']}\n"

        f"Indicator issues: {stats['indicator_errors']}\n"

        f"Data request errors: {stats['fetch_errors']}\n"

        f"Other data errors: {stats['data_errors']}\n"

        f"Unexpected errors: {stats['unexpected_errors']}\n"

        "Stale/incomplete M5 candles are rejected.\n"

        "Data source: Yahoo Finance, not a broker feed. "

        "Verify entries on MT4."

    )

    telegram_send(summary)

    logging.info(summary.replace("\n", " | "))

if __name__ == "__main__":

    main()
