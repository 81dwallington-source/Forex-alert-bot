"""Bot 6: institutional-style liquidity sweep and FVG retest scanner. Signal-only."""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
BASE_URL = "https://biquote.io/api/{symbol}/ohlc"
STATE_PATH = HERE / "state.json"
LOG_PATH = HERE / "signals.csv"
EVIDENCE_DIR = HERE / "evidence"
PAIRS = [
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD",
    "EURGBP", "EURJPY", "EURCHF", "EURAUD", "EURCAD", "EURNZD",
    "GBPJPY", "GBPCHF", "GBPAUD", "GBPCAD", "GBPNZD",
    "AUDJPY", "AUDCHF", "AUDCAD", "AUDNZD",
    "NZDJPY", "NZDCHF", "NZDCAD", "CADJPY", "CHFJPY",
]
MAX_ALERTS_PER_UTC_DAY = 5
MAX_ONE_PER_PAIR_PER_UTC_DAY = True
MAX_M5_CANDLE_AGE_MINUTES = 5
MAX_ENTRY_DRIFT_PIPS = 2.0
MAX_SIMULTANEOUS_JPY_TRADES = 1
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "ForexBot6-Institutional/2.0"})


def now_utc():
    return pd.Timestamp.now(tz="UTC")


def pip_size(pair: str) -> float:
    return 0.01 if pair.endswith("JPY") else 0.0001


def _normalise_bars(rows, pair: str, interval: str, required: int, source: str):
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise ValueError(f"{pair} {interval}: {source} returned no completed candles")
    frame = frame.set_index("time").sort_index()
    frame = frame[~frame.index.duplicated(keep="last")]
    if len(frame) < required:
        raise ValueError(f"{pair} {interval}: {source} returned {len(frame)} candles; need {required}")
    frame.attrs["source"] = source
    return frame


def _fetch_yahoo_bars(pair: str, interval: str, required: int):
    """Secondary intraday OHLC source; live BiQuote tick remains the entry cross-check."""
    yahoo_interval = {"5m": "5m", "15m": "15m", "1h": "60m"}.get(interval)
    minutes = {"5m": 5, "15m": 15, "1h": 60}.get(interval)
    if not yahoo_interval or not minutes:
        raise ValueError(f"unsupported Yahoo interval: {interval}")
    symbol = f"{pair}=X"
    period = "5d" if interval in ("5m", "15m") else "1mo"
    response = SESSION.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
        params={"range": period, "interval": yahoo_interval, "includePrePost": "true"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    result = (payload.get("chart", {}).get("result") or [None])[0]
    if not result:
        raise ValueError(f"Yahoo returned no chart for {symbol}: {payload.get('chart', {}).get('error')}")
    timestamps = result.get("timestamp") or []
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    rows = []
    current = now_utc()
    for i, stamp in enumerate(timestamps):
        try:
            open_time = pd.to_datetime(stamp, unit="s", utc=True)
            if open_time + pd.Timedelta(minutes=minutes) > current:
                continue
            row = {
                "time": open_time,
                "open": float(quote["open"][i]),
                "high": float(quote["high"][i]),
                "low": float(quote["low"][i]),
                "close": float(quote["close"][i]),
            }
            if any(pd.isna(row[k]) for k in ("open", "high", "low", "close")):
                continue
            if row["low"] <= 0 or row["high"] < max(row["open"], row["close"]) or row["low"] > min(row["open"], row["close"]):
                continue
            rows.append(row)
        except (IndexError, KeyError, TypeError, ValueError, OverflowError):
            continue
    source = "Yahoo Finance intraday OHLC (secondary; not broker-verified)"
    frame = _normalise_bars(rows, pair, interval, required, source)
    age = (now_utc() - (frame.index[-1] + pd.Timedelta(minutes=minutes))).total_seconds() / 60
    allowed_age = {"5m": 5, "15m": 25, "1h": 75}[interval]
    if age < -1 or age > allowed_age:
        raise ValueError(f"Yahoo {interval} candles are stale ({age:.1f} minutes old)")
    return frame


def fetch_bars(pair: str, interval: str, required: int):
    """Use BiQuote first; fall back to independent Yahoo candles only when BiQuote is stale/unusable."""
    biquote_error = None
    try:
        response = SESSION.get(
            BASE_URL.format(symbol=pair),
            params={"interval": interval, "limit": 500},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        rows = []
        for bar in payload.get("bars") or []:
            if bar.get("isOpen") is True:
                continue
            try:
                row = {
                    "time": pd.to_datetime(bar["openTime"], utc=True),
                    "open": float(bar["open"]),
                    "high": float(bar["high"]),
                    "low": float(bar["low"]),
                    "close": float(bar["close"]),
                }
                if row["low"] <= 0 or row["high"] < max(row["open"], row["close"]) or row["low"] > min(row["open"], row["close"]):
                    continue
                rows.append(row)
            except (KeyError, TypeError, ValueError):
                continue
        frame = _normalise_bars(rows, pair, interval, required, "BiQuote OHLC")
        minutes = {"5m": 5, "15m": 15, "1h": 60}[interval]
        allowed_age = {"5m": 5, "15m": 25, "1h": 75}[interval]
        age = (now_utc() - (frame.index[-1] + pd.Timedelta(minutes=minutes))).total_seconds() / 60
        if age < -1 or age > allowed_age:
            raise ValueError(f"BiQuote completed candles stale ({age:.1f} minutes old)")
        return frame
    except Exception as exc:
        biquote_error = exc
    try:
        fallback = _fetch_yahoo_bars(pair, interval, required)
        print(f"BOT6_FEED_FALLBACK {pair} {interval}: BiQuote unavailable ({biquote_error}); using {fallback.attrs.get('source')}", flush=True)
        return fallback
    except Exception as fallback_error:
        raise ValueError(
            f"{pair} {interval}: BiQuote failed ({biquote_error}); Yahoo fallback failed ({fallback_error})"
        ) from fallback_error

def check_fresh(frame: pd.DataFrame, interval_minutes: int, max_age_minutes: int):
    candle_close = frame.index[-1] + pd.Timedelta(minutes=interval_minutes)
    age = (now_utc() - candle_close).total_seconds() / 60
    if age < -1 or age > max_age_minutes:
        raise ValueError(f"stale completed candle ({age:.1f} minutes old)")



def fetch_live_quote(pair: str):
    """Require a fresh live tick as a sanity check against the completed M5 close."""
    response = SESSION.get(f"https://biquote.io/api/{pair}", params={"allowStale": "false"}, timeout=15)
    response.raise_for_status()
    quote = response.json()
    mid = float(quote.get("mid") or 0)
    age = float(quote.get("quoteAgeSeconds", 999999))
    stamp = pd.to_datetime(quote.get("timestamp"), utc=True, errors="coerce")
    if mid <= 0 or pd.isna(stamp) or age > 120 or quote.get("stale") is True:
        raise ValueError(f"{pair}: live quote missing/stale (age={age:.0f}s)")
    if quote.get("marketState") not in (None, "open"):
        raise ValueError(f"{pair}: market is not open ({quote.get('marketState')})")
    return {"mid": mid, "timestamp": stamp.isoformat(), "age_seconds": age,
            "source": quote.get("source", "BiQuote live tick")}


def diagnose_primary_feed():
    """Separate live-tick health from candle-collector health before the full scan."""
    pair = "EURUSD"
    try:
        quote = fetch_live_quote(pair)
        quote_note = f"live_tick_age={quote['age_seconds']:.0f}s price={quote['mid']}"
    except Exception as exc:
        quote_note = f"live_tick_error={type(exc).__name__}: {exc}"
    try:
        bars = fetch_bars(pair, "5m", 2)
        latest_open = bars.index[-1]
        age = (now_utc() - (latest_open + pd.Timedelta(minutes=5))).total_seconds() / 60
        candle_note = f"latest_completed_M5={latest_open.isoformat()} age={age:.1f}m"
    except Exception as exc:
        candle_note = f"M5_error={type(exc).__name__}: {exc}"
    print(f"BOT6_FEED_DIAGNOSTIC {pair}: {quote_note}; {candle_note}", flush=True)


def confirmed_swing_stop(frame: pd.DataFrame, direction: str, entry: float, buffer: float):
    """Use the nearest confirmed 2-left/2-right pivot, not an arbitrary six-bar extreme."""
    window = frame.iloc[-42:-2]
    pivots = []
    for j in range(2, len(window) - 2):
        row = window.iloc[j]
        if direction == "SELL":
            valid = (row["high"] > window.iloc[j-1]["high"] and
                     row["high"] > window.iloc[j-2]["high"] and
                     row["high"] >= window.iloc[j+1]["high"] and
                     row["high"] >= window.iloc[j+2]["high"] and row["high"] > entry)
            if valid:
                pivots.append(float(row["high"]))
        else:
            valid = (row["low"] < window.iloc[j-1]["low"] and
                     row["low"] < window.iloc[j-2]["low"] and
                     row["low"] <= window.iloc[j+1]["low"] and
                     row["low"] <= window.iloc[j+2]["low"] and row["low"] < entry)
            if valid:
                pivots.append(float(row["low"]))
    if not pivots:
        raise ValueError(f"No confirmed M5 swing available for {direction} stop")
    pivot = pivots[-1]
    return (pivot + buffer if direction == "SELL" else pivot - buffer), pivot


def save_evidence(trade, m5, m15, h1, quote, signal):
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    def serialise(frame, count):
        data = frame.tail(count).reset_index()
        data["time"] = data["time"].astype(str)
        return data.to_dict(orient="records")
    payload = {
        "trade": trade, "live_quote": quote,
        "breakout_level": signal["breakout_level"],
        "breakout_time": signal["breakout_time"],
        "confirmed_swing": signal["confirmed_swing"],
        "candles": {"M5": serialise(m5, 60), "M15": serialise(m15, 40)},
    }
    filename = trade["signal_time_utc"].replace(":", "").replace("+00:00", "Z")
    (EVIDENCE_DIR / f"{filename}_{trade['pair']}.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )


def indicators(frame: pd.DataFrame):
    # Backtests may pass causally precomputed columns to avoid recalculating
    # the full EMA/ATR history at every walk-forward candle.
    if {"ema21", "ema50", "ema200", "atr"}.issubset(frame.columns):
        return frame
    x = frame.copy()
    for period in (21, 50, 200):
        x[f"ema{period}"] = x["close"].ewm(span=period, adjust=False).mean()
    previous_close = x["close"].shift(1)
    tr = pd.concat([
        x["high"] - x["low"],
        (x["high"] - previous_close).abs(),
        (x["low"] - previous_close).abs(),
    ], axis=1).max(axis=1)
    x["atr"] = tr.rolling(14).mean()
    return x


def get_signal(m5: pd.DataFrame, m15: pd.DataFrame, h1: pd.DataFrame | None, pair: str, verbose: bool = True):
    """Diagram-only setup: sharp structure-breaking impulse, its immediately
    preceding origin candle, retest of that exact candle zone, then M5 rejection.
    H1 is accepted for backtest compatibility but is deliberately not used."""
    if len(m15) < 10 or len(m5) < 15:
        return None

    mt = m15
    c5 = m5.iloc[-1]
    prev5 = m5.iloc[-2]
    pip = pip_size(pair)
    n = len(mt)

    # Look back for the most recent clear impulse. No EMA/RSI/ADX/H1/ATR gates.
    # The origin is the candle immediately before the impulse, as in the user's diagram.
    first = max(5, n - 40)
    for bi in range(n - 2, first, -1):
        impulse = mt.iloc[bi]
        origin = mt.iloc[bi - 1]
        prior = mt.iloc[bi - 6:bi]
        if len(prior) != 5:
            continue

        if float(impulse["close"]) < float(impulse["open"]):
            direction = "SELL"
            structure = float(prior["low"].min())
            if float(impulse["close"]) >= structure:
                continue
            # The last bullish/base candle immediately before the bearish impulse.
            if float(origin["close"]) > float(origin["open"]):
                zone_low, zone_high = float(origin["open"]), float(origin["high"])
            else:
                body = abs(float(origin["close"]) - float(origin["open"]))
                full = max(float(origin["high"]) - float(origin["low"]), pip)
                if body / full > 0.35:
                    continue
                zone_low, zone_high = float(origin["open"]), float(origin["high"])
        elif float(impulse["close"]) > float(impulse["open"]):
            direction = "BUY"
            structure = float(prior["high"].max())
            if float(impulse["close"]) <= structure:
                continue
            # Exact reverse: last bearish/base candle before bullish impulse.
            if float(origin["close"]) < float(origin["open"]):
                zone_low, zone_high = float(origin["low"]), float(origin["open"])
            else:
                body = abs(float(origin["close"]) - float(origin["open"]))
                full = max(float(origin["high"]) - float(origin["low"]), pip)
                if body / full > 0.35:
                    continue
                zone_low, zone_high = float(origin["low"]), float(origin["open"])
        else:
            continue

        if zone_high <= zone_low:
            continue

        # Only candles after the impulse may retest its origin zone.
        impulse_close_time = mt.index[bi] + pd.Timedelta(minutes=15)
        after = m5.loc[m5.index >= impulse_close_time]
        if len(after) < 2:
            continue
        touched = ((after["low"] <= zone_high) & (after["high"] >= zone_low))
        if not touched.any():
            continue

        # The latest completed M5 candle must itself reject the retested zone.
        if direction == "SELL":
            rejected = (
                float(c5["close"]) < float(c5["open"]) and
                float(c5["high"]) >= zone_low and
                float(c5["close"]) < zone_low
            )
        else:
            rejected = (
                float(c5["close"]) > float(c5["open"]) and
                float(c5["low"]) <= zone_high and
                float(c5["close"]) > zone_high
            )
        if not rejected:
            continue

        entry = float(c5["close"])
        recent = m5.iloc[-15:-1]
        if recent.empty:
            continue
        # Place the stop beyond the recent M5 swing and the origin boundary.
        recent_range = float((recent["high"] - recent["low"]).mean())
        buffer = max(2.0 * pip, 0.10 * recent_range)
        if direction == "SELL":
            swing = float(recent["high"].max())
            stop = max(swing, zone_high) + buffer
            risk = stop - entry
            target = entry - 1.5 * risk
        else:
            swing = float(recent["low"].min())
            stop = min(swing, zone_low) - buffer
            risk = entry - stop
            target = entry + 1.5 * risk
        if risk <= 0:
            continue

        return {
            "direction": direction, "entry": entry, "stop": stop, "target": target,
            "risk_pips": risk / pip, "rr": 1.5, "atr": recent_range,
            "breakout_level": structure, "breakout_time": mt.index[bi].isoformat(),
            "confirmed_swing": swing, "signal_time": (m5.index[-1] + pd.Timedelta(minutes=5)).isoformat(),
            "pullback_time": after.index[touched.to_numpy().nonzero()[0][-1]].isoformat(),
            "entry_zone_low": zone_low, "entry_zone_high": zone_high,
            "origin_time": mt.index[bi - 1].isoformat(),
        }
    return None

def send_telegram(message: str):
    if not TOKEN or not CHAT_ID:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID repository secrets")
    response = SESSION.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": message, "disable_web_page_preview": True},
        timeout=20,
    )
    response.raise_for_status()
    result = response.json()
    if not result.get("ok"):
        raise RuntimeError("Telegram did not confirm message delivery")


def load_state():
    if not STATE_PATH.exists():
        return {"pending": [], "completed": [], "last_scan_utc": None}
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    state.setdefault("pending", [])
    state.setdefault("completed", [])
    return state


def save_state(state):
    temp = STATE_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temp.replace(STATE_PATH)


def write_log(trade):
    fields = ["signal_time_utc", "pair", "direction", "entry", "stop_loss", "take_profit",
              "risk_pips", "planned_rr", "outcome", "outcome_time_utc", "outcome_note",
              "data_source"]
    rows = []
    if LOG_PATH.exists():
        with LOG_PATH.open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    key = (trade["signal_time_utc"], trade["pair"], trade["direction"])
    new_row = {k: trade.get(k, "") for k in fields}
    for i, row in enumerate(rows):
        if (row.get("signal_time_utc"), row.get("pair"), row.get("direction")) == key:
            rows[i] = new_row
            break
    else:
        rows.append(new_row)
    temp = LOG_PATH.with_suffix(".csv.tmp")
    with temp.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    temp.replace(LOG_PATH)


def resolve_outcomes(state):
    remaining = []
    for trade in state["pending"]:
        try:
            bars = fetch_bars(trade["pair"], "5m", 3)
        except Exception as exc:
            print(f"Outcome check deferred for {trade['pair']}: {exc}", flush=True)
            remaining.append(trade)
            continue
        future = bars.loc[bars.index > pd.Timestamp(trade["signal_time_utc"])]
        result = None
        result_time = ""
        note = ""
        for stamp, candle in future.iloc[:288].iterrows():
            if trade["direction"] == "BUY":
                hit_sl = float(candle["low"]) <= float(trade["stop_loss"])
                hit_tp = float(candle["high"]) >= float(trade["take_profit"])
            else:
                hit_sl = float(candle["high"]) >= float(trade["stop_loss"])
                hit_tp = float(candle["low"]) <= float(trade["take_profit"])
            if hit_sl and hit_tp:
                result, note = "AMBIGUOUS", "Both SL and TP touched in same M5 candle; not guessed"
            elif hit_sl:
                result, note = "LOSS", "Completed public-feed M5 candle touched stop"
            elif hit_tp:
                result, note = "WIN", "Completed public-feed M5 candle touched target"
            if result:
                result_time = stamp.isoformat()
                break
        if result:
            trade.update({"outcome": result, "outcome_time_utc": result_time, "outcome_note": note})
            state["completed"].append(trade)
            write_log(trade)
            print(f"Outcome {trade['pair']} {trade['direction']}: {result}", flush=True)
        elif len(future) >= 288:
            trade.update({"outcome": "EXPIRED", "outcome_time_utc": future.index[287].isoformat(),
                          "outcome_note": "No SL/TP within 24 hours of completed M5 candles"})
            state["completed"].append(trade)
            write_log(trade)
        else:
            remaining.append(trade)
    state["pending"] = remaining


def scan():
    # Emergency kill switch: a committed STOP file prevents pending/new runners
    # from sending signals while the strategy is under review.
    if (HERE / "STOP").exists():
        print("BOT 6 PAUSED: bot6/STOP exists; no scan or Telegram alerts sent.", flush=True)
        return
    if not TOKEN or not CHAT_ID:
        raise RuntimeError("Telegram secrets are not configured")
    state = load_state()
    today = now_utc().strftime("%Y-%m-%d")
    todays = [t for t in state["pending"] + state["completed"] if t.get("signal_time_utc", "").startswith(today)]
    used_pairs = {t["pair"] for t in todays}
    resolve_outcomes(state)
    sent = 0
    errors = 0
    analysed = 0
    no_setup = 0
    error_details = []
    print(f"BOT 6 starting impulse-origin retest scan: {len(PAIRS)} pairs | UTC={now_utc().isoformat()}", flush=True)
    diagnose_primary_feed()
    for pair in PAIRS:
        if MAX_ONE_PER_PAIR_PER_UTC_DAY and pair in used_pairs:
            continue
        if len(todays) + sent >= MAX_ALERTS_PER_UTC_DAY:
            break
        try:
            m5 = fetch_bars(pair, "5m", 210)
            m15 = fetch_bars(pair, "15m", 150)
            check_fresh(m5, 5, MAX_M5_CANDLE_AGE_MINUTES)
            check_fresh(m15, 15, 25)
            analysed += 1
            print(f"BOT6_DATA_OK {pair}: M5/M15/H1 fresh; evaluating impulse-origin retest", flush=True)
            signal = get_signal(m5, m15, None, pair)
            if not signal:
                no_setup += 1
                print(f"BOT6_NO_SIGNAL {pair}: no complete qualified origin/retest/confirmation sequence", flush=True)
                continue
            # One open JPY-cross position at a time: avoid stacking the same
            # JPY exposure across pairs such as AUDJPY and EURJPY.
            if pair.endswith("JPY"):
                open_jpy = sum(1 for t in state["pending"] if t.get("pair", "").endswith("JPY"))
                if open_jpy >= MAX_SIMULTANEOUS_JPY_TRADES:
                    raise ValueError(
                        f"{pair}: rejected; {open_jpy} JPY-cross position(s) already pending"
                    )

            quote = fetch_live_quote(pair)
            candle_close = pd.Timestamp(signal["signal_time"])
            quote_time = pd.Timestamp(quote["timestamp"])
            if quote_time < candle_close:
                raise ValueError(f"{pair}: live quote predates the completed M5 candle")
            # Do not chase a move. The live mid must still be very close to
            # the planned confirmation-candle close used as the entry.
            price_gap = abs(float(quote["mid"]) - float(signal["entry"]))
            max_price_gap = min(
                MAX_ENTRY_DRIFT_PIPS * pip_size(pair),
                0.15 * float(signal["atr"])
            )
            if price_gap > max_price_gap:
                raise ValueError(
                    f"{pair}: entry has moved {price_gap / pip_size(pair):.1f} pips "
                    f"from signal close (max {max_price_gap / pip_size(pair):.1f}); rejected"
                )
            trade = {
                "signal_time_utc": signal["signal_time"], "pair": pair,
                "direction": signal["direction"], "entry": round(signal["entry"], 6),
                "stop_loss": round(signal["stop"], 6), "take_profit": round(signal["target"], 6),
                "risk_pips": round(signal["risk_pips"], 1), "planned_rr": signal["rr"],
                "outcome": "PENDING", "outcome_time_utc": "", "outcome_note": "",
                "data_source": " | ".join([m5.attrs.get("source", "unknown"), m15.attrs.get("source", "unknown")]) + " + BiQuote live tick; not broker-verified",
                "live_quote_mid": round(quote["mid"], 6),
                "live_quote_time_utc": quote["timestamp"],
                "confirmed_swing": round(signal["confirmed_swing"], 6),
                "pullback_time_utc": signal["pullback_time"],
                "entry_zone_low": round(signal["entry_zone_low"], 6),
                "entry_zone_high": round(signal["entry_zone_high"], 6),
            }
            save_evidence(trade, m5, m15, None, quote, signal)
            candle_close = pd.Timestamp(trade["signal_time_utc"])
            candle_age = max(0.0, (now_utc() - candle_close).total_seconds() / 60)
            message = (
                f"📣 BOT 6 — IMPULSE ORIGIN + RETEST {trade['direction']} {pair}\n"
                f"Entry: {trade['entry']}\nStop loss: {trade['stop_loss']}\n"
                f"Take profit: {trade['take_profit']}\n"
                f"Risk: {trade['risk_pips']} pips | Planned R:R 1:{trade['planned_rr']}\n"
                f"Diagram setup: impulse + exact origin-candle retest + rejection\n"
                f"Origin zone: {signal['entry_zone_low']:.6f}–{signal['entry_zone_high']:.6f} | Confirmed swing: {trade['confirmed_swing']}\n"
                f"M5 rejection candle close (UTC): {candle_close.isoformat()} | Data age: {candle_age:.1f} min\n"
                f"Live tick check: {quote['mid']} ({quote['age_seconds']:.0f}s old)\n"
                f"Source: {m5.attrs.get('source', 'unknown')} + BiQuote live tick check; not broker-verified. Check broker price/spread before entry. "
                f"Manual signal only; risk no more than 0.25%."
            )
            send_telegram(message)
            state["pending"].append(trade)
            write_log(trade)
            used_pairs.add(pair)
            sent += 1
            print(f"BOT 6 alert sent: {pair} {signal['direction']}", flush=True)
        except Exception as exc:
            errors += 1
            detail = f"{pair}: {type(exc).__name__}: {exc}"
            error_details.append(detail)
            print(f"BOT 6 data/error: {detail}", flush=True)
    state["last_scan_utc"] = now_utc().isoformat()
    save_state(state)
    print(f"BOT 6 scan complete. Alerts sent={sent}; pairs analysed={analysed}; no setup={no_setup}; data/errors={errors}; pending={len(state['pending'])}; outcomes={len(state['completed'])}", flush=True)
    if error_details:
        print("BOT 6 error summary: " + " | ".join(error_details[:8]), flush=True)
    # Do not let GitHub report a green scan when every pair failed data validation.
    if errors > 0 and analysed == 0:
        raise RuntimeError(
            f"Bot 6 unavailable: zero pairs analysed and {errors} data errors. "
            "No signals were sent; inspect BOT6_FEED_DIAGNOSTIC and error summary."
        )


if __name__ == "__main__":
    scan()
