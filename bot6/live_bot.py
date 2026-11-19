"""Bot 6: 15-minute EMA pullback continuation scanner. Signal-only."""
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
PAIRS = [
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD",
    "EURGBP", "EURJPY", "EURCHF", "EURAUD", "EURCAD", "EURNZD",
    "GBPJPY", "GBPCHF", "GBPAUD", "GBPCAD", "GBPNZD",
    "AUDJPY", "AUDCHF", "AUDCAD", "AUDNZD",
    "NZDJPY", "NZDCHF", "NZDCAD", "CADJPY", "CHFJPY",
]
MAX_ALERTS_PER_UTC_DAY = 5
MAX_ONE_PER_PAIR_PER_UTC_DAY = True
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "ForexBot6/1.0"})


def now_utc():
    return pd.Timestamp.now(tz="UTC")


def pip_size(pair: str) -> float:
    return 0.01 if pair.endswith("JPY") else 0.0001


def fetch_bars(pair: str, interval: str, required: int):
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
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise ValueError(f"{pair} {interval}: no completed candles returned")
    frame = frame.set_index("time").sort_index()
    frame = frame[~frame.index.duplicated(keep="last")]
    if len(frame) < required:
        raise ValueError(f"{pair} {interval}: only {len(frame)} completed candles; need {required}")
    return frame


def check_fresh(frame: pd.DataFrame, interval_minutes: int, max_age_minutes: int):
    candle_close = frame.index[-1] + pd.Timedelta(minutes=interval_minutes)
    age = (now_utc() - candle_close).total_seconds() / 60
    if age < -1 or age > max_age_minutes:
        raise ValueError(f"stale completed candle ({age:.1f} minutes old)")


def indicators(frame: pd.DataFrame):
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


def get_signal(m15: pd.DataFrame, h1: pd.DataFrame, pair: str):
    m = indicators(m15)
    h = indicators(h1)
    # All decisions use completed candles only.
    c = m.iloc[-1]
    p = m.iloc[-2]
    hcur = h.iloc[-1]
    if pd.isna(c["atr"]) or c["atr"] <= 0:
        return None

    # Higher-timeframe trend must be unambiguous and aligned.
    h1_up = hcur["close"] > hcur["ema50"] > hcur["ema200"] and hcur["ema21"] > hcur["ema50"]
    h1_down = hcur["close"] < hcur["ema50"] < hcur["ema200"] and hcur["ema21"] < hcur["ema50"]
    m15_up = c["close"] > c["ema50"] > c["ema200"] and c["ema21"] > c["ema50"]
    m15_down = c["close"] < c["ema50"] < c["ema200"] and c["ema21"] < c["ema50"]

    atr = float(c["atr"])
    body = abs(float(c["close"]) - float(c["open"]))
    candle_range = max(float(c["high"] - c["low"]), 1e-12)
    lower_wick = min(float(c["open"]), float(c["close"])) - float(c["low"])
    upper_wick = float(c["high"]) - max(float(c["open"]), float(c["close"]))

    direction = None
    if h1_up and m15_up:
        # Pullback within last 4 completed candles must tag the EMA21/EMA50 band.
        recent = m.iloc[-4:-1]
        touched = ((recent["low"] <= recent["ema21"] + 0.15 * recent["atr"]) &
                   (recent["low"] >= recent["ema50"] - 0.25 * recent["atr"])).any()
        rejection = (
            c["close"] > c["open"]
            and c["close"] > p["high"]
            and lower_wick >= max(body * 0.5, candle_range * 0.15)
            and c["close"] > c["ema21"]
            and body >= 0.20 * atr
        )
        if touched and rejection:
            direction = "BUY"
    elif h1_down and m15_down:
        recent = m.iloc[-4:-1]
        touched = ((recent["high"] >= recent["ema21"] - 0.15 * recent["atr"]) &
                   (recent["high"] <= recent["ema50"] + 0.25 * recent["atr"])).any()
        rejection = (
            c["close"] < c["open"]
            and c["close"] < p["low"]
            and upper_wick >= max(body * 0.5, candle_range * 0.15)
            and c["close"] < c["ema21"]
            and body >= 0.20 * atr
        )
        if touched and rejection:
            direction = "SELL"

    if not direction:
        return None

    # Structural stop beyond the recent confirmed M15 swing, with an ATR buffer.
    recent_swing = m.iloc[-7:-1]
    buffer = max(0.15 * atr, 1.5 * pip_size(pair))
    entry = float(c["close"])
    if direction == "BUY":
        swing = float(recent_swing["low"].min())
        stop = swing - buffer
        risk = entry - stop
    else:
        swing = float(recent_swing["high"].max())
        stop = swing + buffer
        risk = stop - entry
    # Reject poor geometry; never squeeze the stop to force a trade.
    if risk < 0.65 * atr or risk > 2.5 * atr:
        return None
    target = entry + 1.5 * risk if direction == "BUY" else entry - 1.5 * risk
    return {
        "direction": direction,
        "entry": entry,
        "stop": stop,
        "target": target,
        "risk_pips": risk / pip_size(pair),
        "rr": 1.5,
        "atr": atr,
        "signal_time": m.index[-1].isoformat(),
    }


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
            bars = fetch_bars(trade["pair"], "15m", 3)
        except Exception as exc:
            print(f"Outcome check deferred for {trade['pair']}: {exc}", flush=True)
            remaining.append(trade)
            continue
        future = bars.loc[bars.index > pd.Timestamp(trade["signal_time_utc"])]
        result = None
        result_time = ""
        note = ""
        for stamp, candle in future.iloc[:96].iterrows():
            if trade["direction"] == "BUY":
                hit_sl = float(candle["low"]) <= float(trade["stop_loss"])
                hit_tp = float(candle["high"]) >= float(trade["take_profit"])
            else:
                hit_sl = float(candle["high"]) >= float(trade["stop_loss"])
                hit_tp = float(candle["low"]) <= float(trade["take_profit"])
            if hit_sl and hit_tp:
                result, note = "AMBIGUOUS", "Both SL and TP touched in same 15m candle; not guessed"
            elif hit_sl:
                result, note = "LOSS", "Completed public-feed 15m candle touched stop"
            elif hit_tp:
                result, note = "WIN", "Completed public-feed 15m candle touched target"
            if result:
                result_time = stamp.isoformat()
                break
        if result:
            trade.update({"outcome": result, "outcome_time_utc": result_time, "outcome_note": note})
            state["completed"].append(trade)
            write_log(trade)
            print(f"Outcome {trade['pair']} {trade['direction']}: {result}", flush=True)
        elif len(future) >= 96:
            trade.update({"outcome": "EXPIRED", "outcome_time_utc": future.index[95].isoformat(),
                          "outcome_note": "No SL/TP within 24 hours of completed M15 candles"})
            state["completed"].append(trade)
            write_log(trade)
        else:
            remaining.append(trade)
    state["pending"] = remaining


def scan():
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
    print(f"BOT 6 starting 15-minute pullback scan: {len(PAIRS)} pairs", flush=True)
    for pair in PAIRS:
        if MAX_ONE_PER_PAIR_PER_UTC_DAY and pair in used_pairs:
            continue
        if len(todays) + sent >= MAX_ALERTS_PER_UTC_DAY:
            break
        try:
            # BiQuote currently returns 168 completed H1 candles even with limit=500.
            # Require 150; keep the H1 EMA21/50/200 alignment filter intact.
            # Match the H1 minimum: this feed can return fewer than 180 completed M15 bars.
            # EMA200 and both timeframe trend-alignment filters remain in force.
            m15 = fetch_bars(pair, "15m", 150)
            h1 = fetch_bars(pair, "1h", 150)
            check_fresh(m15, 15, 25)
            check_fresh(h1, 60, 75)
            analysed += 1
            signal = get_signal(m15, h1, pair)
            if not signal:
                no_setup += 1
                continue
            trade = {
                "signal_time_utc": signal["signal_time"], "pair": pair,
                "direction": signal["direction"], "entry": round(signal["entry"], 6),
                "stop_loss": round(signal["stop"], 6), "take_profit": round(signal["target"], 6),
                "risk_pips": round(signal["risk_pips"], 1), "planned_rr": signal["rr"],
                "outcome": "PENDING", "outcome_time_utc": "", "outcome_note": "",
                "data_source": "BiQuote public FX OHLC; not broker-verified",
            }
            message = (
                f"📣 BOT 6 — 15M PULLBACK {trade['direction']} {pair}\n"
                f"Entry: {trade['entry']}\nStop loss: {trade['stop_loss']}\n"
                f"Take profit: {trade['take_profit']}\n"
                f"Risk: {trade['risk_pips']} pips | Planned R:R 1:{trade['planned_rr']}\n"
                f"15M EMA21/50/200 trend + confirmed EMA21/50 pullback rejection\n"
                f"H1 trend aligned | Completed 15M candle: {trade['signal_time_utc']}\n"
                f"Source: public BiQuote feed, not broker-verified. Check broker price/spread before entry. "
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


if __name__ == "__main__":
    scan()
