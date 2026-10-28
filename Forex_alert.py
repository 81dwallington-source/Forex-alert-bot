

"""

Selective Forex Telegram Alert Bot

M5 entries with M15/H1 confirmation. Alerts only; never places trades.

Yahoo Finance data is not broker-verified.

"""

import os

import json

import logging

from pathlib import Path

from datetime import datetime, timezone

import requests

import pandas as pd

import yfinance as yf

PAIRS = [

    "EURUSD=X", "GBPUSD=X", "USDJPY=X", "USDCHF=X", "USDCAD=X",

    "AUDUSD=X", "NZDUSD=X", "EURGBP=X", "EURJPY=X", "EURCHF=X",

    "EURCAD=X", "EURAUD=X", "EURNZD=X", "GBPJPY=X", "GBPCHF=X",

    "GBPCAD=X", "GBPAUD=X", "GBPNZD=X", "CHFJPY=X", "CADJPY=X",

    "AUDJPY=X", "NZDJPY=X", "CADCHF=X", "AUDCAD=X", "AUDCHF=X",

    "AUDNZD=X", "NZDCAD=X", "NZDCHF=X",

]

MIN_RR = 1.5

MAX_M5_AGE_MINUTES = 15

MAX_CANDLE_ATR = 1.8

MIN_ATR_FRACTION = 0.00008

MAX_ATR_FRACTION = 0.004

MIN_SIGNAL_SCORE = 5

STATE_FILE = Path("signals_state.json")

LOG_FILE = Path("signal_log.csv")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")

CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID")

logging.basicConfig(

    level=logging.INFO,

    format="%(asctime)s %(levelname)s %(message)s",

)

def telegram(message):

    if not BOT_TOKEN or not CHAT_ID:

        logging.error("Missing Telegram bot token or chat ID.")

        return False

    try:

        response = requests.post(

            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",

            json={"chat_id": CHAT_ID, "text": message},

            timeout=15,

        )

        response.raise_for_status()

        return bool(response.json().get("ok"))

    except Exception as exc:

        logging.error("Telegram send failed: %s", exc)

        return False

def load_state():

    try:

        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))

        if isinstance(data, dict):

            data.setdefault("sent", [])

            data.setdefault("open_estimates", [])

            return data

    except (OSError, ValueError):

        pass

    return {"sent": [], "open_estimates": []}

def save_state(state):

    STATE_FILE.write_text(

        json.dumps(state, indent=2),

        encoding="utf-8",

    )

def download_candles(symbol, interval, period):

    try:

        df = yf.download(

            symbol,

            period=period,

            interval=interval,

            auto_adjust=False,

            progress=False,

            threads=False,

            timeout=20,

        )

    except Exception as exc:

        logging.warning("%s %s download failed: %s", symbol, interval, exc)

        return None

    if df is None or df.empty:

        return None

    if isinstance(df.columns, pd.MultiIndex):

        df.columns = df.columns.get_level_values(0)

    df = df.loc[:, ~df.columns.duplicated()]

    needed = ["Open", "High", "Low", "Close"]

    if any(col not in df.columns for col in needed):

        return None

    df = df[needed].dropna()

    df = df[~df.index.duplicated(keep="last")].sort_index()

    if len(df) < 5:

        return None

    # Exclude the newest potentially unfinished candle.

    return df.iloc[:-1].copy()

def indicators(df):

    out = df.copy()

    close = out["Close"]

    out["ema9"] = close.ewm(span=9, adjust=False).mean()

    out["ema20"] = close.ewm(span=20, adjust=False).mean()

    out["ema21"] = close.ewm(span=21, adjust=False).mean()

    out["ema50"] = close.ewm(span=50, adjust=False).mean()

    delta = close.diff()

    gain = delta.clip(lower=0)

    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(

        alpha=1 / 14, min_periods=14, adjust=False

    ).mean()

    avg_loss = loss.ewm(

        alpha=1 / 14, min_periods=14, adjust=False

    ).mean()

    rs = avg_gain / avg_loss.replace(0, float("nan"))

    out["rsi"] = 100 - (100 / (1 + rs))

    previous_close = close.shift(1)

    true_range = pd.concat(

        [

            out["High"] - out["Low"],

            (out["High"] - previous_close).abs(),

            (out["Low"] - previous_close).abs(),

        ],

        axis=1,

    ).max(axis=1)

    out["atr"] = true_range.ewm(

        alpha=1 / 14,

        min_periods=14,

        adjust=False,

    ).mean()

    return out.dropna()

def get_trend(df):

    if df is None:

        return None

    ind = indicators(df)

    if len(ind) < 55:

        return None

    row = ind.iloc[-1]

    if row["ema20"] > row["ema50"] and row["Close"] > row["ema50"]:

        return "BUY"

    if row["ema20"] < row["ema50"] and row["Close"] < row["ema50"]:

        return "SELL"

    return None

def candle_age_minutes(timestamp):

    ts = pd.Timestamp(timestamp)

    if ts.tzinfo is None:

        ts = ts.tz_localize("UTC")

    else:

        ts = ts.tz_convert("UTC")

    candle_close = ts + pd.Timedelta(minutes=5)

    return (

        pd.Timestamp.now(tz="UTC") - candle_close

    ).total_seconds() / 60

def evaluate_pair(symbol):

    m5_raw = download_candles(symbol, "5m", "5d")

    m15_raw = download_candles(symbol, "15m", "10d")

    h1_raw = download_candles(symbol, "60m", "30d")

    if m5_raw is None or m15_raw is None or h1_raw is None:

        return None, "insufficient data"

    age = candle_age_minutes(m5_raw.index[-1])

    if age < -1 or age > MAX_M5_AGE_MINUTES:

        return None, "stale M5 data"

    m5 = indicators(m5_raw)

    if len(m5) < 55:

        return None, "short M5 history"

    m15_trend = get_trend(m15_raw)

    h1_trend = get_trend(h1_raw)

    if not m15_trend or not h1_trend or m15_trend != h1_trend:

        return None, "M15/H1 not aligned"

    direction = h1_trend

    previous = m5.iloc[-2]

    current = m5.iloc[-1]

    crossed_up = (

        previous["ema9"] <= previous["ema21"]

        and current["ema9"] > current["ema21"]

    )

    crossed_down = (

        previous["ema9"] >= previous["ema21"]

        and current["ema9"] < current["ema21"]

    )

    # Entry trigger: fresh crossover OR confirmed trend pullback.

    if direction == "BUY":

        fresh_trigger = crossed_up

        pullback_trigger = (

            previous["Low"] <= previous["ema21"] * 1.0005

            and current["Close"] > current["ema9"]

            and current["Close"] > previous["High"]

        )

    else:

        fresh_trigger = crossed_down

        pullback_trigger = (

            previous["High"] >= previous["ema21"] * 0.9995

            and current["Close"] < current["ema9"]

            and current["Close"] < previous["Low"]

        )

    if not (fresh_trigger or pullback_trigger):

        return None, "no crossover or confirmed pullback"

    entry = float(current["Close"])

    atr = float(current["atr"])

    rsi = float(current["rsi"])

    candle_range = float(current["High"] - current["Low"])

    if entry <= 0 or atr <= 0:

        return None, "invalid price or ATR"

    atr_fraction = atr / entry

    if not MIN_ATR_FRACTION <= atr_fraction <= MAX_ATR_FRACTION:

        return None, "volatility outside bounds"

    if candle_range > MAX_CANDLE_ATR * atr:

        return None, "oversized signal candle"

    if direction == "BUY" and not 50 <= rsi <= 68:

        return None, "buy RSI outside preferred range"

    if direction == "SELL" and not 32 <= rsi <= 50:

        return None, "sell RSI outside preferred range"

    score = 0

    reasons = []

    score += 1

    reasons.append("M15 trend aligned")

    score += 1

    reasons.append("H1 trend aligned")

    if (

        direction == "BUY" and current["Close"] > current["ema50"]

    ) or (

        direction == "SELL" and current["Close"] < current["ema50"]

    ):

        score += 1

        reasons.append("M5 trend aligned")

    momentum_ok = (

        53 <= rsi <= 64

        if direction == "BUY"

        else 36 <= rsi <= 47

    )

    if momentum_ok:

        score += 1

        reasons.append("momentum supportive")

    if pullback_trigger:

        score += 1

        reasons.append("confirmed pullback")

    if fresh_trigger:

        score += 1

        reasons.append("fresh EMA crossover")

    if score < MIN_SIGNAL_SCORE:

        return None, f"quality score {score}/{MIN_SIGNAL_SCORE}"

    recent = m5.tail(6)

    if direction == "BUY":

        swing_low = float(recent["Low"].min())

        stop = min(entry - 1.2 * atr, swing_low - 0.1 * atr)

        risk = entry - stop

        target = entry + MIN_RR * risk

    else:

        swing_high = float(recent["High"].max())

        stop = max(entry + 1.2 * atr, swing_high + 0.1 * atr)

        risk = stop - entry

        target = entry - MIN_RR * risk

    if risk <= 0 or target <= 0:

        return None, "invalid risk levels"

    candle_time = str(m5.index[-1])

    signal_id = f"{symbol}|{direction}|{candle_time}"

    return {

        "id": signal_id,

        "pair": symbol.replace("=X", ""),

        "direction": direction,

        "entry": entry,

        "stop": stop,

        "target": target,

        "rr": round(abs(target - entry) / risk, 2),

        "rsi": round(rsi, 1),

        "score": score,

        "reasons": reasons,

        "candle_time": candle_time,

    }, "qualified"

def log_signal(signal):

    row = {

        "signal_id": signal["id"],

        "timestamp_utc": datetime.now(timezone.utc).isoformat(),

        "pair": signal["pair"],

        "direction": signal["direction"],

        "entry_reference": signal["entry"],

        "stop_loss_estimate": signal["stop"],

        "take_profit_estimate": signal["target"],

        "planned_rr": signal["rr"],

        "score": signal["score"],

        "status": "ESTIMATE_ONLY",

        "outcome": "UNVERIFIED",

    }

    pd.DataFrame([row]).to_csv(

        LOG_FILE,

        mode="a",

        header=not LOG_FILE.exists(),

        index=False,

    )

def format_alert(signal):

    return (

        "FOREX SETUP — M5 (ESTIMATED LEVELS)\n\n"

        f"Pair: {signal['pair']}\n"

        f"Direction: {signal['direction']}\n"

        f"Entry reference: {signal['entry']:.5f}\n"

        f"Stop-loss estimate: {signal['stop']:.5f}\n"

        f"Take-profit estimate: {signal['target']:.5f}\n"

        f"Planned R:R: 1:{signal['rr']:.2f}\n"

        f"Quality score: {signal['score']}/6\n"

        f"RSI: {signal['rsi']}\n"

        f"Closed M5 candle: {signal['candle_time']}\n"

        f"Reasons: {', '.join(signal['reasons'])}\n\n"

        "Yahoo data is not broker-verified. Check broker price, spread "

        "and execution before considering entry. Alert only; no auto-trading."

    )

def main():

    state = load_state()

    sent = set(state.get("sent", []))

    counts = {}

    checked = 0

    qualifying = 0

    alerts_sent = 0

    for symbol in PAIRS:

        checked += 1

        try:

            signal, reason = evaluate_pair(symbol)

            counts[reason] = counts.get(reason, 0) + 1

            if signal is None:

                continue

            qualifying += 1

            if signal["id"] in sent:

                counts["duplicate skipped"] = (

                    counts.get("duplicate skipped", 0) + 1

                )

                continue

            if telegram(format_alert(signal)):

                sent.add(signal["id"])

                state["sent"] = list(sent)[-1000:]

                state.setdefault("open_estimates", []).append(signal)

                state["open_estimates"] = state["open_estimates"][-200:]

                save_state(state)

                log_signal(signal)

                alerts_sent += 1

        except Exception:

            logging.exception("Analysis error for %s", symbol)

            counts["analysis error"] = counts.get("analysis error", 0) + 1

    summary = [

        "Forex scan complete.",

        f"Pairs checked: {checked}",

        f"Qualifying setups: {qualifying}",

        f"Qualifying alerts sent: {alerts_sent}",

        "Data source: Yahoo Finance; not broker-verified.",

        "No signal is not a reason to force a trade.",

        "Rejections:",

    ]

    for reason, number in sorted(counts.items()):

        summary.append(f"- {reason}: {number}")

    telegram("\n".join(summary))

    logging.info("\n".join(summary))

if __name__ == "__main__":

    main()
