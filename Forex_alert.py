

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

ATR_STOP_MULTIPLIER = 1.5

MIN_ATR_PERCENT = 0.015

MAX_ATR_PERCENT = 0.60

MAX_CANDLE_ATR = 1.8

# Require a meaningful and strengthening M5 EMA9/EMA21 gap.
M5_MIN_EMA_SEPARATION_ATR = 0.04

# Give M5 stops room beyond recent structure, with a volatility buffer.
STRUCTURE_LOOKBACK_CANDLES = 12
STRUCTURE_STOP_BUFFER_ATR = 0.25
MAX_STOP_DISTANCE_ATR = 3.0

MIN_SCORE = 6

# Conservative alert-only controls; these do not control broker execution.
MAX_ALERTS_PER_UTC_DAY = 5
MAX_ONE_ALERT_PER_PAIR_PER_UTC_DAY = True

# Manual-execution safety only: this bot cannot read MT4 equity, spread,
# broker pip values, or enforce position size.
MAX_ENTRY_DRIFT_ATR = 0.20
MAX_SIGNAL_AGE_MINUTES = 10
RECOMMENDED_MAX_RISK_PERCENT = 0.25

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
    # Expand older CSV schemas in place so no previous signal history is lost.
    fieldnames = [
        "timestamp_utc", "pair", "direction", "entry_reference", "stop_loss",
        "take_profit", "planned_rr", "rsi", "score", "candle_time",
        "data_source", "outcome", "m5_ema9", "m5_ema21", "m15_trend", "h1_trend",
        "m15_ema20", "m15_ema50", "h1_ema20", "h1_ema50", "atr", "atr_percent",
        "signal_candle_open", "signal_candle_high", "signal_candle_low",
        "evidence_note", "outcome_candle_time", "outcome_evidence",
    ]
    if LOG_FILE.exists():
        try:
            with LOG_FILE.open("r", newline="", encoding="utf-8") as existing:
                reader = csv.DictReader(existing)
                old_header = reader.fieldnames or []
                old_rows = list(reader)
            if old_header != fieldnames:
                temp_file = LOG_FILE.with_suffix(".upgrade.tmp")
                with temp_file.open("w", newline="", encoding="utf-8") as upgraded:
                    writer = csv.DictWriter(upgraded, fieldnames=fieldnames, extrasaction="ignore")
                    writer.writeheader()
                    for old_row in old_rows:
                        upgraded_row = {key: old_row.get(key, "") for key in fieldnames}
                        if not upgraded_row.get("evidence_note"):
                            upgraded_row["evidence_note"] = "Historical alert; detailed indicator snapshot was not captured by the older bot version."
                        if not upgraded_row.get("outcome_evidence") and old_row.get("outcome"):
                            upgraded_row["outcome_evidence"] = "Historical outcome label; original candle evidence was not stored."
                        writer.writerow(upgraded_row)
                temp_file.replace(LOG_FILE)
                log(f"Upgraded signal log schema in place; preserved {len(old_rows)} historical rows")
        except Exception as exc:
            log(f"Existing signal log could not be upgraded: {exc}")
            raise
    exists = LOG_FILE.exists()
    with LOG_FILE.open("a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerow({key: row.get(key, "") for key in fieldnames})

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

def candle_is_fresh(data, max_age_minutes, candle_duration_minutes=0):

    # Yahoo timestamps candles at their opening time. Freshness must be
    # measured from the candle close, otherwise a completed M5 bar appears
    # five minutes older than it really is.
    last_time = data.index[-1]

    if last_time.tzinfo is None:

        last_time = last_time.tz_localize("UTC")

    else:

        last_time = last_time.tz_convert("UTC")

    candle_close_time = last_time + pd.Timedelta(minutes=candle_duration_minutes)

    age_minutes = (

        datetime.now(timezone.utc) - candle_close_time.to_pydatetime()

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

def detect_h1_swing_levels(data, lookback=120, left_bars=2, right_bars=2):
    """Return latest confirmed H1 pivot high/low using two closed bars either side."""
    if len(data) < left_bars + right_bars + 5:
        return {"high": None, "low": None}
    window = data.iloc[-lookback:].copy()
    highs, lows = [], []
    for i in range(left_bars, len(window) - right_bars):
        high = float(window.iloc[i]["High"])
        low = float(window.iloc[i]["Low"])
        prior_highs = window.iloc[i-left_bars:i]["High"]
        next_highs = window.iloc[i+1:i+right_bars+1]["High"]
        prior_lows = window.iloc[i-left_bars:i]["Low"]
        next_lows = window.iloc[i+1:i+right_bars+1]["Low"]
        if high > float(prior_highs.max()) and high > float(next_highs.max()):
            highs.append(high)
        if low < float(prior_lows.min()) and low < float(next_lows.min()):
            lows.append(low)
    return {"high": highs[-1] if highs else None, "low": lows[-1] if lows else None}


def detect_liquidity_sweep(data, direction, lookback=12, recent_candles=3):
    """Detect a recent stop-run and close back inside the prior swing range.

    BUY: wick takes the prior swing low, then a bullish candle closes back above it.
    SELL: wick takes the prior swing high, then a bearish candle closes back below it.
    This is a quality-score bonus, never a mandatory entry condition.
    """
    if direction not in ("BUY", "SELL") or len(data) < lookback + 1:
        return False
    first = max(lookback, len(data) - recent_candles)
    for index in range(first, len(data)):
        candle = data.iloc[index]
        prior = data.iloc[index - lookback:index]
        prior_low = float(prior["Low"].min())
        prior_high = float(prior["High"].max())
        open_price = float(candle["Open"])
        high = float(candle["High"])
        low = float(candle["Low"])
        close = float(candle["Close"])
        if direction == "BUY":
            swept = low < prior_low and close > prior_low
            rejection = close > open_price
        else:
            swept = high > prior_high and close < prior_high
            rejection = close < open_price
        if swept and rejection:
            return True
    return False


def detect_fvg_retest(data, direction, atr, lookback=10):
    """Require the latest completed candle to reject a recent three-candle FVG."""
    if direction not in ("BUY", "SELL") or len(data) < 5 or atr <= 0:
        return False
    latest = data.iloc[-1]
    start = max(2, len(data) - lookback - 1)
    for index in range(start, len(data) - 1):
        first = data.iloc[index - 2]
        third = data.iloc[index]
        if direction == "BUY":
            gap_low = float(first["High"])
            gap_high = float(third["Low"])
            gap_size = gap_high - gap_low
            if gap_size < atr * 0.05 or gap_size > atr * 0.80:
                continue
            touched = float(latest["Low"]) <= gap_high and float(latest["High"]) >= gap_low
            confirmed = float(latest["Close"]) > gap_high and float(latest["Close"]) > float(latest["Open"])
        else:
            gap_low = float(third["High"])
            gap_high = float(first["Low"])
            gap_size = gap_high - gap_low
            if gap_size < atr * 0.05 or gap_size > atr * 0.80:
                continue
            touched = float(latest["High"]) >= gap_low and float(latest["Low"]) <= gap_high
            confirmed = float(latest["Close"]) < gap_low and float(latest["Close"]) < float(latest["Open"])
        if touched and confirmed:
            return True
    return False
def m5_ema_separation_check(data, direction, min_atr=M5_MIN_EMA_SEPARATION_ATR):
    """Reject flat/touching M5 EMAs; require directional gap to be widening."""
    if direction not in ("BUY", "SELL") or len(data) < 2:
        return False, "insufficient data for M5 EMA separation check"
    last = data.iloc[-1]
    previous = data.iloc[-2]
    atr = float(last["ATR"])
    if atr <= 0:
        return False, "invalid ATR for M5 EMA separation check"
    current_gap = float(last["EMA9"]) - float(last["EMA21"])
    previous_gap = float(previous["EMA9"]) - float(previous["EMA21"])
    directional_gap = current_gap if direction == "BUY" else -current_gap
    previous_directional_gap = previous_gap if direction == "BUY" else -previous_gap
    if directional_gap < atr * min_atr:
        return False, "M5 EMA9/EMA21 separation too small"
    if directional_gap <= previous_directional_gap:
        return False, "M5 EMA9/EMA21 separation is not expanding"
    return True, "M5 EMA separation confirmed"


def entry_location_check(data, direction, atr):
    """Demand a real pullback location, not a momentum candle in open space."""
    if direction not in ("BUY", "SELL") or len(data) < 25 or atr <= 0:
        return False, "insufficient data for structural entry-location check"

    last = data.iloc[-1]
    recent = data.iloc[-4:]
    prior_range = data.iloc[-21:-1]
    close = float(last["Close"])
    ema21 = float(last["EMA21"])

    # A nearby EMA21 retest is useful, but alone is not proof of a good location.
    if direction == "BUY":
        ema_retest = any(
            float(row["Low"]) <= float(row["EMA21"]) + float(row["ATR"]) * 0.15
            and float(row["Close"]) > float(row["EMA21"])
            for _, row in recent.iterrows()
        )
        if close < ema21 or close - ema21 > atr * 0.45:
            return False, "buy entry not close enough to a reclaimed M5 EMA21"
        range_low = float(prior_range["Low"].min())
        range_high = float(prior_range["High"].max())
        position = (close - range_low) / max(range_high - range_low, 1e-12)
        room = range_high - close
    else:
        ema_retest = any(
            float(row["High"]) >= float(row["EMA21"]) - float(row["ATR"]) * 0.15
            and float(row["Close"]) < float(row["EMA21"])
            for _, row in recent.iterrows()
        )
        if close > ema21 or ema21 - close > atr * 0.45:
            return False, "sell entry not close enough to a rejected M5 EMA21"
        range_low = float(prior_range["Low"].min())
        range_high = float(prior_range["High"].max())
        position = (close - range_low) / max(range_high - range_low, 1e-12)
        room = close - range_low

    if not ema_retest:
        return False, "no fresh M5 pullback/retest at the entry area"

    if direction == "BUY" and position > 0.68:
        return False, "buy entry too high in recent M5 range; poor reward location"
    if direction == "SELL" and position < 0.32:
        return False, "sell entry too low in recent M5 range; poor reward location"
    if room < atr * 1.25:
        return False, "not enough space to the recent swing boundary"

    # Require evidence that price reacted at a meaningful area: a stop-run
    # reclaim or a three-candle imbalance (FVG) retest. Neither is a prediction.
    # Only a sweep on the latest completed candle validates a fresh entry.\n    sweep = detect_liquidity_sweep(data, direction, lookback=12, recent_candles=1)
    fvg_retest = detect_fvg_retest(data, direction, atr, lookback=10)
    if not (sweep or fvg_retest):
        return False, "no recent liquidity sweep/reclaim or FVG retest at entry location"

    evidence = []
    if ema_retest:
        evidence.append("EMA21 pullback")
    if sweep:
        evidence.append("liquidity sweep/reclaim")
    if fvg_retest:
        evidence.append("FVG retest")
    return True, " + ".join(evidence) + "; range location and target space confirmed"


def detect_pullback_continuation(data, direction):
    """Confirm a shallow pullback into M5 EMA21 followed by a break of the prior candle."""
    if direction not in ("BUY", "SELL") or len(data) < 30:
        return False, "insufficient data for pullback continuation"

    last = data.iloc[-1]
    previous = data.iloc[-2]
    pullback = data.iloc[-5:-1]
    atr = float(last["ATR"])
    if atr <= 0:
        return False, "invalid ATR for pullback continuation"

    if direction == "BUY":
        touched_zone = any(
            float(row["Low"]) <= float(row["EMA21"]) + float(row["ATR"]) * 0.20
            and float(row["Close"]) >= float(row["EMA21"]) - float(row["ATR"]) * 0.35
            for _, row in pullback.iterrows()
        )
        confirmed = (
            float(last["Close"]) > float(last["Open"])
            and float(last["Close"]) > float(previous["High"])
            and float(last["Close"]) > float(last["EMA21"])
            and float(last["EMA9"]) > float(previous["EMA9"])
            and float(last["EMA9"]) > float(last["EMA21"])
            and float(last["Close"]) - float(last["EMA21"]) <= atr * 0.55
        )
    else:
        touched_zone = any(
            float(row["High"]) >= float(row["EMA21"]) - float(row["ATR"]) * 0.20
            and float(row["Close"]) <= float(row["EMA21"]) + float(row["ATR"]) * 0.35
            for _, row in pullback.iterrows()
        )
        confirmed = (
            float(last["Close"]) < float(last["Open"])
            and float(last["Close"]) < float(previous["Low"])
            and float(last["Close"]) < float(last["EMA21"])
            and float(last["EMA9"]) < float(previous["EMA9"])
            and float(last["EMA9"]) < float(last["EMA21"])
            and float(last["EMA21"]) - float(last["Close"]) <= atr * 0.55
        )

    if not touched_zone:
        return False, "no recent pullback into the M5 EMA21 area"
    if not confirmed:
        return False, "pullback has not produced a confirmed continuation candle"
    return True, "M5 EMA21 pullback + confirmed break of prior candle"

def analyse_pair(ticker):

    m5 = indicators(download_data(ticker, "5m", "5d"))

    m15 = indicators(download_data(ticker, "15m", "5d"))

    h1 = indicators(download_data(ticker, "1h", "1mo"))

    fresh, age = candle_is_fresh(m5, MAX_SIGNAL_AGE_MINUTES, candle_duration_minutes=5)

    if not fresh:

        return None, f"stale M5 data ({age:.1f} minutes old)"

    fresh15, age15 = candle_is_fresh(m15, 40, candle_duration_minutes=15)

    fresh1h, age1h = candle_is_fresh(h1, 150, candle_duration_minutes=60)

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

    # Standard continuation setups need M15/H1 agreement. A separate,
    # stricter sweep-reversal route may qualify when exactly one higher
    # timeframe agrees with the latest completed M5 sweep direction.
    sweep_buy = detect_liquidity_sweep(m5, "BUY", lookback=12, recent_candles=1)
    sweep_sell = detect_liquidity_sweep(m5, "SELL", lookback=12, recent_candles=1)
    if sweep_buy and sweep_sell:
        return None, "ambiguous M5 sweep in both directions"

    setup_type = "CONTINUATION"
    if m15_trend != "NONE" and m15_trend == h1_trend:
        direction = m15_trend
        pullback_candidate, _ = detect_pullback_continuation(m5, direction)
        if pullback_candidate:
            setup_type = "PULLBACK CONTINUATION"
    else:
        reversal_direction = "BUY" if sweep_buy else "SELL" if sweep_sell else None
        if reversal_direction is None:
            return None, "M15/H1 mismatch without a fresh M5 liquidity sweep"
        if reversal_direction not in (m15_trend, h1_trend):
            return None, "sweep reversal rejected: neither M15 nor H1 confirms direction"
        direction = reversal_direction
        setup_type = "SWEEP REVERSAL"

    # Filter weak/choppy trends using ATR-normalized EMA separation.
    m15_last = m15.iloc[-1]
    h1_last = h1.iloc[-1]
    if abs(float(m15_last["EMA20"]) - float(m15_last["EMA50"])) < float(m15_last["ATR"]) * 0.08:
        return None, "M15 trend too weak / choppy"
    if abs(float(h1_last["EMA20"]) - float(h1_last["EMA50"])) < float(h1_last["ATR"]) * 0.08:
        return None, "H1 trend too weak / choppy"

    # Avoid chasing a move that is already stretched from the trigger EMA.
    if abs(close - float(last["EMA21"])) > atr * 0.8:
        return None, "entry too extended from M5 EMA21"
    if direction == "BUY" and float(last["Close"]) <= float(last["Open"]):
        return None, "M5 signal candle did not close bullish"
    if direction == "SELL" and float(last["Close"]) >= float(last["Open"]):
        return None, "M5 signal candle did not close bearish"

    # Require a meaningful, expanding M5 EMA gap before accepting any setup.
    ema_gap_ok, ema_gap_note = m5_ema_separation_check(m5, direction)
    if not ema_gap_ok:
        return None, ema_gap_note

    # Pullback continuations use their own EMA21 retest + candle-break confirmation.
    # Other setup types retain the stricter sweep/FVG structural location gate.
    if setup_type == "PULLBACK CONTINUATION":
        location_ok, location_note = detect_pullback_continuation(m5, direction)
    else:
        location_ok, location_note = entry_location_check(m5, direction, atr)
    if not location_ok:
        return None, location_note

    # Continuations require a fresh EMA crossover. Sweep reversals do not
    # wait for that late crossover, but still require a decisive M5 close,
    # directional EMA9 momentum and a controlled RSI.
    recent = m5.iloc[-4:]
    if direction == "BUY":
        crossed = (
            (recent["EMA9"].shift(1) <= recent["EMA21"].shift(1))
            & (recent["EMA9"] > recent["EMA21"])
        ).any()
        if setup_type == "CONTINUATION" and not crossed:
            return None, "no fresh M5 bullish crossover"
        if last["EMA9"] <= previous["EMA9"]:
            return None, "M5 EMA9 momentum not rising"
        if setup_type == "CONTINUATION" and last["EMA9"] <= last["EMA21"]:
            return None, "M5 momentum no longer bullish"
        if setup_type == "CONTINUATION" and not 50 <= rsi <= 68:
            return None, "RSI outside buy range"
        if setup_type == "SWEEP REVERSAL" and not 40 <= rsi <= 68:
            return None, "reversal RSI outside buy range"
        if setup_type == "CONTINUATION" and close <= float(last["EMA21"]):
            return None, "price not holding above EMA21"
    else:
        crossed = (
            (recent["EMA9"].shift(1) >= recent["EMA21"].shift(1))
            & (recent["EMA9"] < recent["EMA21"])
        ).any()
        if setup_type == "CONTINUATION" and not crossed:
            return None, "no fresh M5 bearish crossover"
        if last["EMA9"] >= previous["EMA9"]:
            return None, "M5 EMA9 momentum not falling"
        if setup_type == "CONTINUATION" and last["EMA9"] >= last["EMA21"]:
            return None, "M5 momentum no longer bearish"
        if setup_type == "CONTINUATION" and not 32 <= rsi <= 50:
            return None, "RSI outside sell range"
        if setup_type == "SWEEP REVERSAL" and not 32 <= rsi <= 60:
            return None, "reversal RSI outside sell range"
        if setup_type == "CONTINUATION" and close >= float(last["EMA21"]):
            return None, "price not holding below EMA21"

    # Confirmed H1 pivots: two completed candles must form on the right.
    h1_swings = detect_h1_swing_levels(h1)
    h1_swing_high = h1_swings["high"]
    h1_swing_low = h1_swings["low"]

    # Simple quality score. A high score is a filter, not

    # proof that the trade has a high probability of winning.

    score = 0

    liquidity_sweep = detect_liquidity_sweep(m5, direction)
    if liquidity_sweep:
        score += 2 if setup_type == "SWEEP REVERSAL" else 1

    if m15_trend == direction:
        score += 2

    if h1_trend == direction:
        score += 2

    if setup_type == "SWEEP REVERSAL":
        momentum_confirmed = (
            direction == "BUY" and last["EMA9"] > previous["EMA9"]
        ) or (
            direction == "SELL" and last["EMA9"] < previous["EMA9"]
        )
        rsi_confirmed = (
            direction == "BUY" and 45 <= rsi <= 65
        ) or (
            direction == "SELL" and 35 <= rsi <= 55
        )
    else:
        momentum_confirmed = (
            direction == "BUY" and last["EMA9"] > last["EMA21"]
        ) or (
            direction == "SELL" and last["EMA9"] < last["EMA21"]
        )
        rsi_confirmed = (
            direction == "BUY" and 52 <= rsi <= 65
        ) or (
            direction == "SELL" and 35 <= rsi <= 48
        )

    if momentum_confirmed:
        score += 1
    if rsi_confirmed:
        score += 1

    if score < MIN_SCORE:

        return None, "quality score too low"

    # Wider volatility-aware stop, placed beyond recent M5 structure.
    # Use the last completed candles (including the signal candle), then add
    # an ATR buffer. Never force a stop so wide that the setup is unsuitable.
    structure = m5.iloc[-STRUCTURE_LOOKBACK_CANDLES:]
    buffer = atr * STRUCTURE_STOP_BUFFER_ATR
    minimum_distance = atr * ATR_STOP_MULTIPLIER

    if direction == "BUY":
        structure_stop = float(structure["Low"].min()) - buffer
        stop = min(close - minimum_distance, structure_stop)
        stop_distance = close - stop
        if stop_distance <= 0 or stop_distance > atr * MAX_STOP_DISTANCE_ATR:
            return None, "structure stop too wide for M5 setup"
        target = close + stop_distance * MIN_RR

    else:
        structure_stop = float(structure["High"].max()) + buffer
        stop = max(close + minimum_distance, structure_stop)
        stop_distance = stop - close
        if stop_distance <= 0 or stop_distance > atr * MAX_STOP_DISTANCE_ATR:
            return None, "structure stop too wide for M5 setup"
        target = close - stop_distance * MIN_RR

    # Reject targets that run into the nearest opposing confirmed H1 swing.
    h1_clearance = float(h1.iloc[-1]["ATR"]) * 0.15
    if direction == "BUY" and h1_swing_high is not None and close < h1_swing_high:
        if h1_swing_high - close <= (target - close) + h1_clearance:
            return None, "H1 swing-high resistance too close for planned BUY target"
    if direction == "SELL" and h1_swing_low is not None and close > h1_swing_low:
        if close - h1_swing_low <= (close - target) + h1_clearance:
            return None, "H1 swing-low support too close for planned SELL target"

    # Forex quote precision: JPY pairs usually use 3 decimals.

    pair = ticker.replace("=X", "")

    decimals = 3 if pair.endswith("JPY") else 5

    return {

        "ticker": ticker,

        "pair": pair[:3] + "/" + pair[3:],

        "direction": direction,

        "setup_type": setup_type,

        "entry": round(close, decimals),

        "stop": round(stop, decimals),

        "target": round(target, decimals),

        "rr": MIN_RR,

        "rsi": round(rsi, 1),

        "score": score,

        "liquidity_sweep": liquidity_sweep,

        "location_note": location_note,

        "candle_time": str(m5.index[-1]),

        "atr": round(atr, decimals),

        "atr_percent": round(atr_percent, 4),

        "m5_ema9": round(float(last["EMA9"]), decimals),

        "m5_ema21": round(float(last["EMA21"]), decimals),

        "m15_trend": m15_trend,

        "h1_trend": h1_trend,
        "h1_swing_high": round(h1_swing_high, decimals) if h1_swing_high is not None else None,
        "h1_swing_low": round(h1_swing_low, decimals) if h1_swing_low is not None else None,

        "m15_ema20": round(float(m15.iloc[-1]["EMA20"]), decimals),

        "m15_ema50": round(float(m15.iloc[-1]["EMA50"]), decimals),

        "h1_ema20": round(float(h1.iloc[-1]["EMA20"]), decimals),

        "h1_ema50": round(float(h1.iloc[-1]["EMA50"]), decimals),

        "signal_candle_open": round(float(last["Open"]), decimals),

        "signal_candle_high": round(float(last["High"]), decimals),

        "signal_candle_low": round(float(last["Low"]), decimals),

        "evidence_note": (
            f"{setup_type}; M5 candle confirmation; M15={m15_trend}, H1={h1_trend}; "
            f"RSI={rsi:.1f}; ATR={atr:.{decimals}f}; ATR%={atr_percent:.4f}; "
            f"quality filter score={score}/7; latest-candle liquidity sweep={'yes' if sweep_buy or sweep_sell else 'no'}. Rule-based evidence, not a win-probability estimate."
        ),

    }, "qualifies"


def update_signal_outcomes():
    """Resolve prior alerts using later Yahoo M5 candles; never imply broker verification."""
    if not LOG_FILE.exists():
        return

    fieldnames = [
        "timestamp_utc", "pair", "direction", "entry_reference", "stop_loss",
        "take_profit", "planned_rr", "rsi", "score", "candle_time",
        "data_source", "outcome", "m5_ema9", "m5_ema21", "m15_trend", "h1_trend",
        "m15_ema20", "m15_ema50", "h1_ema20", "h1_ema50", "atr", "atr_percent",
        "signal_candle_open", "signal_candle_high", "signal_candle_low",
        "evidence_note", "outcome_candle_time", "outcome_evidence",
    ]

    try:
        with LOG_FILE.open("r", newline="", encoding="utf-8") as file:
            reader = csv.DictReader(file)
            old_fieldnames = reader.fieldnames or []
            rows = list(reader)
        if old_fieldnames != fieldnames:
            # Upgrade history before outcome checks, even on scans with no new alerts.
            for row in rows:
                for field in fieldnames:
                    row.setdefault(field, "")
                if not row.get("evidence_note"):
                    row["evidence_note"] = "Historical alert; detailed indicator snapshot was not captured by the older bot version."
                if not row.get("outcome_evidence") and row.get("outcome"):
                    row["outcome_evidence"] = "Historical outcome label; original candle evidence was not stored."
            temp_file = LOG_FILE.with_suffix(".upgrade.tmp")
            with temp_file.open("w", newline="", encoding="utf-8") as upgraded:
                writer = csv.DictWriter(upgraded, fieldnames=fieldnames, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(rows)
            temp_file.replace(LOG_FILE)
            log(f"Upgraded signal log schema for outcome tracking; preserved {len(rows)} historical rows")
    except Exception as exc:
        log(f"Outcome tracking could not read signal log: {exc}")
        return

    pending_by_pair = {}
    for row in rows:
        if row.get("outcome") not in ("UNVERIFIED", "UNVERIFIED_PENDING", ""):
            continue
        pair = (row.get("pair") or "").replace("/", "")
        ticker = pair + "=X"
        if pair and ticker in PAIRS:
            pending_by_pair.setdefault(ticker, []).append(row)

    if not pending_by_pair:
        return

    changed = False
    resolved_notifications = []
    checked_at = datetime.now(timezone.utc).isoformat()

    for ticker, pending_rows in pending_by_pair.items():
        try:
            candles = download_data(ticker, "5m", "5d")
            candles = candles.copy()
            if candles.index.tz is None:
                candles.index = candles.index.tz_localize("UTC")
            else:
                candles.index = candles.index.tz_convert("UTC")

            for row in pending_rows:
                try:
                    signal_time = pd.Timestamp(row["candle_time"])
                    if signal_time.tzinfo is None:
                        signal_time = signal_time.tz_localize("UTC")
                    else:
                        signal_time = signal_time.tz_convert("UTC")
                    entry = float(row["entry_reference"])
                    stop = float(row["stop_loss"])
                    target = float(row["take_profit"])
                    direction = row["direction"]
                except (ValueError, TypeError, KeyError):
                    continue

                later = candles[candles.index > signal_time]
                entry_hit = False
                for outcome_time, candle in later.iterrows():
                    high = float(candle["High"])
                    low = float(candle["Low"])
                    evidence_time = outcome_time.isoformat()
                    evidence_detail = (
                        f"Yahoo M5 candle {evidence_time}: high={high}, low={low}; "
                        f"entry reference={entry}, SL={stop}, TP={target}. "
                        "Not broker-verified."
                    )
                    if direction == "BUY":
                        stop_hit = low <= stop
                        target_hit = high >= target
                    elif direction == "SELL":
                        stop_hit = high >= stop
                        target_hit = low <= target
                    else:
                        break

                    # Do not score an alert until its entry reference was actually
                    # touched in a later candle. If entry and either exit level are
                    # touched in that same OHLC candle, their order is unknowable.
                    if not entry_hit:
                        if not (low <= entry <= high):
                            continue
                        entry_hit = True
                        if stop_hit or target_hit:
                            row["outcome"] = "AMBIGUOUS_ENTRY_EXIT_SAME_CANDLE"
                            row["outcome_candle_time"] = evidence_time
                            row["outcome_evidence"] = (
                                evidence_detail + " Entry and an exit level were touched "
                                "in the same M5 candle; intrabar order cannot be verified."
                            )
                            changed = True
                            log(f"Outcome ambiguous for {row['pair']}: entry and exit level touched in same M5 candle")
                            resolved_notifications.append(
                                f"{row['pair']} {direction}: AMBIGUOUS — entry and SL/TP level touched in one Yahoo M5 candle at {evidence_time}. Not broker-verified."
                            )
                            break
                        continue

                    if stop_hit and target_hit:
                        row["outcome"] = "AMBIGUOUS_BOTH_HIT_YAHOO"
                        row["outcome_candle_time"] = evidence_time
                        row["outcome_evidence"] = evidence_detail
                        changed = True
                        log(f"Outcome ambiguous for {row['pair']}: stop and target touched in same M5 candle")
                        resolved_notifications.append(
                            f"{row['pair']} {direction}: AMBIGUOUS — SL and TP both touched in one Yahoo M5 candle at {evidence_time}. Not broker-verified."
                        )
                        break
                    if target_hit:
                        row["outcome"] = "TP_HIT_YAHOO_UNVERIFIED"
                        row["outcome_candle_time"] = evidence_time
                        row["outcome_evidence"] = evidence_detail
                        changed = True
                        log(f"Target touched in Yahoo M5 data for {row['pair']} (not broker-verified)")
                        resolved_notifications.append(
                            f"{row['pair']} {direction}: TP touched at {evidence_time} — Yahoo M5 estimate, NOT broker-verified."
                        )
                        break
                    if stop_hit:
                        row["outcome"] = "SL_HIT_YAHOO_UNVERIFIED"
                        row["outcome_candle_time"] = evidence_time
                        row["outcome_evidence"] = evidence_detail
                        changed = True
                        log(f"Stop touched in Yahoo M5 data for {row['pair']} (not broker-verified)")
                        resolved_notifications.append(
                            f"{row['pair']} {direction}: SL touched at {evidence_time} — Yahoo M5 estimate, NOT broker-verified."
                        )
                        break
                else:
                    # Keep unresolved alerts pending; no outcome is invented.
                    if row.get("outcome") != "UNVERIFIED_PENDING":
                        row["outcome"] = "UNVERIFIED_PENDING"
                        changed = True
        except Exception as exc:
            log(f"Outcome tracking error for {ticker}: {type(exc).__name__}: {exc}")

    if changed:
        temp_file = LOG_FILE.with_suffix(".tmp")
        try:
            with temp_file.open("w", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(rows)
            temp_file.replace(LOG_FILE)
            log(f"Updated signal outcomes at {checked_at}; Yahoo-based results remain unverified.")
            if resolved_notifications:
                summary = "FOREX ALERT OUTCOME CHECK (Yahoo M5; not broker-verified)\\n\\n" + "\\n".join(resolved_notifications)
                telegram_send(summary)
        except Exception as exc:
            log(f"Could not save updated signal outcomes: {exc}")
            if temp_file.exists():
                temp_file.unlink()

def main():

    log("Starting forex scan")

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:

        raise RuntimeError(

            "Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID GitHub secret"

        )

    update_signal_outcomes()

    state = load_state()
    today_utc = datetime.now(timezone.utc).date().isoformat()
    daily_pair_prefix = f"daily_pair|{today_utc}|"
    alerts_today = 0
    if LOG_FILE.exists():
        try:
            with LOG_FILE.open("r", newline="", encoding="utf-8") as file:
                for logged_row in csv.DictReader(file):
                    if (logged_row.get("timestamp_utc") or "")[:10] == today_utc:
                        alerts_today += 1
        except Exception as exc:
            log(f"Could not read daily alert count; fail-safe is to stop new alerts: {exc}")
            telegram_send("Forex bot safety stop: unable to read daily alert log. No new signals will be sent until the log is checked.")
            return
    if alerts_today >= MAX_ALERTS_PER_UTC_DAY:
        summary = f"Forex scan safety stop: UTC daily alert cap reached ({alerts_today}/{MAX_ALERTS_PER_UTC_DAY}). No more alerts today. Alert-only control; it does not track broker P/L or replace FTMO account limits."
        log(summary)
        telegram_send(summary)
        return

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

            # Limit repeat alerts for the same pair and cap total alerts per UTC day.
            daily_pair_key = daily_pair_prefix + signal["ticker"]
            if MAX_ONE_ALERT_PER_PAIR_PER_UTC_DAY and state.get(daily_pair_key):
                rejected["daily pair alert limit"] = rejected.get("daily pair alert limit", 0) + 1
                continue
            if alerts_today + sent >= MAX_ALERTS_PER_UTC_DAY:
                rejected["UTC daily alert cap reached"] = rejected.get("UTC daily alert cap reached", 0) + 1
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
                f"Setup type: {signal['setup_type']}\n"

                f"Entry reference: {signal['entry']}\n"

                f"Stop-loss: {signal['stop']}\n"

                f"Take-profit: {signal['target']}\n"

                f"Planned risk/reward: 1:{signal['rr']:.2f}\n"

                f"RSI: {signal['rsi']}\n"

                f"Quality filter score: {signal['score']}/7 (filter, not win probability)\n"

                f"Liquidity sweep: {'YES' if signal['liquidity_sweep'] else 'NO'}\n"

                f"M5 EMA9/EMA21: {signal['m5_ema9']} / {signal['m5_ema21']}\n"

                f"M15 trend: {signal['m15_trend']} (EMA20 {signal['m15_ema20']} / EMA50 {signal['m15_ema50']})\n"

                f"H1 trend: {signal['h1_trend']} (EMA20 {signal['h1_ema20']} / EMA50 {signal['h1_ema50']})\n"

                f"ATR: {signal['atr']} | ATR%: {signal['atr_percent']}\n"

                f"Signal candle O/H/L: {signal['signal_candle_open']} / {signal['signal_candle_high']} / {signal['signal_candle_low']}\n"

                f"Trigger: {'EMA21 pullback + confirmed candle break' if signal['setup_type'] == 'PULLBACK CONTINUATION' else 'M5 EMA crossover or confirmed sweep reversal'}\\n"

                f"Entry location: {signal['location_note']}\n"

                f"Candle time: {signal['candle_time']}\n"

                f"Data: {DATA_SOURCE}\n\n"

                f"ENTRY SAFETY: only consider entry if your broker price is within "
                f"{MAX_ENTRY_DRIFT_ATR:.2f} ATR of the entry reference and the signal "
                f"is no older than {MAX_SIGNAL_AGE_MINUTES} minutes. Otherwise skip it.\n"
 
                f"RISK SAFETY: this alert cannot read MT4 equity or calculate broker-specific "
                f"lot size. Never use a fixed 20-lot size. Keep planned risk at or below "
                f"{RECOMMENDED_MAX_RISK_PERCENT:.2f}% of current equity and calculate volume "
                f"using your broker's contract/pip value. If uncertain, do not enter.\n\n"
 
                f"Alert only — verify live price, spread, stop and target with your broker. "
                f"Entry is a reference, not a guaranteed fill. Risk is not guaranteed."

            )

            if telegram_send(message):

                state[key] = True
                state[daily_pair_key] = True

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

                    "outcome": "UNVERIFIED_PENDING",

                    "m5_ema9": signal["m5_ema9"],

                    "m5_ema21": signal["m5_ema21"],

                    "m15_trend": signal["m15_trend"],

                    "h1_trend": signal["h1_trend"],

                    "m15_ema20": signal["m15_ema20"],

                    "m15_ema50": signal["m15_ema50"],

                    "h1_ema20": signal["h1_ema20"],

                    "h1_ema50": signal["h1_ema50"],

                    "atr": signal["atr"],

                    "atr_percent": signal["atr_percent"],

                    "signal_candle_open": signal["signal_candle_open"],

                    "signal_candle_high": signal["signal_candle_high"],

                    "signal_candle_low": signal["signal_candle_low"],

                    "evidence_note": signal["evidence_note"],

                    "outcome_candle_time": "",

                    "outcome_evidence": "",

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
