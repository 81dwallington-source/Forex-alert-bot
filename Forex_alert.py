import csv
import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# Forex Alert Bot v3 — signal-only, no automatic trading.
# Yahoo Finance data is not broker-verified or guaranteed real-time.

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
STATE_FILE = Path("signals_state.json")
LOG_FILE = Path("signal_log.csv")

PAIRS = [
    "EURUSD=X", "GBPUSD=X", "USDJPY=X", "USDCHF=X", "USDCAD=X",
    "AUDUSD=X", "NZDUSD=X", "EURGBP=X", "EURJPY=X", "EURCHF=X",
    "EURCAD=X", "EURAUD=X", "EURNZD=X", "GBPJPY=X", "GBPCHF=X",
    "GBPCAD=X", "GBPAUD=X", "GBPNZD=X", "AUDJPY=X", "AUDCHF=X",
    "AUDCAD=X", "AUDNZD=X", "NZDJPY=X", "NZDCHF=X", "NZDCAD=X",
    "CADJPY=X", "CHFJPY=X",
]

MIN_RR = 1.5
MAX_M5_AGE_MINUTES = 12
MIN_HISTORY = 80
MIN_ADX = 18
MIN_RSI_BUY, MAX_RSI_BUY = 52, 68
MIN_RSI_SELL, MAX_RSI_SELL = 32, 48
MAX_CANDLE_ATR = 1.8
MIN_ATR_PCT, MAX_ATR_PCT = 0.015, 0.60
STOP_ATR_MULTIPLIER = 1.2
MAX_ENTRY_EXTENSION_ATR = 0.65
MIN_STOP_PIPS, MAX_STOP_PIPS = 3.0, 30.0
RISK_PERCENT = 0.25  # Advisory only; this alert-only bot cannot enforce account risk.
MAX_PENDING_SIGNALS = 100
OUTCOME_LOOKAHEAD_CANDLES = 48  # 4 hours of M5 candles, after the signal candle.
STATE_RETENTION = 500

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "ForexAlertBot/3.0"})
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{}"


def utc_now():
    return datetime.now(timezone.utc)


def telegram(message):
    if not BOT_TOKEN or not CHAT_ID:
        print("Telegram secrets missing; message not sent.")
        return False
    try:
        response = SESSION.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": message, "disable_web_page_preview": True},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            print("Telegram rejected message.")
            return False
        return True
    except Exception as exc:
        print(f"Telegram error: {exc}")
        return False


def load_state():
    if not STATE_FILE.exists():
        return {"sent_ids": [], "pending": [], "outcomes": []}
    try:
        with STATE_FILE.open("r", encoding="utf-8") as f:
            state = json.load(f)
        if not isinstance(state, dict):
            raise ValueError("State is not an object")
        state.setdefault("sent_ids", [])
        state.setdefault("pending", [])
        state.setdefault("outcomes", [])
        return state
    except Exception as exc:
        # Do not silently overwrite corrupt history.
        raise RuntimeError(f"Could not read {STATE_FILE}: {exc}") from exc


def save_state(state):
    state["sent_ids"] = state.get("sent_ids", [])[-STATE_RETENTION:]
    state["pending"] = state.get("pending", [])[-MAX_PENDING_SIGNALS:]
    state["outcomes"] = state.get("outcomes", [])[-STATE_RETENTION:]
    tmp = STATE_FILE.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    tmp.replace(STATE_FILE)


def append_log(row):
    fields = [
        "signal_id", "created_utc", "pair", "direction", "entry",
        "sl", "tp", "rr", "outcome", "outcome_utc", "note",
    ]
    exists = LOG_FILE.exists()
    with LOG_FILE.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if not exists:
            writer.writeheader()
        writer.writerow({key: row.get(key, "") for key in fields})


def fetch_candles(symbol, interval, range_value):
    response = SESSION.get(
        YAHOO_URL.format(symbol),
        params={"interval": interval, "range": range_value, "includePrePost": "false"},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    chart = payload.get("chart", {})
    if chart.get("error"):
        raise ValueError(f"Yahoo error: {chart['error']}")
    results = chart.get("result")
    if not results:
        raise ValueError("No Yahoo chart result")
    result = results[0]
    timestamps = result.get("timestamp") or []
    quote_list = result.get("indicators", {}).get("quote", [])
    if not quote_list:
        raise ValueError("No OHLC data")
    quote = quote_list[0]
    interval_seconds = {"5m": 300, "15m": 900, "60m": 3600}.get(interval)
    if interval_seconds is None:
        raise ValueError(f"Unsupported interval: {interval}")

    now = int(time.time())
    candles = []
    for i, stamp in enumerate(timestamps):
        try:
            o, h, low, c = (
                quote["open"][i], quote["high"][i],
                quote["low"][i], quote["close"][i]
            )
            if None in (o, h, low, c):
                continue
            o, h, low, c = map(float, (o, h, low, c))
            if not all(math.isfinite(v) for v in (o, h, low, c)):
                continue
            if low <= 0 or h < low or h < max(o, c) or low > min(o, c):
                continue
            # Yahoo timestamps are candle opening times. Keep completed candles only.
            if int(stamp) + interval_seconds > now:
                continue
            candles.append({
                "time": int(stamp), "open": o, "high": h, "low": low, "close": c
            })
        except (IndexError, TypeError, ValueError, KeyError):
            continue

    candles.sort(key=lambda x: x["time"])
    # Deduplicate timestamps.
    unique = {}
    for candle in candles:
        unique[candle["time"]] = candle
    candles = [unique[t] for t in sorted(unique)]

    if len(candles) < MIN_HISTORY:
        raise ValueError(f"Insufficient completed {interval} candles: {len(candles)}")

    # Reject gaps in the recent M5 sequence rather than interpreting missing bars as continuous data.
    if interval == "5m" and len(candles) >= 4:
        recent = candles[-4:]
        for prev, cur in zip(recent, recent[1:]):
            gap = cur["time"] - prev["time"]
            # Larger gaps can occur around market closure/weekends. Only reject unusual gaps
            # during the latest active run; the age check below is the primary freshness guard.
            if gap > 30 * 60 and (now - cur["time"]) < 2 * 60 * 60:
                raise ValueError("Unexpected recent M5 candle gap")

    return candles


def ema(values, period):
    if len(values) < period:
        return []
    alpha = 2.0 / (period + 1)
    out = [None] * (period - 1)
    current = sum(values[:period]) / period
    out.append(current)
    for value in values[period:]:
        current = alpha * value + (1.0 - alpha) * current
        out.append(current)
    return out


def rsi(values, period=14):
    if len(values) <= period:
        return None
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(x, 0.0) for x in changes]
    losses = [max(-x, 0.0) for x in changes]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


def atr_series(candles, period=14):
    if len(candles) <= period:
        return []
    trs = []
    for i in range(1, len(candles)):
        cur, prev = candles[i], candles[i - 1]
        trs.append(max(
            cur["high"] - cur["low"],
            abs(cur["high"] - prev["close"]),
            abs(cur["low"] - prev["close"]),
        ))
    out = [None] * (period - 1)
    current = sum(trs[:period]) / period
    out.append(current)
    for tr in trs[period:]:
        current = (current * (period - 1) + tr) / period
        out.append(current)
    # ATR list corresponds to candles from index 1 onward.
    return [None] + out


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
    dx = []
    for i in range(period - 1, len(trs)):
        start = i - period + 1
        tr_sum = sum(trs[start:i + 1])
        if tr_sum <= 0:
            continue
        plus = 100.0 * sum(plus_dm[start:i + 1]) / tr_sum
        minus = 100.0 * sum(minus_dm[start:i + 1]) / tr_sum
        denom = plus + minus
        dx.append(0.0 if denom == 0 else 100.0 * abs(plus - minus) / denom)
    if len(dx) < period:
        return None
    value = sum(dx[:period]) / period
    for value_dx in dx[period:]:
        value = (value * (period - 1) + value_dx) / period
    return value


def trend_direction(candles):
    closes = [c["close"] for c in candles]
    fast, slow = ema(closes, 20), ema(closes, 50)
    if not fast or not slow or fast[-1] is None or slow[-1] is None:
        return None
    if fast[-1] > slow[-1] and closes[-1] > fast[-1]:
        return "BUY"
    if fast[-1] < slow[-1] and closes[-1] < fast[-1]:
        return "SELL"
    return None


def pip_size(symbol):
    return 0.01 if symbol.replace("=X", "").endswith("JPY") else 0.0001


def analyse_pair(symbol):
    m5 = fetch_candles(symbol, "5m", "5d")
    m15 = fetch_candles(symbol, "15m", "10d")
    h1 = fetch_candles(symbol, "60m", "30d")

    now = int(time.time())
    last = m5[-1]
    # Age is measured from candle CLOSE time, not opening time.
    age_minutes = max(0.0, (now - (last["time"] + 300)) / 60.0)
    if age_minutes > MAX_M5_AGE_MINUTES:
        raise ValueError(f"Stale completed M5 candle ({age_minutes:.1f} min since close)")

    closes = [c["close"] for c in m5]
    fast, slow = ema(closes, 9), ema(closes, 21)
    if not fast or not slow or fast[-2] is None or slow[-2] is None:
        raise ValueError("Insufficient EMA history")

    crossed_up = fast[-2] <= slow[-2] and fast[-1] > slow[-1]
    crossed_down = fast[-2] >= slow[-2] and fast[-1] < slow[-1]
    if not crossed_up and not crossed_down:
        return None, "no fresh M5 crossover"

    direction = "BUY" if crossed_up else "SELL"
    if trend_direction(m15) != direction:
        return None, "M15 trend mismatch"
    if trend_direction(h1) != direction:
        return None, "H1 trend mismatch"

    momentum = rsi(closes)
    strength = adx(m5)
    atr_values = atr_series(m5)
    volatility = atr_values[-1] if atr_values else None
    if momentum is None or strength is None or volatility is None:
        return None, "indicator unavailable"
    if strength < MIN_ADX:
        return None, "ADX below threshold"
    if direction == "BUY" and not MIN_RSI_BUY <= momentum <= MAX_RSI_BUY:
        return None, "buy RSI outside range"
    if direction == "SELL" and not MIN_RSI_SELL <= momentum <= MAX_RSI_SELL:
        return None, "sell RSI outside range"

    entry = last["close"]
    if entry <= 0 or volatility <= 0:
        return None, "invalid price/ATR"

    atr_percent = volatility / entry * 100.0
    if not MIN_ATR_PCT <= atr_percent <= MAX_ATR_PCT:
        return None, "ATR percentage outside range"
    if last["high"] - last["low"] > MAX_CANDLE_ATR * volatility:
        return None, "oversized signal candle"

    # Avoid chasing price too far from the fast EMA after a crossover.
    extension_atr = abs(entry - fast[-1]) / volatility
    if extension_atr > MAX_ENTRY_EXTENSION_ATR:
        return None, "entry too extended from EMA"

    risk_distance = STOP_ATR_MULTIPLIER * volatility
    stop_pips = risk_distance / pip_size(symbol)
    if not MIN_STOP_PIPS <= stop_pips <= MAX_STOP_PIPS:
        return None, f"stop distance out of bounds ({stop_pips:.1f} pips)"

    target_distance = risk_distance * MIN_RR
    if direction == "BUY":
        stop, target = entry - risk_distance, entry + target_distance
    else:
        stop, target = entry + risk_distance, entry - target_distance

    pair = symbol.replace("=X", "")
    decimals = 3 if pair.endswith("JPY") else 5
    signal_id = f"{symbol}:{direction}:{last['time']}"
    signal = {
        "signal_id": signal_id,
        "pair": pair[:3] + "/" + pair[3:],
        "symbol": symbol,
        "direction": direction,
        "entry": round(entry, decimals),
        "sl": round(stop, decimals),
        "tp": round(target, decimals),
        "rr": MIN_RR,
        "rsi": round(momentum, 1),
        "adx": round(strength, 1),
        "atr_pips": round(volatility / pip_size(symbol), 1),
        "extension_atr": round(extension_atr, 2),
        "candle_time": datetime.fromtimestamp(last["time"], timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "candle_epoch": last["time"],
        "created_utc": utc_now().isoformat(),
    }
    return signal, "qualified"


def signal_message(s):
    return (
        "FOREX SETUP — M5\n"
        f"Pair: {s['pair']}\n"
        f"Direction: {s['direction']}\n"
        f"Entry reference: {s['entry']}\n"
        f"Stop-loss: {s['sl']}\n"
        f"Take-profit: {s['tp']}\n"
        f"Planned risk/reward: 1:{s['rr']:.2f}\n"
        f"RSI: {s['rsi']} | ADX: {s['adx']}\n"
        f"ATR: {s['atr_pips']} pips | EMA extension: {s['extension_atr']} ATR\n"
        f"Closed M5 candle: {s['candle_time']}\n"
        "Data: Yahoo Finance, not broker-verified.\n"
        "Entry is a reference, not a guaranteed fill. Verify broker price/spread.\n"
        "Risk guidance only: suggested ceiling 0.25%; not account-enforced."
    )


def check_pending_outcomes(state):
    """Best-effort outcome tracking from Yahoo candles; ambiguous same-bar hits remain unresolved."""
    remaining = []
    outcomes = state.get("outcomes", [])
    checked = 0

    for pending in state.get("pending", []):
        try:
            candles = fetch_candles(pending["symbol"], "5m", "5d")
            after = [c for c in candles if c["time"] > int(pending["candle_epoch"])]
            if not after:
                remaining.append(pending)
                continue

            resolved = None
            resolve_time = None
            note = ""
            relevant = after[:OUTCOME_LOOKAHEAD_CANDLES]
            for candle in relevant:
                if pending["direction"] == "BUY":
                    hit_sl = candle["low"] <= float(pending["sl"])
                    hit_tp = candle["high"] >= float(pending["tp"])
                else:
                    hit_sl = candle["high"] >= float(pending["sl"])
                    hit_tp = candle["low"] <= float(pending["tp"])

                if hit_sl and hit_tp:
                    resolved = "AMBIGUOUS"
                    note = "SL and TP touched in same M5 candle; order unknown"
                elif hit_sl:
                    resolved = "LOSS"
                    note = "SL touched first in available candle sequence"
                elif hit_tp:
                    resolved = "WIN"
                    note = "TP touched first in available candle sequence"
                if resolved:
                    resolve_time = datetime.fromtimestamp(candle["time"], timezone.utc).isoformat()
                    break

            if resolved:
                record = {
                    **pending,
                    "outcome": resolved,
                    "outcome_utc": resolve_time,
                    "note": note + "; Yahoo data, not broker-confirmed",
                }
                outcomes.append(record)
                append_log(record)
                checked += 1
            elif len(after) >= OUTCOME_LOOKAHEAD_CANDLES:
                record = {
                    **pending,
                    "outcome": "EXPIRED",
                    "outcome_utc": datetime.fromtimestamp(after[OUTCOME_LOOKAHEAD_CANDLES - 1]["time"], timezone.utc).isoformat(),
                    "note": "Neither target nor stop recorded within lookahead",
                }
                outcomes.append(record)
                append_log(record)
                checked += 1
            else:
                remaining.append(pending)
        except Exception as exc:
            print(f"Outcome check error for {pending.get('signal_id')}: {exc}")
            remaining.append(pending)

    state["pending"] = remaining
    state["outcomes"] = outcomes[-STATE_RETENTION:]
    return checked


def main():
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID GitHub secrets.")

    state = load_state()
    outcome_count = check_pending_outcomes(state)
    sent_ids = set(state.get("sent_ids", []))
    stats = {"checked": 0, "data_ok": 0, "sent": 0, "duplicate": 0, "errors": 0, "reasons": {}}

    print("Forex alert bot v3 scan started.")
    print("Source: Yahoo Finance; not broker-verified.")
    print("Time:", utc_now().isoformat())

    for symbol in PAIRS:
        stats["checked"] += 1
        try:
            signal, reason = analyse_pair(symbol)
            stats["data_ok"] += 1
            if signal is None:
                stats["reasons"][reason] = stats["reasons"].get(reason, 0) + 1
                continue
            if signal["signal_id"] in sent_ids:
                stats["duplicate"] += 1
                continue

            # Only mark a signal as sent after Telegram confirms delivery.
            if telegram(signal_message(signal)):
                stats["sent"] += 1
                sent_ids.add(signal["signal_id"])
                state.setdefault("sent_ids", []).append(signal["signal_id"])
                pending = {
                    **signal,
                    "outcome": "PENDING",
                    "outcome_utc": "",
                    "note": "",
                }
                state.setdefault("pending", []).append(pending)
                append_log(pending)
                save_state(state)
                print(f"{signal['pair']} {signal['direction']}: alert sent")
            else:
                print(f"{signal['pair']}: Telegram send failed; not marked as sent")
        except Exception as exc:
            stats["errors"] += 1
            print(f"{symbol}: rejected/error — {exc}")
        time.sleep(0.15)

    save_state(state)
    reasons = "\n".join(f"{k}: {v}" for k, v in sorted(stats["reasons"].items())) or "None"
    summary = (
        "Forex scan complete.\n"
        f"Pairs checked: {stats['checked']}\n"
        f"Pairs with usable data: {stats['data_ok']}\n"
        f"Qualifying alerts sent: {stats['sent']}\n"
        f"Duplicates skipped: {stats['duplicate']}\n"
        f"Data/analysis errors: {stats['errors']}\n"
        f"Outcomes newly resolved: {outcome_count}\n"
        f"Rejected setups:\n{reasons}\n\n"
        "Outcome records are estimates from Yahoo candles, not broker-verified fills.\n"
        "No signal is not a reason to force a trade."
    )
    print(summary)
    telegram(summary)


if __name__ == "__main__":
    main()
