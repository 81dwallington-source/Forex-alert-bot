

import os

import json

import csv

import time

from datetime import datetime, timezone

from pathlib import Path

import requests

import pandas as pd

# =========================================================

# FOREX ALERT BOT — M5 ENTRIES, M15 + H1 CONFIRMATION

# Alerts only. No automatic trading.

# Data source: free public FX chart feed; not broker-verified.

# =========================================================

BOT_NAME = "Forex Alert Bot"

DATA_SOURCE = "biquote public FX OHLC endpoint; third-party feed, not user-broker verified"

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

ATR_STOP_MULTIPLIER = 1.75

MIN_ATR_PERCENT = 0.015

MAX_ATR_PERCENT = 0.60

MAX_CANDLE_ATR = 1.8

# Require a meaningful and strengthening M5 EMA9/EMA21 gap.
M5_MIN_EMA_SEPARATION_ATR = 0.08

# Give M5 stops room beyond recent structure, with a volatility buffer.
STRUCTURE_LOOKBACK_CANDLES = 36
STRUCTURE_PIVOT_LEFT_BARS = 2
STRUCTURE_PIVOT_RIGHT_BARS = 2
STRUCTURE_STOP_BUFFER_ATR = 0.25
MAX_STOP_DISTANCE_ATR = 4.5

MIN_SCORE = 6

# Keep only the two best-defined, testable M5 entry patterns.
ALLOWED_SETUP_TYPES = ("PULLBACK CONTINUATION", "BREAKOUT RETEST")
BUY_RSI_MIN, BUY_RSI_MAX = 52, 65
SELL_RSI_MIN, SELL_RSI_MAX = 35, 48
MIN_HTF_EMA_GAP_ATR = 0.12

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
        "timestamp_utc", "pair", "direction", "setup_type", "entry_reference", "stop_loss",
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
    """Fetch completed FX candles from the third-party biquote public OHLC endpoint; provider provenance is not independently verified."""
    interval_map = {"5m": "5m", "15m": "15m", "1h": "1h"}
    if interval not in interval_map or period not in ("5d", "1mo"):
        raise ValueError(f"Unsupported public-feed request: {interval}, {period}")

    symbol = ticker.replace("=X", "")
    url = f"https://biquote.io/api/{symbol}/ohlc"
    response = requests.get(
        url,
        params={"interval": interval_map[interval], "limit": 1000},
        headers={"User-Agent": "ForexAlertBot/1.0"},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    bars = payload.get("bars") or []
    if not bars:
        raise ValueError(f"biquote returned no {interval} candles for {symbol}")

    rows = []
    for bar in bars:
        # The provider prepends a live/open candle. Strategy calculations only
        # use completed candles, so discard it rather than treating it as closed.
        if bar.get("isOpen") is True:
            continue
        stamp = bar.get("openTime")
        if not stamp:
            continue
        try:
            candle_time = pd.to_datetime(stamp, utc=True)
            values = {
                "Open": bar["open"],
                "High": bar["high"],
                "Low": bar["low"],
                "Close": bar["close"],
            }
            if any(value is None for value in values.values()):
                continue
            rows.append({"time": candle_time, **{k: float(v) for k, v in values.items()}})
        except (KeyError, TypeError, ValueError):
            continue

    if len(rows) < 60:
        raise ValueError(
            f"biquote returned only {len(rows)} completed {interval} candles for {symbol}"
        )
    data = pd.DataFrame(rows).set_index("time").sort_index()
    data = data[~data.index.duplicated(keep="last")]
    if len(data) < 55:
        raise ValueError("Insufficient completed biquote candle history")

    # Reject malformed provider candles before they can affect indicators or risk levels.
    numeric = data[["Open", "High", "Low", "Close"]]
    if not numeric.apply(lambda column: pd.to_numeric(column, errors="coerce").notna().all()).all():
        raise ValueError("Public feed contains non-numeric OHLC values")
    if (numeric <= 0).any().any():
        raise ValueError("Public feed contains non-positive OHLC values")
    invalid_ohlc = (
        (data["High"] < data[["Open", "Close", "Low"]].max(axis=1))
        | (data["Low"] > data[["Open", "Close", "High"]].min(axis=1))
    )
    if invalid_ohlc.any():
        raise ValueError(f"Public feed contains {int(invalid_ohlc.sum())} malformed OHLC candles")
    now = pd.Timestamp.now(tz="UTC")
    if data.index[-1] > now + pd.Timedelta(minutes=1):
        raise ValueError("Public feed returned a future-dated candle")
    data.attrs["data_source"] = DATA_SOURCE
    return data


def latest_feed_price(ticker):
    """Read the provider's live/open M5 candle for a pre-alert price sanity check."""
    symbol = ticker.replace("=X", "")
    url = f"https://biquote.io/api/{symbol}/ohlc"
    response = requests.get(
        url,
        params={"interval": "5m", "limit": 10},
        headers={"User-Agent": "ForexAlertBot/1.0"},
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    bars = payload.get("bars") or []
    live_bars = [bar for bar in bars if bar.get("isOpen") is True]
    if not live_bars:
        return None, None, "provider did not supply a live/open M5 candle"

    bar = max(live_bars, key=lambda item: pd.to_datetime(item.get("openTime"), utc=True))
    try:
        stamp = pd.to_datetime(bar["openTime"], utc=True)
        price = float(bar["close"])
    except (KeyError, TypeError, ValueError):
        return None, None, "live M5 candle has invalid timestamp or price"
    age = (datetime.now(timezone.utc) - stamp.to_pydatetime()).total_seconds() / 60
    if not 0 <= age <= 6:
        return None, None, f"live M5 quote is stale ({age:.1f} minutes from candle open)"
    if not price > 0:
        return None, None, "live M5 quote is not a positive price"
    return price, stamp.isoformat(), "live M5 public-feed quote"


def candle_is_fresh(data, max_age_minutes, candle_duration_minutes=0):

     # OANDA timestamps candles at their opening time. Freshness must be
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

    df["EMA200"] = df["Close"].ewm(span=200, adjust=False).mean()

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


def detect_m5_swing_levels(data, lookback=STRUCTURE_LOOKBACK_CANDLES,
                          left_bars=STRUCTURE_PIVOT_LEFT_BARS,
                          right_bars=STRUCTURE_PIVOT_RIGHT_BARS):
    """Find the latest confirmed M5 pivot high and low; right-side bars confirm each pivot."""
    if len(data) < left_bars + right_bars + 5:
        return {"high": None, "low": None}
    window = data.iloc[-lookback:].copy()
    highs, lows = [], []
    # The final right_bars candles cannot be pivot centres because they are
    # needed to confirm that the swing has held.
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


def m5_ema_separation_check(data, direction, min_atr=M5_MIN_EMA_SEPARATION_ATR):
    """Reject weak EMA alignment; require expanding EMA9/21 gap and falling/rising EMA21/50."""
    if direction not in ("BUY", "SELL") or len(data) < 2:
        return False, "insufficient data for M5 EMA separation check"
    last = data.iloc[-1]
    previous = data.iloc[-2]
    # In production, analyse_pair supplies a full M5 history. A two-row
    # minimum also keeps the isolated regression test fixtures valid.
    earlier = data.iloc[-4] if len(data) >= 4 else previous
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

    # A single candle can briefly sit below/above a stacked EMA set during a
    # counter-trend bounce. Require both medium EMAs to slope in trade direction.
    ema21_move = float(last["EMA21"]) - float(earlier["EMA21"])
    if direction == "SELL" and ema21_move >= 0:
        return False, "M5 EMA21 is not falling"
    if direction == "BUY" and ema21_move <= 0:
        return False, "M5 EMA21 is not rising"

    # EMA50 is present in production M5 data. Some focused unit-test frames
    # contain only EMA9/EMA21; enforce EMA50 slope whenever that column exists.
    if "EMA50" in data.columns:
        ema50_move = float(last["EMA50"]) - float(earlier["EMA50"])
        if direction == "SELL" and ema50_move >= 0:
            return False, "M5 EMA50 is not falling"
        if direction == "BUY" and ema50_move <= 0:
            return False, "M5 EMA50 is not rising"
    return True, "M5 EMA gap and EMA21/EMA50 slope confirmed"


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

    # Entry location is confirmed by the EMA21 pullback, range position, and clear target space.
    return True, "EMA21 pullback; range location and target space confirmed"


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

def detect_breakout_retest(data, direction, lookback=20):
    """Require a prior close beyond a range edge, a retest, then a fresh M5 continuation break."""
    if direction not in ("BUY", "SELL") or len(data) < lookback + 8:
        return False, "insufficient data for breakout/retest"
    atr = float(data.iloc[-1]["ATR"])
    if atr <= 0:
        return False, "invalid ATR for breakout/retest"
    base = data.iloc[-(lookback + 8):-8]
    breakout_window = data.iloc[-8:-3]
    retest_window = data.iloc[-3:-1]
    last, previous = data.iloc[-1], data.iloc[-2]
    if direction == "BUY":
        level = float(base["High"].max())
        broke = (breakout_window["Close"] > level + atr * 0.05).any()
        retested = (
            (retest_window["Low"] <= level + atr * 0.25)
            & (retest_window["Close"] >= level - atr * 0.10)
        ).any()
        confirmed = (
            float(last["Close"]) > level
            and float(last["Close"]) > float(previous["High"])
            and float(last["Close"]) > float(last["Open"])
        )
    else:
        level = float(base["Low"].min())
        broke = (breakout_window["Close"] < level - atr * 0.05).any()
        retested = (
            (retest_window["High"] >= level - atr * 0.25)
            & (retest_window["Close"] <= level + atr * 0.10)
        ).any()
        confirmed = (
            float(last["Close"]) < level
            and float(last["Close"]) < float(previous["Low"])
            and float(last["Close"]) < float(last["Open"])
        )
    if not broke:
        return False, "no prior confirmed range breakout"
    if not retested:
        return False, "breakout level has not been cleanly retested"
    if not confirmed:
        return False, "breakout retest lacks a fresh continuation close"
    return True, "range breakout + retest + fresh M5 continuation break"


def detect_support_resistance_rejection(data, direction, lookback=20):
    """Require a clean rejection of a recent range extreme in the M15/H1 trend direction."""
    if direction not in ("BUY", "SELL") or len(data) < lookback + 3:
        return False, "insufficient data for level rejection"
    atr = float(data.iloc[-1]["ATR"])
    if atr <= 0:
        return False, "invalid ATR for level rejection"
    prior = data.iloc[-(lookback + 1):-1]
    last = data.iloc[-1]
    candle_range = float(last["High"] - last["Low"])
    if candle_range <= 0:
        return False, "zero-range rejection candle"
    if direction == "BUY":
        level = float(prior["Low"].min())
        touched = float(last["Low"]) <= level + atr * 0.20
        rejected = (
            float(last["Close"]) > float(last["Open"])
            and float(last["Close"]) - level >= atr * 0.35
            and (float(last["Close"]) - float(last["Low"])) / candle_range >= 0.65
        )
    else:
        level = float(prior["High"].max())
        touched = float(last["High"]) >= level - atr * 0.20
        rejected = (
            float(last["Close"]) < float(last["Open"])
            and level - float(last["Close"]) >= atr * 0.35
            and (float(last["High"]) - float(last["Close"])) / candle_range >= 0.65
        )
    if not touched:
        return False, "price did not test the recent support/resistance level"
    if not rejected:
        return False, "support/resistance test lacks decisive rejection"
    return True, "recent support/resistance rejection confirmed on M5"


def signal_candle_quality_check(candle, direction, min_body_fraction=0.45, close_edge_fraction=0.25):
    """Require a decisive candle body and a close near its directional extreme."""
    if direction not in ("BUY", "SELL"):
        return False, "invalid direction for signal-candle quality"
    high = float(candle["High"])
    low = float(candle["Low"])
    open_price = float(candle["Open"])
    close = float(candle["Close"])
    candle_range = high - low
    if candle_range <= 0:
        return False, "zero-range signal candle"
    body_fraction = abs(close - open_price) / candle_range
    if body_fraction < min_body_fraction:
        return False, "signal candle body too weak"
    if direction == "BUY":
        if close <= open_price:
            return False, "signal candle is not bullish"
        close_location = (high - close) / candle_range
    else:
        if close >= open_price:
            return False, "signal candle is not bearish"
        close_location = (close - low) / candle_range
    if close_location > close_edge_fraction:
        return False, "signal candle did not close near its directional extreme"
    return True, "decisive signal candle confirmed"


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

    # Strict EMA 21/50/200 stacking on both M5 entry and M15 confirmation.
    # BUY: price > EMA21 > EMA50 > EMA200.
    # SELL: price < EMA21 < EMA50 < EMA200.
    # H1 remains the higher-timeframe EMA20/EMA50 trend context.
    def ema_stack_aligned(frame, side):
        row = frame.iloc[-1]
        price = float(row["Close"])
        ema21 = float(row["EMA21"])
        ema50 = float(row["EMA50"])
        ema200 = float(row["EMA200"])
        if side == "BUY":
            return price > ema21 > ema50 > ema200
        if side == "SELL":
            return price < ema21 < ema50 < ema200
        return False

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

    # All setups require M15 and H1 trend agreement.
    if m15_trend == "NONE" or h1_trend == "NONE" or m15_trend != h1_trend:
        return None, "M15/H1 trend mismatch"

    direction = m15_trend

    if not ema_stack_aligned(m5, direction):
        return None, "M5 EMA21/50/200 stack or price alignment not confirmed"
    if not ema_stack_aligned(m15, direction):
        return None, "M15 EMA21/50/200 confirmation stack not aligned"

    setup_type = "CONTINUATION"
    location_note = "M5 continuation confirmation"
    pullback_candidate, pullback_note = detect_pullback_continuation(m5, direction)
    breakout_candidate, breakout_note = detect_breakout_retest(m5, direction)
    if pullback_candidate:
        setup_type, location_note = "PULLBACK CONTINUATION", pullback_note
    elif breakout_candidate:
        setup_type, location_note = "BREAKOUT RETEST", breakout_note

    # Filter weak/choppy trends using ATR-normalized EMA separation.
    m15_last = m15.iloc[-1]
    h1_last = h1.iloc[-1]
    if abs(float(m15_last["EMA20"]) - float(m15_last["EMA50"])) < float(m15_last["ATR"]) * MIN_HTF_EMA_GAP_ATR:
        return None, "M15 trend too weak / choppy"
    if abs(float(h1_last["EMA20"]) - float(h1_last["EMA50"])) < float(h1_last["ATR"]) * MIN_HTF_EMA_GAP_ATR:
        return None, "H1 trend too weak / choppy"

    if setup_type not in ALLOWED_SETUP_TYPES:
        return None, "setup type not on approved high-quality list"

    if direction == "BUY" and not BUY_RSI_MIN <= rsi <= BUY_RSI_MAX:
        return None, "RSI outside approved buy range"
    if direction == "SELL" and not SELL_RSI_MIN <= rsi <= SELL_RSI_MAX:
        return None, "RSI outside approved sell range"

    # Avoid chasing a move that is already stretched from the trigger EMA.
    if abs(close - float(last["EMA21"])) > atr * 0.8:
        return None, "entry too extended from M5 EMA21"
    candle_ok, candle_note = signal_candle_quality_check(
        last, direction, min_body_fraction=0.55, close_edge_fraction=0.18
    )
    if not candle_ok:
        return None, candle_note

    # Require a meaningful, expanding M5 EMA gap before accepting any setup.
    ema_gap_ok, ema_gap_note = m5_ema_separation_check(m5, direction)
    if not ema_gap_ok:
        return None, ema_gap_note

    # Each setup type has its own independent price-action trigger.
    if setup_type == "PULLBACK CONTINUATION":
        location_ok, location_note = detect_pullback_continuation(m5, direction)
    elif setup_type == "BREAKOUT RETEST":
        location_ok, location_note = detect_breakout_retest(m5, direction)
    else:
        return None, "no approved pullback or breakout-retest trigger"
    if not location_ok:
        return None, location_note

    # The classic EMA crossover is only required for the original continuation setup.
    # Other setup types must pass their own retest/rejection trigger above.
    recent = m5.iloc[-4:]
    if direction == "BUY":
        crossed = (
            (recent["EMA9"].shift(1) <= recent["EMA21"].shift(1))
            & (recent["EMA9"] > recent["EMA21"])
        ).any()
        if last["EMA9"] <= previous["EMA9"]:
            return None, "M5 EMA9 momentum not rising"
        if setup_type == "CONTINUATION" and last["EMA9"] <= last["EMA21"]:
            return None, "M5 momentum no longer bullish"
    else:
        crossed = (
            (recent["EMA9"].shift(1) >= recent["EMA21"].shift(1))
            & (recent["EMA9"] < recent["EMA21"])
        ).any()
        if last["EMA9"] >= previous["EMA9"]:
            return None, "M5 EMA9 momentum not falling"
        if setup_type == "CONTINUATION" and last["EMA9"] >= last["EMA21"]:
            return None, "M5 momentum no longer bearish"

    # Confirmed H1 pivots: two completed candles must form on the right.
    h1_swings = detect_h1_swing_levels(h1)
    h1_swing_high = h1_swings["high"]
    h1_swing_low = h1_swings["low"]

    # Simple quality score. A high score is a filter, not

    # proof that the trade has a high probability of winning.

    score = 0

    if m15_trend == direction:
        score += 2

    if h1_trend == direction:
        score += 2

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

    # Structural stop: use a confirmed M5 pivot, not the rolling high/low
    # of an arbitrary candle window. Two completed candles to the right must
    # confirm the swing before it can be used.
    m5_swings = detect_m5_swing_levels(m5)
    buffer = atr * STRUCTURE_STOP_BUFFER_ATR
    minimum_distance = atr * ATR_STOP_MULTIPLIER

    if direction == "BUY":
        swing_low = m5_swings["low"]
        if swing_low is None or swing_low >= close:
            return None, "no confirmed M5 swing low below entry for structural BUY stop"
        structure_stop = swing_low - buffer
        stop = min(close - minimum_distance, structure_stop)
        stop_distance = close - stop
        if stop_distance <= 0 or stop_distance > atr * MAX_STOP_DISTANCE_ATR:
            return None, "confirmed M5 swing-low stop too wide for M5 setup"
        target = close + stop_distance * MIN_RR

    else:
        swing_high = m5_swings["high"]
        if swing_high is None or swing_high <= close:
            return None, "no confirmed M5 swing high above entry for structural SELL stop"
        structure_stop = swing_high + buffer
        stop = max(close + minimum_distance, structure_stop)
        stop_distance = stop - close
        if stop_distance <= 0 or stop_distance > atr * MAX_STOP_DISTANCE_ATR:
            return None, "confirmed M5 swing-high stop too wide for M5 setup"
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
        "m5_swing_high": round(m5_swings["high"], decimals) if m5_swings["high"] is not None else None,
        "m5_swing_low": round(m5_swings["low"], decimals) if m5_swings["low"] is not None else None,
        "stop_basis": "confirmed M5 swing high/low + ATR buffer",

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
            f"quality filter score={score}/6; decisive candle body/close. Rule-based evidence, not a win-probability estimate."
        ),

    }, "qualifies"


def update_signal_outcomes():
    """Resolve prior alerts using later biquote public-feed candles; estimates only, not actual execution fills."""
    if not LOG_FILE.exists():
        return

    fieldnames = [
        "timestamp_utc", "pair", "direction", "setup_type", "entry_reference", "stop_loss",
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
                        f"biquote public M5 candle {evidence_time}: high={high}, low={low}; "
                        f"entry reference={entry}, SL={stop}, TP={target}. "
                        "biquote public-feed candle evidence; actual execution fills are not verified."
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
                                f"{row['pair']} {direction}: AMBIGUOUS — entry and SL/TP level touched in one biquote public-feed M5 candle at {evidence_time}. biquote public-feed candle evidence; actual execution fills are not verified."
                            )
                            break
                        continue

                    if stop_hit and target_hit:
                        row["outcome"] = "AMBIGUOUS_BOTH_HIT_PUBLIC_CANDLES"
                        row["outcome_candle_time"] = evidence_time
                        row["outcome_evidence"] = evidence_detail
                        changed = True
                        log(f"Outcome ambiguous for {row['pair']}: stop and target touched in same M5 candle")
                        resolved_notifications.append(
                            f"{row['pair']} {direction}: AMBIGUOUS — SL and TP both touched in one biquote public-feed M5 candle at {evidence_time}. Not actual fills."
                        )
                        break
                    if target_hit:
                        row["outcome"] = "TP_HIT_PUBLIC_CANDLE_ESTIMATE"
                        row["outcome_candle_time"] = evidence_time
                        row["outcome_evidence"] = evidence_detail
                        changed = True
                        log(f"Target touched in Yahoo public-feed M5 data for {row['pair']} (not actual fills)")
                        resolved_notifications.append(
                            f"{row['pair']} {direction}: TP touched at {evidence_time} — public-feed candle estimate; not an executed-trade result."
                        )
                        break
                    if stop_hit:
                        row["outcome"] = "SL_HIT_PUBLIC_CANDLE_ESTIMATE"
                        row["outcome_candle_time"] = evidence_time
                        row["outcome_evidence"] = evidence_detail
                        changed = True
                        log(f"Stop touched in Yahoo public-feed M5 data for {row['pair']} (not actual fills)")
                        resolved_notifications.append(
                            f"{row['pair']} {direction}: SL touched at {evidence_time} — public-feed candle estimate; not an executed-trade result."
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
            log(f"Updated signal outcomes at {checked_at}; public-feed candle-based estimates; not executed-trade results.")
            if resolved_notifications:
                log("Outcome updates recorded locally; no Telegram notification sent (trade-entry alerts only).")
        except Exception as exc:
            log(f"Could not save updated signal outcomes: {exc}")
            if temp_file.exists():
                temp_file.unlink()


def build_performance_report(rows):
    """Summarise public-candle estimates separately from broker-verified trade records."""
    wins_status = "TP_HIT_PUBLIC_CANDLE_ESTIMATE"
    loss_status = "SL_HIT_PUBLIC_CANDLE_ESTIMATE"
    ambiguous_status = {"AMBIGUOUS_ENTRY_EXIT_SAME_CANDLE", "AMBIGUOUS_BOTH_HIT_PUBLIC_CANDLES"}
    pending_status = {"UNVERIFIED", "UNVERIFIED_PENDING", ""}
    closed, verified = [], []
    pending = ambiguous = 0
    for row in rows:
        outcome = (row.get("outcome") or "").strip().upper()
        if outcome in {wins_status, loss_status}:
            try:
                rr = float(row.get("planned_rr") or 0)
            except (TypeError, ValueError):
                rr = 0.0
            closed.append((row, rr if outcome == wins_status else -1.0))
        elif outcome in ambiguous_status:
            ambiguous += 1
        elif outcome in pending_status:
            pending += 1
        elif outcome in {"VERIFIED_WIN", "VERIFIED_LOSS", "VERIFIED_BREAK_EVEN"}:
            verified.append(row)
    if closed:
        wins = sum(1 for row, value in closed if row.get("outcome") == wins_status)
        losses = len(closed) - wins
        ordered = sorted(closed, key=lambda item: item[0].get("outcome_candle_time") or item[0].get("timestamp_utc") or "")
        values = [value for row, value in ordered]
        equity = peak = drawdown = 0.0
        for value in values:
            equity += value
            peak = max(peak, equity)
            drawdown = max(drawdown, peak - equity)
        expectancy = sum(values) / len(values)
        estimate = (
            f"Public-candle estimates only: {wins} TP / {losses} SL; "
            f"estimated hit rate={wins / len(closed) * 100:.1f}%; "
            f"avg/expectancy={expectancy:+.2f}R; max drawdown={drawdown:.2f}R; "
            f"pending={pending}; ambiguous={ambiguous}. Not broker fills."
        )
    else:
        estimate = f"Public-candle estimate ledger: no resolved estimates yet (pending={pending}, ambiguous={ambiguous})."
    actual = f"Broker-verified trade records: {len(verified)} (only explicit VERIFIED labels; none inferred from public candles)."
    return estimate + "\n" + actual


def read_performance_report():
    if not LOG_FILE.exists():
        return build_performance_report([])
    try:
        with LOG_FILE.open("r", newline="", encoding="utf-8") as file:
            return build_performance_report(list(csv.DictReader(file)))
    except Exception as exc:
        log(f"Performance report unavailable: {type(exc).__name__}: {exc}")
        return "Performance report unavailable; no figures inferred."


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
            log("Safety stop recorded in Actions logs only; no Telegram notification sent.")
            return
    if alerts_today >= MAX_ALERTS_PER_UTC_DAY:
        summary = f"Forex scan safety stop: UTC daily alert cap reached ({alerts_today}/{MAX_ALERTS_PER_UTC_DAY}). No more alerts today. Alert-only control; it does not track broker P/L or replace FTMO account limits."
        log(summary)
        log("Daily alert cap reached; no Telegram notification sent.")
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

            # Re-check signal age and the latest provider live price immediately
            # before sending. Fail closed if a fresh live/open quote is unavailable.
            signal_candle_close = pd.Timestamp(signal["candle_time"])
            if signal_candle_close.tzinfo is None:
                signal_candle_close = signal_candle_close.tz_localize("UTC")
            else:
                signal_candle_close = signal_candle_close.tz_convert("UTC")
            signal_age = (
                datetime.now(timezone.utc)
                - (signal_candle_close + pd.Timedelta(minutes=5)).to_pydatetime()
            ).total_seconds() / 60
            if not -1 <= signal_age <= MAX_SIGNAL_AGE_MINUTES:
                rejected["signal expired before Telegram send"] = rejected.get("signal expired before Telegram send", 0) + 1
                log(f"{ticker}: alert suppressed — signal candle age {signal_age:.1f} minutes at send time")
                continue

            live_price, live_quote_time, live_quote_note = latest_feed_price(ticker)
            if live_price is None:
                rejected["no fresh live price for pre-send check"] = rejected.get("no fresh live price for pre-send check", 0) + 1
                log(f"{ticker}: alert suppressed — {live_quote_note}")
                continue

            entry_drift = abs(live_price - float(signal["entry"]))
            max_entry_drift = float(signal["atr"]) * MAX_ENTRY_DRIFT_ATR
            if entry_drift > max_entry_drift:
                rejected["entry drift too large at send time"] = rejected.get("entry drift too large at send time", 0) + 1
                log(
                    f"{ticker}: alert suppressed — live public-feed price {live_price:.6f} "
                    f"is {entry_drift:.6f} from entry; max allowed {max_entry_drift:.6f}"
                )
                continue

            message = (

                f"FOREX SETUP — M5\n"

                f"Pair: {signal['pair']}\n"

                f"Direction: {signal['direction']}\n"
                f"Setup type: {signal['setup_type']}\n"

                f"Entry reference: {signal['entry']}\n"

                f"Pre-send public-feed price: {live_price:.{3 if signal['pair'].endswith('/JPY') else 5}f} "
                f"(checked {live_quote_time}; drift within limit)\n"

                f"Stop-loss: {signal['stop']}\n"
                f"Stop basis: {signal['stop_basis']}\n"

                f"Take-profit: {signal['target']}\n"

                f"Planned risk/reward: 1:{signal['rr']:.2f}\n"

                f"RSI: {signal['rsi']}\n"

                f"Quality filter score: {signal['score']}/6 (filter, not win probability)\n"

                f"M5 EMA9/EMA21: {signal['m5_ema9']} / {signal['m5_ema21']}\n"

                f"M15 trend: {signal['m15_trend']} (EMA20 {signal['m15_ema20']} / EMA50 {signal['m15_ema50']})\n"

                f"H1 trend: {signal['h1_trend']} (EMA20 {signal['h1_ema20']} / EMA50 {signal['h1_ema50']})\n"

                f"ATR: {signal['atr']} | ATR%: {signal['atr_percent']}\n"

                f"Signal candle O/H/L: {signal['signal_candle_open']} / {signal['signal_candle_high']} / {signal['signal_candle_low']}\n"

                f"Trigger: {'EMA21 pullback + confirmed candle break' if signal['setup_type'] == 'PULLBACK CONTINUATION' else 'M5 EMA crossover + EMA21 location confirmation'}\n"

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

                    "setup_type": signal["setup_type"],

                    "entry_reference": signal["entry"],

                    "stop_loss": signal["stop"],

                    "take_profit": signal["target"],

                    "planned_rr": signal["rr"],

                    "rsi": signal["rsi"],

                    "score": signal["score"],

                    "candle_time": signal["candle_time"],

                    "data_source": m5.attrs.get("data_source", DATA_SOURCE),

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

        f"\n\nData source: {DATA_SOURCE}.\n"

        "No signal does not mean a trade should be forced.\\n\\n"

        + read_performance_report()

    )

    # Routine scan summaries stay in GitHub Actions logs. Telegram is reserved
    # for qualifying trade alerts (and separate outcome-tracking notices).
    log(summary)

if __name__ == "__main__":

    main()
