

import os

import time

import requests

from datetime import datetime, timezone

# ============================================================

# FOREX ALERT BOT - QUALITY FILTERED M5 VERSION

# Signals only. Does not place trades.

# Data: Yahoo Finance; broker prices/spread are not verified.

# ============================================================

BOT_TOKEN = (

    os.getenv("TELEGRAM_BOT_TOKEN")

    or os.getenv("BOT_TOKEN")

)

CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID")

MIN_RR = 1.5

MAX_CANDLE_AGE_MINUTES = 10

REQUEST_TIMEOUT = 15

PAIRS = [

    "EURUSD", "GBPUSD", "USDJPY", "USDCHF",

    "USDCAD", "AUDUSD", "NZDUSD",

    "EURGBP", "EURJPY", "EURCHF", "EURCAD",

    "EURAUD", "EURNZD",

    "GBPJPY", "GBPCHF", "GBPCAD", "GBPAUD", "GBPNZD",

    "CHFJPY", "CADJPY", "AUDJPY", "NZDJPY",

    "CADCHF", "AUDCHF", "NZDCHF",

    "AUDCAD", "NZDCAD", "AUDNZD"

]

SESSION = requests.Session()

SESSION.headers.update({"User-Agent": "Mozilla/5.0"})

def log(message):

    print(f"[{datetime.now(timezone.utc).isoformat()}] {message}")

def yahoo_candles(pair, interval, period="5d"):

    """Download candles and discard the currently forming candle."""

    symbol = pair + "=X"

    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

    response = SESSION.get(

        url,

        params={"range": period, "interval": interval},

        timeout=REQUEST_TIMEOUT,

    )

    response.raise_for_status()

    result = response.json()["chart"]["result"]

    if not result:

        raise ValueError("No chart data returned")

    chart = result[0]

    timestamps = chart.get("timestamp") or []

    quote = chart["indicators"]["quote"][0]

    candles = []

    seconds = {"5m": 300, "15m": 900, "60m": 3600}[interval]

    now = time.time()

    for i, stamp in enumerate(timestamps):

        values = [

            quote[field][i]

            for field in ("open", "high", "low", "close")

        ]

        if any(v is None for v in values):

            continue

        # Only use candles that should have fully closed.

        if stamp + seconds > now:

            continue

        o, h, low, close = map(float, values)

        if min(o, h, low, close) <= 0 or h < low:

            continue

        candles.append({

            "time": int(stamp),

            "open": o,

            "high": h,

            "low": low,

            "close": close,

        })

    if len(candles) < 60:

        raise ValueError(f"Insufficient {interval} candles")

    age = now - (candles[-1]["time"] + seconds)

    if age > MAX_CANDLE_AGE_MINUTES * 60:

        raise ValueError(

            f"Stale {interval} candles: latest closed candle "

            f"is {age / 60:.1f} minutes old"

        )

    return candles

def ema(values, period):

    if len(values) < period:

        raise ValueError("Not enough values for EMA")

    alpha = 2.0 / (period + 1)

    result = [sum(values[:period]) / period]

    for value in values[period:]:

        result.append(alpha * value + (1 - alpha) * result[-1])

    return [None] * (period - 1) + result

def atr(candles, period=14):

    ranges = []

    for i in range(1, len(candles)):

        current = candles[i]

        previous_close = candles[i - 1]["close"]

        ranges.append(max(

            current["high"] - current["low"],

            abs(current["high"] - previous_close),

            abs(current["low"] - previous_close),

        ))

    if len(ranges) < period:

        raise ValueError("Not enough candles for ATR")

    return sum(ranges[-period:]) / period

def aligned_trend(candles, direction):

    closes = [c["close"] for c in candles]

    fast = ema(closes, 20)

    slow = ema(closes, 50)

    if fast[-1] is None or slow[-1] is None:

        return False

    if direction == "BUY":

        return fast[-1] > slow[-1] and closes[-1] > fast[-1]

    return fast[-1] < slow[-1] and closes[-1] < fast[-1]

def find_setup(pair):

    m5 = yahoo_candles(pair, "5m")

    m15 = yahoo_candles(pair, "15m")

    h1 = yahoo_candles(pair, "60m", "10d")

    closes = [c["close"] for c in m5]

    fast = ema(closes, 9)

    slow = ema(closes, 21)

    trend = ema(closes, 50)

    i = len(m5) - 1

    # Require a fresh EMA crossover on the latest closed M5 candle.

    crossed_up = (

        fast[i - 1] <= slow[i - 1]

        and fast[i] > slow[i]

    )

    crossed_down = (

        fast[i - 1] >= slow[i - 1]

        and fast[i] < slow[i]

    )

    if crossed_up:

        direction = "BUY"

    elif crossed_down:

        direction = "SELL"

    else:

        return None

    close = closes[-1]

    # Require M5 trend and both higher-timeframe trends to agree.

    if direction == "BUY":

        if close <= trend[-1]:

            return None

    else:

        if close >= trend[-1]:

            return None

    if not aligned_trend(m15, direction):

        return None

    if not aligned_trend(h1, direction):

        return None

    volatility = atr(m5)

    if volatility <= 0:

        return None

    # Structural stop beyond the recent five-candle swing,

    # with an additional volatility buffer.

    recent = m5[-6:-1]

    buffer = volatility * 0.25

    if direction == "BUY":

        sl = min(c["low"] for c in recent) - buffer

        risk = close - sl

        tp = close + MIN_RR * risk

    else:

        sl = max(c["high"] for c in recent) + buffer

        risk = sl - close

        tp = close - MIN_RR * risk

    if risk <= 0 or tp <= 0:

        return None

    # Reject stops that are extremely wide relative to volatility.

    if risk > 3.0 * volatility:

        return None

    rr = abs(tp - close) / risk

    if rr < MIN_RR:

        return None

    return {

        "pair": pair,

        "direction": direction,

        "entry": close,

        "sl": sl,

        "tp": tp,

        "rr": rr,

        "candle_time": m5[-1]["time"],

        "atr": volatility,

    }

def format_price(value):

    return f"{value:.5f}"

def send_telegram(message):

    if not BOT_TOKEN or not CHAT_ID:

        raise RuntimeError(

            "Telegram secrets missing. Check GitHub Actions secrets."

        )

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    response = SESSION.post(

        url,

        json={"chat_id": CHAT_ID, "text": message},

        timeout=REQUEST_TIMEOUT,

    )

    response.raise_for_status()

    if not response.json().get("ok"):

        raise RuntimeError("Telegram rejected the message")

def main():

    if not BOT_TOKEN or not CHAT_ID:

        raise RuntimeError("Telegram token/chat ID not configured")

    log("Starting M5 quality-filtered scan")

    signals = 0

    errors = 0

    for pair in PAIRS:

        try:

            setup = find_setup(pair)

            if not setup:

                continue

            candle_time = datetime.fromtimestamp(

                setup["candle_time"], timezone.utc

            ).strftime("%Y-%m-%d %H:%M UTC")

            message = (

                "FOREX SETUP - 5m\n"

                f"Pair: {setup['pair'][:3]}/{setup['pair'][3:]}\n"

                f"Direction: {setup['direction']}\n"

                f"Entry reference: {format_price(setup['entry'])}\n"

                f"Stop-loss: {format_price(setup['sl'])}\n"

                f"Take-profit: {format_price(setup['tp'])}\n"

                f"Planned risk/reward: 1:{setup['rr']:.2f}\n"

                "5m trigger: fresh EMA 9/21 crossover\n"

                "15m confirmation: aligned EMA 20/50 trend\n"

                "1h trend: aligned EMA 20/50 trend\n"

                f"Closed candle: {candle_time}\n"

                "Data source: Yahoo Finance, not broker-verified.\n"

                "Check Pepperstone MT4 price and spread before entry. "

                "No trade is guaranteed."

            )

            send_telegram(message)

            signals += 1

            log(f"Signal sent: {pair} {setup['direction']}")

        except Exception as exc:

            errors += 1

            log(f"{pair}: skipped - {exc}")

    log(

        f"Scan complete. Signals sent: {signals}. "

        f"Pairs skipped/errors: {errors}."

    )

if __name__ == "__main__":

    main()

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

INTERVALS = {

    "5m": {"range": "5d", "seconds": 300, "max_age": 1200},

    "15m": {"range": "5d", "seconds": 900, "max_age": 2700},

    "1h": {"range": "1mo", "seconds": 3600, "max_age": 9000},

}

def telegram(message):

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:

        raise ValueError("Telegram secrets are missing")

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    response = session.post(

        url,

        json={"chat_id": TELEGRAM_CHAT_ID, "text": message},

        timeout=15,

    )

    response.raise_for_status()

    if not response.json().get("ok"):

        raise ValueError("Telegram rejected the message")

def get_candles(pair, interval):

    if interval not in INTERVALS:

        raise ValueError("Unsupported candle interval")

    settings = INTERVALS[interval]

    url = (

        "https://query1.finance.yahoo.com/v8/finance/chart/"

        f"{pair}?range={settings['range']}&interval={interval}"

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

    quotes_list = item.get("indicators", {}).get("quote", [])

    if not timestamps or not quotes_list:

        raise ValueError("Market candles are missing")

    quotes = quotes_list[0]

    closes = quotes.get("close") or []

    highs = quotes.get("high") or []

    lows = quotes.get("low") or []

    candles = []

    for i, stamp in enumerate(timestamps):

        if i >= len(closes) or i >= len(highs) or i >= len(lows):

            continue

        close, high, low = closes[i], highs[i], lows[i]

        if close is None or high is None or low is None:

            continue

        close, high, low = float(close), float(high), float(low)

        if not (0 < low <= close <= high):

            continue

        candles.append({

            "time": int(stamp),

            "close": close,

            "high": high,

            "low": low,

        })

    candles.sort(key=lambda candle: candle["time"])

    if len(candles) < 60:

        raise ValueError("Not enough valid candles")

    now = datetime.now(timezone.utc).timestamp()

    latest_age = now - candles[-1]["time"]

    if latest_age < -60 or latest_age > settings["max_age"]:

        raise ValueError("Market data is stale - no signal allowed")

    # Exclude the potentially forming candle.

    closed = candles[:-1]

    if len(closed) < 50:

        raise ValueError("Not enough closed candles")

    # Ensure the most recent CLOSED candle is reasonably fresh too.

    closed_age = now - closed[-1]["time"]

    if closed_age < 0 or closed_age > settings["max_age"]:

        raise ValueError("Last closed candle is stale")

    return closed

def ema(values, period):

    if len(values) < period:

        raise ValueError("Not enough values for EMA")

    alpha = 2 / (period + 1)

    result = [values[0]]

    for value in values[1:]:

        result.append(alpha * value + (1 - alpha) * result[-1])

    return result

def aligned_trend(candles, direction):

    closes = [c["close"] for c in candles]

    fast = ema(closes, 9)

    slow = ema(closes, 21)

    if direction == "BUY":

        return (

            fast[-1] > slow[-1]

            and fast[-1] > fast[-4]

            and slow[-1] >= slow[-4]

            and closes[-1] > fast[-1]

        )

    return (

        fast[-1] < slow[-1]

        and fast[-1] < fast[-4]

        and slow[-1] <= slow[-4]

        and closes[-1] < fast[-1]

    )

def find_fresh_crossover(candles):

    closes = [c["close"] for c in candles]

    fast = ema(closes, 9)

    slow = ema(closes, 21)

    # Only consider a crossover within the last three CLOSED candles.

    for i in range(len(candles) - 1, max(0, len(candles) - 4), -1):

        if i < 1:

            continue

        if fast[i - 1] <= slow[i - 1] and fast[i] > slow[i]:

            if closes[-1] > fast[-1] and fast[-1] > slow[-1]:

                return "BUY"

        if fast[i - 1] >= slow[i - 1] and fast[i] < slow[i]:

            if closes[-1] < fast[-1] and fast[-1] < slow[-1]:

                return "SELL"

    return None

def pair_name(pair):

    symbol = pair.replace("=X", "")

    return symbol[:3] + "/" + symbol[3:]

def price_digits(pair):

    return 3 if "JPY" in pair else 5

def scan_pair(pair):

    entry_candles = get_candles(pair, "5m")

    direction = find_fresh_crossover(entry_candles)

    if not direction:

        return None

    confirmation = get_candles(pair, "15m")

    context = get_candles(pair, "1h")

    if not aligned_trend(confirmation, direction):

        return None

    if not aligned_trend(context, direction):

        return None

    candle = entry_candles[-1]

    previous = entry_candles[-2]

    # Reject unusually weak candles and obvious indecision.

    candle_range = candle["high"] - candle["low"]

    if candle_range <= 0:

        return None

    body = abs(candle["close"] - previous["close"])

    if body / candle_range < 0.25:

        return None

    recent = entry_candles[-15:]

    average_range = sum(

        c["high"] - c["low"] for c in recent

    ) / len(recent)

    if average_range <= 0:

        return None

    entry = candle["close"]

    # Place the stop beyond recent structure with a volatility buffer.

    if direction == "BUY":

        swing_low = min(c["low"] for c in entry_candles[-10:])

        sl = swing_low - average_range * 0.2

        risk = entry - sl

    else:

        swing_high = max(c["high"] for c in entry_candles[-10:])

        sl = swing_high + average_range * 0.2

        risk = sl - entry

    if risk <= 0:

        return None

    # Reject stops that are implausibly tight or too wide.

    if risk < average_range * 0.5:

        return None

    if risk > average_range * 3:

        return None

    # Require a minimum planned reward-to-risk ratio of 1.5.

    if direction == "BUY":

        tp = entry + risk * 1.5

    else:

        tp = entry - risk * 1.5

    digits = price_digits(pair)

    candle_time = datetime.fromtimestamp(

        candle["time"], timezone.utc

    ).strftime("%Y-%m-%d %H:%M UTC")

    return (

        "FOREX SETUP - 5m\n"

        f"Pair: {pair_name(pair)}\n"

        f"Direction: {direction}\n"

        f"Entry reference: {entry:.{digits}f}\n"

        f"Stop-loss: {sl:.{digits}f}\n"

        f"Take-profit: {tp:.{digits}f}\n"

        "Planned risk/reward: 1:1.5\n"

        "5m trigger: EMA crossover\n"

        "15m confirmation: aligned\n"

        "1h trend: aligned\n"

        f"Closed candle: {candle_time}\n"

        "Data source: Yahoo Finance, not broker-verified.\n"

        "Check Pepperstone MT4 price and spread before entry. "

        "No trade is guaranteed."

    )

def main():

    print("Forex alert bot started")

    print(datetime.now(timezone.utc).isoformat())

    telegram(

        "Forex alert bot started.\n"

        "Scanning 20 pairs.\n"

        "5m entry + 15m confirmation + 1h context.\n"

        "Yahoo Finance data; not broker-verified."

    )

    for pair in PAIRS:

        try:

            message = scan_pair(pair)

            if message:

                print(message)

                telegram(message)

            else:

                print(f"No qualified setup: {pair}")

        except Exception as error:

            print(f"Scan failed: {pair}: {error}")

        time.sleep(1)

    print("Scan complete")

if __name__ == "__main__":

    main()
