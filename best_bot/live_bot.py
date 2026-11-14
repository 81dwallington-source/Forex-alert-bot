"""Live, signal-only M5 forex scanner for GitHub Actions.

Signals use completed candles only. Public BiQuote prices are not broker verified.
"""
from __future__ import annotations

import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from strategy import make_signal  # noqa: E402

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
STATE_PATH = HERE / "live_state.json"
LOG_PATH = HERE / "live_signals.csv"
BASE_URL = "https://biquote.io/api/{symbol}/ohlc"
PAIRS = [
    "EURUSD","GBPUSD","USDJPY","USDCHF","USDCAD","AUDUSD","NZDUSD",
    "EURGBP","EURJPY","EURCHF","EURAUD","EURCAD","EURNZD",
    "GBPJPY","GBPCHF","GBPAUD","GBPCAD","GBPNZD",
    "AUDJPY","AUDCHF","AUDCAD","AUDNZD",
    "NZDJPY","NZDCHF","NZDCAD","CADJPY","CHFJPY",
]
MAX_ALERTS_PER_UTC_DAY = 5
MAX_ONE_PER_PAIR_PER_UTC_DAY = True
MIN_CANDLES = 220
MAX_M5_AGE_MINUTES = 10
MAX_M15_AGE_MINUTES = 25
MAX_H1_AGE_MINUTES = 75
MAX_ENTRY_DRIFT_ATR = 0.20
MAX_HOLDING_M5_BARS = 288
SPREAD_ASSUMPTION_PIPS = 1.2  # informational backtest assumption; not applied to public live quotes
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "ForexBestBot/1.0"})


def utc_now() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")


def pip_size(pair: str) -> float:
    return 0.01 if pair.endswith("JPY") else 0.0001


def fetch_bars(pair: str, interval: str, limit: int = 1000):
    url = BASE_URL.format(symbol=pair)
    response = SESSION.get(url, params={"interval": interval, "limit": limit}, timeout=20)
    response.raise_for_status()
    payload = response.json()
    bars = payload.get("bars") or []
    completed, live = [], []
    for bar in bars:
        stamp = bar.get("openTime")
        if not stamp:
            continue
        try:
            row = {
                "time": pd.to_datetime(stamp, utc=True),
                "open": float(bar["open"]),
                "high": float(bar["high"]),
                "low": float(bar["low"]),
                "close": float(bar["close"]),
            }
            if min(row[k] for k in ("open","high","low","close")) <= 0:
                continue
            if row["high"] < max(row["open"], row["close"], row["low"]):
                continue
            if row["low"] > min(row["open"], row["close"], row["high"]):
                continue
            (live if bar.get("isOpen") is True else completed).append(row)
        except (KeyError, TypeError, ValueError):
            continue
    required = MIN_CANDLES if interval == "5m" else 55
    if len(completed) < required:
        raise ValueError(f"{pair} {interval}: only {len(completed)} valid completed candles (need {required})")
    frame = pd.DataFrame(completed).set_index("time").sort_index()
    frame = frame[~frame.index.duplicated(keep="last")]
    frame = frame.rename(columns=str.lower)
    frame = frame[["open","high","low","close"]]
    if len(frame) < required:
        raise ValueError(f"{pair} {interval}: insufficient unique candles (need {required})")
    return frame, live


def validate_freshness(pair, frame, interval, max_age):
    minutes = {"5m":5, "15m":15, "1h":60}[interval]
    closed_at = frame.index[-1] + pd.Timedelta(minutes=minutes)
    age = (utc_now() - closed_at).total_seconds() / 60
    if age < -1 or age > max_age:
        raise ValueError(f"{pair} {interval}: last completed candle is stale ({age:.1f} min)")
    return age


def send_telegram(message: str) -> bool:
    if not TOKEN or not CHAT_ID:
        raise RuntimeError("Telegram secrets TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are missing")
    response = SESSION.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": message, "disable_web_page_preview": True},
        timeout=20,
    )
    response.raise_for_status()
    result = response.json()
    if not result.get("ok"):
        raise RuntimeError(f"Telegram rejected message: {result}")
    return True


def load_state():
    if not STATE_PATH.exists():
        return {"signals": [], "pending": [], "last_scan_utc": None}
    with STATE_PATH.open(encoding="utf-8") as f:
        state = json.load(f)
    state.setdefault("signals", [])
    state.setdefault("pending", [])
    return state


def save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temp.replace(STATE_PATH)


def append_log(row):
    """Upsert one CSV row per trade: PENDING is replaced by its final outcome."""
    fields = [
        "signal_time_utc","pair","direction","entry_reference","stop_loss","take_profit",
        "risk_pips","planned_rr","setup_type","origin_candle_low","origin_candle_high","outcome","outcome_time_utc","outcome_note","data_source",
    ]
    key = (str(row.get("signal_time_utc", "")), str(row.get("pair", "")), str(row.get("direction", "")))
    rows = []
    if LOG_PATH.exists():
        with LOG_PATH.open("r", newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    clean = {k: row.get(k, "") for k in fields}
    replaced = False
    for i, existing in enumerate(rows):
        existing_key = (existing.get("signal_time_utc", ""), existing.get("pair", ""), existing.get("direction", ""))
        if existing_key == key:
            rows[i] = clean
            replaced = True
            break
    if not replaced:
        rows.append(clean)
    temp = LOG_PATH.with_suffix(".csv.tmp")
    with temp.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    temp.replace(LOG_PATH)


def outcome_updates(state, candle_cache):
    """Resolve pending signals from later completed M5 bars; same-bar SL/TP is ambiguous."""
    remaining = []
    changed = False
    for trade in state["pending"]:
        pair = trade["pair"]
        try:
            m5 = candle_cache.get(pair)
            if m5 is None:
                m5, _ = fetch_bars(pair, "5m", 1000)
                candle_cache[pair] = m5
        except Exception as exc:
            print(f"Outcome check {pair} deferred: {exc}", flush=True)
            remaining.append(trade)
            continue
        signal_time = pd.Timestamp(trade["signal_time_utc"])
        future = m5.loc[m5.index > signal_time]
        if future.empty:
            remaining.append(trade)
            continue
        direction = trade["direction"]
        stop, target = float(trade["stop_loss"]), float(trade["take_profit"])
        result = None
        result_time = None
        note = ""
        for stamp, bar in future.iloc[:MAX_HOLDING_M5_BARS].iterrows():
            if direction == "BUY":
                hit_sl, hit_tp = float(bar.low) <= stop, float(bar.high) >= target
            else:
                hit_sl, hit_tp = float(bar.high) >= stop, float(bar.low) <= target
            if hit_sl and hit_tp:
                result, note = "AMBIGUOUS", "SL and TP touched in the same M5 candle; not guessed"
            elif hit_sl:
                result, note = "LOSS", "Public-feed M5 candle touched stop"
            elif hit_tp:
                result, note = "WIN", "Public-feed M5 candle touched target"
            if result:
                result_time = stamp.isoformat()
                break
        if result is None and len(future) >= MAX_HOLDING_M5_BARS:
            result, note = "EXPIRED", "No SL/TP within 24 hours of completed M5 candles"
            result_time = future.index[MAX_HOLDING_M5_BARS-1].isoformat()
        if result:
            trade.update({"outcome":result,"outcome_time_utc":result_time,"outcome_note":note})
            state["signals"].append(trade)
            append_log(trade)
            changed = True
            print(f"Resolved {pair} {direction}: {result}", flush=True)
        else:
            remaining.append(trade)
    state["pending"] = remaining
    return changed


def format_signal(pair, direction, entry, stop, target, risk_pips, stamp, rr, atr, setup, origin_low, origin_high):
    return (
        f"📣 BEST BOT — {direction} {pair}\n"
        f"Entry reference: {entry:.5f}\n"
        f"Stop loss: {stop:.5f}\n"
        f"Take profit: {target:.5f}\n"
        f"Planned R:R: 1:{rr:.1f} | Stop: {risk_pips:.1f} pips\n"
        f"PATTERN CONFIRMED: {setup}\n"
        f"Origin candle body: {origin_low:.5f}–{origin_high:.5f}\n"
        f"M5 entry + M15/H1 trend aligned\n"
        f"Completed candle: {stamp.strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"Public BiQuote feed — NOT broker-verified. Check MT4 spread and price before entry; "
        f"skip if price has moved more than 0.20 ATR. Risk no more than 0.25%."
    )


def scan():
    if not TOKEN or not CHAT_ID:
        raise RuntimeError("Missing Telegram Actions secrets")
    state = load_state()
    now = utc_now()
    today = now.strftime("%Y-%m-%d")
    daily_signals = [s for s in state["pending"] + state["signals"] if s.get("signal_time_utc","").startswith(today)]
    sent_today = len(daily_signals)
    used_pairs = {s["pair"] for s in daily_signals}
    candle_cache = {}
    changed = outcome_updates(state, candle_cache)
    sent = 0
    errors = 0
    print(f"Starting scan {now.isoformat()} across {len(PAIRS)} pairs", flush=True)

    for pair in PAIRS:
        if sent_today + sent >= MAX_ALERTS_PER_UTC_DAY:
            break
        if MAX_ONE_PER_PAIR_PER_UTC_DAY and pair in used_pairs:
            continue
        try:
            m5, live = fetch_bars(pair, "5m")
            m15, _ = fetch_bars(pair, "15m")
            h1, _ = fetch_bars(pair, "1h")
            candle_cache[pair] = m5
            validate_freshness(pair, m5, "5m", MAX_M5_AGE_MINUTES)
            validate_freshness(pair, m15, "15m", MAX_M15_AGE_MINUTES)
            validate_freshness(pair, h1, "1h", MAX_H1_AGE_MINUTES)
            pip = pip_size(pair)
            sig = make_signal(m5, m15, h1, pip)
            if not sig:
                continue
            # Fail closed: only the user's three approved patterns may alert.
            allowed_setups = {
                "TWO_SIDED_SWEEP_ORIGIN_RETEST",
                "LIQUIDITY_SWEEP_RANGE_RETEST",
                "IMPULSE_ORIGIN_RETEST",
            }
            if sig.get("setup") not in allowed_setups:
                print(f"{pair}: rejected — setup is not one of the three approved patterns", flush=True)
                continue
            stamp = m5.index[-1]
            # Sanity-check the reference against the provider's current open candle.
            live_bars = [b for b in live if b.get("time") and b["time"] >= stamp]
            if not live_bars:
                print(f"{pair}: no current open quote; no alert", flush=True)
                continue
            live_price = float(max(live_bars, key=lambda b:b["time"])["close"])
            atr = float(sig["atr"])
            if abs(live_price - float(sig["signal_close"])) > MAX_ENTRY_DRIFT_ATR * atr:
                print(f"{pair}: current price drifted > {MAX_ENTRY_DRIFT_ATR} ATR; rejected", flush=True)
                continue
            entry = float(sig["signal_close"])
            row = {
                "signal_time_utc": stamp.isoformat(), "pair":pair,
                "direction":sig["direction"], "entry_reference":entry,
                "stop_loss":float(sig["stop"]), "take_profit":float(sig["target"]),
                "risk_pips":float(sig["risk_pips"]), "planned_rr":float(sig["rr"]),
                "setup_type":sig["setup"], "origin_candle_low":float(sig["origin_low"]),
                "origin_candle_high":float(sig["origin_high"]),
                "outcome":"PENDING", "outcome_time_utc":"", "outcome_note":"",
                "data_source":"BiQuote public FX OHLC; not broker-verified",
            }
            message = format_signal(pair, sig["direction"], entry, row["stop_loss"],
                                    row["take_profit"], row["risk_pips"], stamp,
                                    row["planned_rr"], atr, sig["setup"], sig["origin_low"], sig["origin_high"])
            send_telegram(message)
            state["pending"].append(row)
            append_log(row)
            used_pairs.add(pair)
            sent += 1
            changed = True
            print(f"Telegram signal sent: {pair} {sig['direction']}", flush=True)
        except Exception as exc:
            errors += 1
            print(f"{pair}: rejected/scan error: {type(exc).__name__}: {exc}", flush=True)

    state["last_scan_utc"] = now.isoformat()
    save_state(state)
    print(
        f"Scan complete. Telegram signals sent: {sent}; pair errors/rejections: {errors}; "
        f"pending outcomes: {len(state['pending'])}; completed outcomes logged: {len(state['signals'])}.",
        flush=True,
    )
    return sent


if __name__ == "__main__":
    scan()
