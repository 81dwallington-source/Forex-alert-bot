
"""
Forex Telegram signal bot with outcome verification.
Signals only: it never places trades.
Data: Yahoo Finance intraday candles, which may be delayed/incomplete and are NOT broker Bid/Ask.
Simulated entry: close of the signal's completed M5 candle.
Outcome: first subsequent M5 candle that touches SL or TP. If both are touched in one candle,
the result remains AMBIGUOUS/UNVERIFIED rather than guessing.
"""
import os
import csv
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

import yfinance as yf

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

PAIRS = [
    "EURUSD=X", "GBPUSD=X", "USDJPY=X", "USDCHF=X", "USDCAD=X",
    "AUDUSD=X", "NZDUSD=X", "EURGBP=X", "EURJPY=X", "EURCHF=X",
    "EURCAD=X", "EURAUD=X", "EURNZD=X", "GBPJPY=X", "GBPCHF=X",
    "GBPCAD=X", "GBPAUD=X", "GBPNZD=X", "AUDJPY=X", "AUDCHF=X",
    "AUDCAD=X", "AUDNZD=X", "NZDJPY=X", "NZDCHF=X", "NZDCAD=X",
    "CADJPY=X", "CADCHF=X", "CHFJPY=X"
]

STATE_FILE = "signals_state.json"
CSV_FILE = "signal_log.csv"
MIN_BARS = 80
MAX_M5_CANDLE_AGE_SECONDS = 20 * 60
MAX_HTF_CANDLE_AGE_MULTIPLE = 2.5
ATR_PERIOD = 14
ATR_STOP_MULTIPLIER = 1.5
MIN_STOP_PIPS = 5.0
MAX_STOP_PIPS = 35.0
MIN_RR = 1.5
MAX_ENTRY_DRIFT_ATR = 0.6
MAX_TRACKING_DAYS = 7

def now_utc():
    return datetime.now(timezone.utc)

def parse_time(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except Exception:
        return None

def pip_size(pair):
    return 0.01 if "JPY" in pair else 0.0001

def digits_for(pair):
    return 3 if "JPY" in pair else 5

def ema(values, period):
    out = [None] * len(values)
    if len(values) < period:
        return out
    alpha = 2.0 / (period + 1.0)
    out[period - 1] = sum(values[:period]) / period
    for i in range(period, len(values)):
        out[i] = alpha * values[i] + (1.0 - alpha) * out[i - 1]
    return out

def calc_atr(highs, lows, closes, period=14):
    if len(closes) <= period:
        return None
    trs = []
    for i in range(1, len(closes)):
        trs.append(max(highs[i] - lows[i],
                       abs(highs[i] - closes[i - 1]),
                       abs(lows[i] - closes[i - 1])))
    if len(trs) < period:
        return None
    value = sum(trs[:period]) / period
    for tr in trs[period:]:
        value = (value * (period - 1) + tr) / period
    return value

def fetch_bars(pair, interval, period):
    try:
        df = yf.Ticker(pair).history(period=period, interval=interval, auto_adjust=False)
        if df is None or df.empty:
            return None
        df = df.dropna(subset=["Open", "High", "Low", "Close"])
        if df.empty:
            return None
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")
        return df.sort_index()
    except Exception as exc:
        print(f"{pair} {interval} data error: {type(exc).__name__}: {exc}")
        return None

def trend_direction(pair, interval, period, candle_seconds):
    df = fetch_bars(pair, interval, period)
    if df is None or len(df) < MIN_BARS:
        return None
    # Drop the final candle because it may still be forming.
    df = df.iloc[:-1]
    if len(df) < MIN_BARS:
        return None
    last_time = df.index[-1].to_pydatetime()
    age = (now_utc() - last_time).total_seconds()
    if age < 0 or age > candle_seconds * MAX_HTF_CANDLE_AGE_MULTIPLE:
        return None
    closes = df["Close"].astype(float).tolist()
    fast, slow = ema(closes, 20), ema(closes, 50)
    if fast[-1] is None or slow[-1] is None:
        return None
    if closes[-1] > fast[-1] > slow[-1]:
        return "BUY"
    if closes[-1] < fast[-1] < slow[-1]:
        return "SELL"
    return None

def get_signal(pair):
    df = fetch_bars(pair, "5m", "5d")
    if df is None or len(df) < MIN_BARS + 1:
        return None, "missing/insufficient M5 candles"
    closed = df.iloc[:-1]  # never use the forming candle
    if len(closed) < MIN_BARS:
        return None, "insufficient closed M5 candles"
    current = now_utc()
    candle_time = closed.index[-1].to_pydatetime()
    age = (current - candle_time).total_seconds()
    if age < 0 or age > MAX_M5_CANDLE_AGE_SECONDS:
        return None, f"latest closed M5 candle is stale ({int(age)}s)"
    closes = closed["Close"].astype(float).tolist()
    opens = closed["Open"].astype(float).tolist()
    highs = closed["High"].astype(float).tolist()
    lows = closed["Low"].astype(float).tolist()
    fast, slow = ema(closes, 9), ema(closes, 21)
    # Crossover on the last two closed candles, not the forming candle.
    if fast[-3] is None or slow[-3] is None:
        return None, "EMA history unavailable"
    direction = None
    if fast[-2] <= slow[-2] and fast[-1] > slow[-1]:
        direction = "BUY"
    elif fast[-2] >= slow[-2] and fast[-1] < slow[-1]:
        direction = "SELL"
    if not direction:
        return None, "no fresh M5 EMA 9/21 crossover"
    if direction == "BUY" and closes[-1] <= opens[-1]:
        return None, "BUY candle not bullish"
    if direction == "SELL" and closes[-1] >= opens[-1]:
        return None, "SELL candle not bearish"
    if trend_direction(pair, "15m", "10d", 900) != direction:
        return None, "15m EMA 20/50 trend not aligned or stale"
    if trend_direction(pair, "60m", "60d", 3600) != direction:
        return None, "1h EMA 20/50 trend not aligned or stale"
    volatility = calc_atr(highs, lows, closes, ATR_PERIOD)
    if volatility is None or volatility <= 0:
        return None, "ATR unavailable"
    entry = closes[-1]  # fixed simulated entry: signal candle close
    distance = volatility * ATR_STOP_MULTIPLIER
    stop_pips = distance / pip_size(pair)
    if stop_pips < MIN_STOP_PIPS or stop_pips > MAX_STOP_PIPS:
        return None, f"ATR stop outside filter ({stop_pips:.1f} pips)"
    # Reject if the still-forming candle has already moved too far from entry.
    forming_close = float(df["Close"].iloc[-1])
    if abs(forming_close - entry) > volatility * MAX_ENTRY_DRIFT_ATR:
        return None, "price drifted too far from simulated entry"
    sl, tp = ((entry - distance, entry + distance * MIN_RR) if direction == "BUY"
              else (entry + distance, entry - distance * MIN_RR))
    digits = digits_for(pair)
    stamp = candle_time.astimezone(timezone.utc).isoformat(timespec="seconds")
    key = f"{pair}|{direction}|{int(candle_time.timestamp())}"
    return {
        "id": key,
        "symbol": pair.replace("=X", ""),
        "direction": direction,
        "entry": round(entry, digits),
        "sl": round(sl, digits),
        "tp": round(tp, digits),
        "rr": MIN_RR,
        "stop_pips": round(stop_pips, 1),
        "signal_candle_utc": stamp,
        "created_utc": current.isoformat(timespec="seconds"),
        "outcome": "OPEN",
        "outcome_utc": "",
        "outcome_candle_utc": "",
        "outcome_detail": "",
        "data_source": "Yahoo Finance; simulated close entry; not broker Bid/Ask"
    }, None

def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("signals"), list):
            return data
    except (OSError, ValueError):
        pass
    return {"signals": []}

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

def send_telegram(message):
    if not BOT_TOKEN or not CHAT_ID:
        print("ERROR: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID secret missing.")
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    body = urllib.parse.urlencode({"chat_id": CHAT_ID, "text": message}).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
            if not result.get("ok"):
                print("Telegram API did not confirm message delivery.")
            return bool(result.get("ok"))
    except Exception as exc:
        print(f"Telegram send failed: {type(exc).__name__}: {exc}")
        return False

def alert_text(s):
    return (
        "FOREX SETUP â M5 (SIMULATED SIGNAL ONLY)\n"
        f"Pair: {s['symbol']}\nDirection: {s['direction']}\n"
        f"Simulated entry: {s['entry']}\nStop-loss: {s['sl']}\nTake-profit: {s['tp']}\n"
        f"Planned risk/reward: 1:{s['rr']:.2f}\n"
        "Trigger: closed M5 EMA 9/21 crossover + candle confirmation\n"
        "Confirmation: 15m and 1h EMA 20/50 aligned\n"
        f"Closed signal candle (UTC): {s['signal_candle_utc']}\n"
        "Outcome tracking: automatic candle-based verification enabled\n"
        "Data warning: Yahoo Finance may be delayed; not broker Bid/Ask. Signals only."
    )

def evaluate_open_signals(state):
    """Check candles strictly after the signal candle. Never guess when both levels hit."""
    changed = False
    now = now_utc()
    for s in state["signals"]:
        if s.get("outcome") != "OPEN":
            continue
        signal_time = parse_time(s.get("signal_candle_utc"))
        if signal_time is None:
            s["outcome"] = "UNVERIFIED"
            s["outcome_detail"] = "Invalid/missing signal timestamp"
            changed = True
            continue
        if now - signal_time > timedelta(days=MAX_TRACKING_DAYS):
            s["outcome"] = "EXPIRED_UNVERIFIED"
            s["outcome_detail"] = f"No SL/TP result verified within {MAX_TRACKING_DAYS} days"
            changed = True
            continue
        pair = s["symbol"] + "=X"
        df = fetch_bars(pair, "5m", "5d")
        if df is None or df.empty:
            continue
        # Drop the currently forming candle so results use completed OHLC bars only.
        closed_df = df.iloc[:-1]
        # Only completed candles whose open timestamp is later than the signal candle are eligible.
        later = closed_df[closed_df.index > signal_time]
        if later.empty:
            continue
        # Evaluate oldest first. Yahoo bars are OHLC, so intrabar ordering is unknown.
        for ts, row in later.iterrows():
            high, low = float(row["High"]), float(row["Low"])
            sl, tp = float(s["sl"]), float(s["tp"])
            if s["direction"] == "BUY":
                hit_sl = low <= sl
                hit_tp = high >= tp
            else:
                hit_sl = high >= sl
                hit_tp = low <= tp
            if hit_sl and hit_tp:
                s["outcome"] = "UNVERIFIED"
                s["outcome_utc"] = now.isoformat(timespec="seconds")
                s["outcome_candle_utc"] = ts.to_pydatetime().astimezone(timezone.utc).isoformat(timespec="seconds")
                s["outcome_detail"] = "Both SL and TP touched in the same M5 candle; order cannot be verified from OHLC"
                changed = True
                break
            if hit_sl:
                s["outcome"] = "LOSS"
                s["outcome_utc"] = now.isoformat(timespec="seconds")
                s["outcome_candle_utc"] = ts.to_pydatetime().astimezone(timezone.utc).isoformat(timespec="seconds")
                s["outcome_detail"] = "SL touched first on a later M5 candle"
                changed = True
                break
            if hit_tp:
                s["outcome"] = "WIN"
                s["outcome_utc"] = now.isoformat(timespec="seconds")
                s["outcome_candle_utc"] = ts.to_pydatetime().astimezone(timezone.utc).isoformat(timespec="seconds")
                s["outcome_detail"] = "TP touched first on a later M5 candle"
                changed = True
                break
    return changed

def write_csv(state):
    fields = [
        "id", "created_utc", "symbol", "direction", "entry", "sl", "tp", "rr",
        "signal_candle_utc", "outcome", "outcome_utc", "outcome_candle_utc",
        "outcome_detail", "data_source"
    ]
    with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for s in state["signals"]:
            writer.writerow({k: s.get(k, "") for k in fields})

def report_text(state):
    signals = state["signals"]
    wins = sum(s.get("outcome") == "WIN" for s in signals)
    losses = sum(s.get("outcome") == "LOSS" for s in signals)
    ambiguous = sum(s.get("outcome") == "UNVERIFIED" for s in signals)
    expired = sum(s.get("outcome") == "EXPIRED_UNVERIFIED" for s in signals)
    open_count = sum(s.get("outcome") == "OPEN" for s in signals)
    resolved = wins + losses
    rate = (wins / resolved * 100.0) if resolved else None
    rate_text = f"{rate:.1f}%" if rate is not None else "N/A (no resolved signals)"
    return (
        "FOREX BOT VERIFIED RESULTS\n"
        f"Verified wins: {wins}\nVerified losses: {losses}\n"
        f"Win rate (wins / wins+losses): {rate_text}\n"
        f"Open signals awaiting SL/TP: {open_count}\n"
        f"Ambiguous/unverified: {ambiguous}\nExpired without verification: {expired}\n"
        f"Total logged signals: {len(signals)}\n"
        "Simulated results only; not actual broker fills or guaranteed performance."
    )

def main():
    if not BOT_TOKEN or not CHAT_ID:
        raise SystemExit("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID repository secrets.")
    state = load_state()
    seen = {s.get("id") for s in state["signals"]}

    # First update existing signals from subsequent completed candles.
    old_outcomes = {s.get("id"): s.get("outcome") for s in state["signals"]}
    evaluate_open_signals(state)
    for s in state["signals"]:
        old = old_outcomes.get(s.get("id"))
        new = s.get("outcome")
        if old == "OPEN" and new in ("WIN", "LOSS", "UNVERIFIED", "EXPIRED_UNVERIFIED"):
            result_message = (
                "FOREX SIGNAL OUTCOME CHECK\n"
                f"Pair: {s['symbol']} | Direction: {s['direction']}\n"
                f"Simulated entry: {s['entry']} | SL: {s['sl']} | TP: {s['tp']}\n"
                f"Outcome: {new}\n"
                f"Checked candle (UTC): {s.get('outcome_candle_utc') or 'not available'}\n"
                f"Reason: {s.get('outcome_detail') or 'No detail'}\n"
                "Candle-based simulation only; not broker execution."
            )
            send_telegram(result_message)

    # Scan once per workflow run; GitHub Actions schedule runs the next scan.
    sent_count = 0
    for pair in PAIRS:
        signal, reason = get_signal(pair)
        if signal is None:
            print(f"{pair}: no signal â {reason}")
            continue
        if signal["id"] in seen:
            print(f"{pair}: duplicate signal skipped")
            continue
        # Only count/store a signal if Telegram confirms the alert was sent.
        if send_telegram(alert_text(signal)):
            state["signals"].append(signal)
            seen.add(signal["id"])
            sent_count += 1
            print(f"Signal sent and logged: {signal['id']}")
        time.sleep(0.5)

    # Keep the persistent history intact; CSV is a readable mirror.
    save_state(state)
    write_csv(state)
    print(f"New alerts sent this run: {sent_count}")
    print(report_text(state))
    # Send result updates only when a signal changed from OPEN to a terminal state.
    # The report is printed to Actions logs every run; no spammy recurring Telegram report.
    terminal = [s for s in state["signals"] if s.get("outcome") in ("WIN", "LOSS", "UNVERIFIED", "EXPIRED_UNVERIFIED")]
    print(f"Signals with a recorded terminal status: {len(terminal)}")

if __name__ == "__main__":
    main()
