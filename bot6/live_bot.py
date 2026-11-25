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
        "candles": {"M5": serialise(m5, 60), "M15": serialise(m15, 40), "H1": serialise(h1, 40)},
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


def get_signal(m5: pd.DataFrame, m15: pd.DataFrame, h1: pd.DataFrame, pair: str):
    """Impulse-origin retest: M15 structure/zone, M5 rejection entry, H1 trend filter."""
    mt, ht, m5i = indicators(m15), indicators(h1), indicators(m5)
    if len(mt) < 150 or len(ht) < 150 or len(m5i) < 210:
        return None

    c15, prev15, hc = mt.iloc[-1], mt.iloc[-2], ht.iloc[-1]
    c5, prev5 = m5i.iloc[-1], m5i.iloc[-2]
    if pd.isna(c15["atr"]) or float(c15["atr"]) <= 0 or pd.isna(c5["atr"]):
        return None

    h1_up = hc["close"] > hc["ema200"] and hc["ema21"] > hc["ema50"]
    h1_down = hc["close"] < hc["ema200"] and hc["ema21"] < hc["ema50"]
    if h1_up:
        direction = "BUY"
    elif h1_down:
        direction = "SELL"
    else:
        return None

    pip = pip_size(pair)
    atr15, atr5 = float(c15["atr"]), float(c5["atr"])
    n = len(mt)

    # Search only recent completed M15 structure breaks. The origin is the
    # final opposing candle immediately before the displacement candle.
    for bi in range(n - 12, n - 2):
        breakout = mt.iloc[bi]
        before = mt.iloc[bi - 5:bi]
        if len(before) != 5:
            continue

        brange = max(float(breakout["high"] - breakout["low"]), 1e-12)
        bbody = abs(float(breakout["close"] - breakout["open"]))
        batr = float(breakout["atr"]) if pd.notna(breakout["atr"]) else atr15
        if batr <= 0 or bbody < 0.55 * batr or brange < 0.80 * batr:
            continue

        if direction == "SELL":
            structure = float(before["low"].min())
            if not (float(breakout["close"]) < structure and
                    float(breakout["close"]) < float(breakout["open"])):
                continue
            opposing = [j for j in range(bi - 5, bi)
                        if float(mt.iloc[j]["close"]) > float(mt.iloc[j]["open"])]
            if not opposing:
                continue
            oi = opposing[-1]
            origin = mt.iloc[oi]
            # Bearish origin zone: open-to-high of the last bullish candle.
            zone_low, zone_high = float(origin["open"]), float(origin["high"])
            origin_extreme = float(origin["high"])
        else:
            structure = float(before["high"].max())
            if not (float(breakout["close"]) > structure and
                    float(breakout["close"]) > float(breakout["open"])):
                continue
            opposing = [j for j in range(bi - 5, bi)
                        if float(mt.iloc[j]["close"]) < float(mt.iloc[j]["open"])]
            if not opposing:
                continue
            oi = opposing[-1]
            origin = mt.iloc[oi]
            # Bullish origin zone: low-to-open of the last bearish candle.
            zone_low, zone_high = float(origin["low"]), float(origin["open"])
            origin_extreme = float(origin["low"])

        if zone_high <= zone_low:
            continue

        # A later completed M15 candle must revisit the origin zone and hold
        # on the correct side. A touch of the zone is never an entry by itself.
        touches = []
        for ri in range(bi + 1, n - 1):
            r = mt.iloc[ri]
            touched = float(r["low"]) <= zone_high and float(r["high"]) >= zone_low
            if not touched:
                continue
            held = (float(r["close"]) <= zone_high if direction == "SELL"
                    else float(r["close"]) >= zone_low)
            if held:
                touches.append(ri)
        if not touches:
            continue
        pullback_i = touches[-1]
        pullback_time = mt.index[pullback_i].isoformat()

        # M15 must still support the H1 direction after the retest.
        m15_confirmed = (
            float(c15["close"]) < float(prev15["low"]) and
            float(c15["close"]) < float(c15["ema21"])
            if direction == "SELL" else
            float(c15["close"]) > float(prev15["high"]) and
            float(c15["close"]) > float(c15["ema21"])
        )
        if not m15_confirmed:
            continue

        # M5 must itself have revisited the same origin zone after the M15
        # retest, then print a separate rejection/continuation candle.
        pullback_stamp = mt.index[pullback_i] + pd.Timedelta(minutes=15)
        recent5 = m5i.loc[m5i.index >= pullback_stamp].tail(36)
        if recent5.empty:
            continue
        m5_touched = ((recent5["low"] <= zone_high) &
                      (recent5["high"] >= zone_low)).any()
        if not m5_touched:
            continue

        if direction == "SELL":
            confirmed = (
                float(c5["close"]) < float(c5["open"]) and
                float(c5["close"]) < float(prev5["low"]) and
                float(c5["close"]) < zone_low and
                float(c5["close"]) < float(c5["ema21"])
            )
        else:
            confirmed = (
                float(c5["close"]) > float(c5["open"]) and
                float(c5["close"]) > float(prev5["high"]) and
                float(c5["close"]) > zone_high and
                float(c5["close"]) > float(c5["ema21"])
            )
        if not confirmed:
            continue

        entry = float(c5["close"])
        buffer = max(0.15 * atr5, 2.0 * pip)
        try:
            swing_stop, swing = confirmed_swing_stop(m5i, direction, entry, buffer)
        except ValueError:
            continue
        # Keep the stop beyond both the latest confirmed M5 swing and the
        # original candle boundary; never compress it to an arbitrary tiny stop.
        stop = (max(swing_stop, zone_high + buffer) if direction == "SELL"
                else min(swing_stop, zone_low - buffer))
        risk = stop - entry if direction == "SELL" else entry - stop
        if risk <= 0 or risk < 0.5 * atr5 or risk > 4.0 * atr15:
            continue

        target = entry - 1.5 * risk if direction == "SELL" else entry + 1.5 * risk
        signal_close = m5i.index[-1] + pd.Timedelta(minutes=5)
        return {
            "direction": direction, "entry": entry, "stop": stop, "target": target,
            "risk_pips": risk / pip, "rr": 1.5, "atr": atr5,
            "breakout_level": structure, "breakout_time": mt.index[bi].isoformat(),
            "confirmed_swing": swing, "signal_time": signal_close.isoformat(),
            "pullback_time": pullback_time, "entry_zone_low": zone_low,
            "entry_zone_high": zone_high, "origin_time": mt.index[oi].isoformat(),
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
    print(f"BOT 6 starting institutional-style liquidity-sweep scan: {len(PAIRS)} pairs", flush=True)
    for pair in PAIRS:
        if MAX_ONE_PER_PAIR_PER_UTC_DAY and pair in used_pairs:
            continue
        if len(todays) + sent >= MAX_ALERTS_PER_UTC_DAY:
            break
        try:
            m5 = fetch_bars(pair, "5m", 210)
            m15 = fetch_bars(pair, "15m", 150)
            h1 = fetch_bars(pair, "1h", 150)
            check_fresh(m5, 5, MAX_M5_CANDLE_AGE_MINUTES)
            check_fresh(m15, 15, 25)
            check_fresh(h1, 60, 75)
            analysed += 1
            signal = get_signal(m5, m15, h1, pair)
            if not signal:
                no_setup += 1
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
                "data_source": "BiQuote OHLC plus live tick cross-check; not broker-verified",
                "live_quote_mid": round(quote["mid"], 6),
                "live_quote_time_utc": quote["timestamp"],
                "confirmed_swing": round(signal["confirmed_swing"], 6),
                "pullback_time_utc": signal["pullback_time"],
                "entry_zone_low": round(signal["entry_zone_low"], 6),
                "entry_zone_high": round(signal["entry_zone_high"], 6),
            }
            save_evidence(trade, m5, m15, h1, quote, signal)
            candle_close = pd.Timestamp(trade["signal_time_utc"])
            candle_age = max(0.0, (now_utc() - candle_close).total_seconds() / 60)
            message = (
                f"📣 BOT 6 — IMPULSE ORIGIN + RETEST {trade['direction']} {pair}\n"
                f"Entry: {trade['entry']}\nStop loss: {trade['stop_loss']}\n"
                f"Take profit: {trade['take_profit']}\n"
                f"Risk: {trade['risk_pips']} pips | Planned R:R 1:{trade['planned_rr']}\n"
                f"M15 structure break + origin-candle retest + M5 rejection confirmation\n"
                f"H1 trend aligned | Entry zone: {signal['entry_zone_low']:.6f}–{signal['entry_zone_high']:.6f} | Confirmed swing: {trade['confirmed_swing']}\n"
                f"M15 confirmation candle close (UTC): {candle_close.isoformat()} | Data age: {candle_age:.1f} min\n"
                f"Live tick check: {quote['mid']} ({quote['age_seconds']:.0f}s old)\n"
                f"Source: BiQuote OHLC + live tick check; not broker-verified. Check broker price/spread before entry. "
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
