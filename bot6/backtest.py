"""Walk-forward, spread-aware historical test for Bot 6. No live alerts or orders."""
from __future__ import annotations
import argparse
import csv
from pathlib import Path
import pandas as pd
from live_bot import indicators, get_signal, pip_size


def load_bars(path: Path, minutes: int) -> pd.DataFrame:
    d = pd.read_csv(path)
    d.columns = [str(c).lower() for c in d.columns]
    t = next((c for c in ("timestamp", "time", "datetime", "date") if c in d), None)
    if t is None:
        raise ValueError(f"{path}: timestamp column missing")
    idx = (pd.to_datetime(d[t], unit="ms", utc=True)
           if pd.api.types.is_numeric_dtype(d[t]) else pd.to_datetime(d[t], utc=True))
    d.index = idx
    for c in ("open", "high", "low", "close"):
        if c not in d:
            raise ValueError(f"{path}: {c} column missing")
        d[c] = pd.to_numeric(d[c], errors="raise")
    d = d[["open", "high", "low", "close"]].sort_index()
    d = d[~d.index.duplicated(keep="last")]
    if len(d) < 500:
        raise ValueError(f"{path}: only {len(d)} rows")
    if (d.high < d[["open", "close", "low"]].max(axis=1)).any() or (d.low > d[["open", "close", "high"]].min(axis=1)).any():
        raise ValueError(f"{path}: invalid OHLC values")
    # Reject missing intervals rather than silently joining across data gaps.
    gaps = d.index.to_series().diff().dropna()
    if (gaps > pd.Timedelta(days=7)).any():
        raise ValueError(f"{path}: data gap greater than seven days")
    return d


def stats(rows):
    if not rows:
        return {"trades": 0, "wins": 0, "losses": 0, "win_rate_pct": 0.0,
                "expectancy_R": 0.0, "profit_factor": 0.0, "max_drawdown_R": 0.0}
    wins = sum(x["result"] == "WIN" for x in rows)
    gains = sum(x["r"] for x in rows if x["r"] > 0)
    losses = -sum(x["r"] for x in rows if x["r"] < 0)
    equity = peak = drawdown = 0.0
    for x in rows:
        equity += x["r"]
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return {"trades": len(rows), "wins": wins, "losses": len(rows) - wins,
            "win_rate_pct": round(100 * wins / len(rows), 2),
            "expectancy_R": round(sum(x["r"] for x in rows) / len(rows), 3),
            "profit_factor": round(gains / losses, 3) if losses else "inf",
            "max_drawdown_R": round(drawdown, 2)}


def evaluate(pair: str, folder: Path, spread_pips: float):
    stem = pair.upper().replace("/", "")
    m5 = load_bars(folder / f"{stem}_M5.csv", 5)
    m15 = load_bars(folder / f"{stem}_M15.csv", 15)
    h1 = load_bars(folder / f"{stem}_H1.csv", 60)
    # Precompute causal indicators once. At index t, EMA/ATR only use data <= t.
    m5i, m15i, h1i = indicators(m5), indicators(m15), indicators(h1)
    pip = pip_size(stem)
    spread = spread_pips * pip
    trades = []
    next_allowed = 210
    for i in range(210, len(m5) - 1):
        if i < next_allowed:
            continue
        close_time = m5.index[i] + pd.Timedelta(minutes=5)
        x15 = m15i.loc[m15i.index + pd.Timedelta(minutes=15) <= close_time]
        x1 = h1i.loc[h1i.index + pd.Timedelta(hours=1) <= close_time]
        if len(x15) < 150 or len(x1) < 150:
            continue
        sig = get_signal(m5i.iloc[:i + 1], x15, x1, stem)
        if not sig:
            continue
        # Signal is formed at candle close; execution is next candle open.
        nxt = m5.iloc[i + 1]
        entry = float(nxt.open) + (spread if sig["direction"] == "BUY" else 0.0)
        sl, tp = float(sig["stop"]), float(sig["target"])
        risk = entry - sl if sig["direction"] == "BUY" else sl - entry
        if risk <= 0:
            continue
        outcome = None
        exit_time = ""
        r_multiple = 0.0
        for j in range(i + 1, len(m5)):
            bar = m5.iloc[j]
            if sig["direction"] == "BUY":
                hit_sl, hit_tp = float(bar.low) <= sl, float(bar.high) >= tp
            else:
                # Historical candles are bid prices; add the full spread for short exits.
                hit_sl = float(bar.high) + spread >= sl
                hit_tp = float(bar.low) + spread <= tp
            if hit_sl and hit_tp:
                outcome, r_multiple = "LOSS", -1.0  # conservative same-candle assumption
            elif hit_sl:
                outcome, r_multiple = "LOSS", -1.0
            elif hit_tp:
                reward = (tp - entry) if sig["direction"] == "BUY" else entry - tp  # short TP is already the ask-side exit level
                outcome, r_multiple = "WIN", reward / risk
            if outcome:
                exit_time = m5.index[j].isoformat()
                next_allowed = j + 1
                break
        if outcome:
            trades.append({"signal_time": close_time.isoformat(), "exit_time": exit_time,
                "pair": stem, "direction": sig["direction"], "entry": round(entry, 6),
                "stop": round(sl, 6), "target": round(tp, 6),
                "risk_pips": round(risk / pip, 1), "result": outcome,
                "r": round(r_multiple, 3)})
    return trades


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", default="historical_data")
    p.add_argument("--pairs", default="EUR/USD,GBP/USD,USD/JPY,USD/CHF,USD/CAD,AUD/USD,NZD/USD")
    p.add_argument("--spread-pips", type=float, default=1.2)
    p.add_argument("--minimum-oos-trades", type=int, default=30)
    a = p.parse_args()
    if a.spread_pips < 0:
        raise SystemExit("Spread must be non-negative")
    all_trades = []
    for pair in a.pairs.split(","):
        rows = evaluate(pair.strip(), Path(a.data_dir), a.spread_pips)
        print(f"{pair.strip().upper()}: {stats(rows)}", flush=True)
        all_trades.extend(rows)
    all_trades.sort(key=lambda x: x["signal_time"])
    fields = ["signal_time", "exit_time", "pair", "direction", "entry", "stop", "target",
              "risk_pips", "result", "r"]
    # Always publish the ledger, including an empty/header-only file when the
    # strategy produces too few trades. A failed sample-size gate must not hide results.
    with open("bot6_backtest_trades.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(all_trades)
    print("ALL RESOLVED TRADES:", stats(all_trades), flush=True)
    if len(all_trades) < 20:
        raise SystemExit(f"INCONCLUSIVE: only {len(all_trades)} resolved trades; no win rate claimed")
    split = int(len(all_trades) * 0.70)
    development, oos = all_trades[:split], all_trades[split:]
    print("DEVELOPMENT (first 70% chronologically):", stats(development), flush=True)
    print("UNTOUCHED OUT-OF-SAMPLE (last 30%):", stats(oos), flush=True)
    if len(oos) < a.minimum_oos_trades:
        raise SystemExit(f"INCONCLUSIVE: only {len(oos)} out-of-sample trades; need {a.minimum_oos_trades}")
    if stats(oos)["expectancy_R"] <= 0:
        raise SystemExit("FAIL: out-of-sample expectancy is not positive")
    print("PASS: minimum sample reached and out-of-sample expectancy is positive. This is not a guarantee of future results.")


if __name__ == "__main__":
    main()
