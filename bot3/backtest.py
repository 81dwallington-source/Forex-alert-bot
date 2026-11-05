"""Point-in-time CSV backtest for Bot 3. No network access or live alerts."""
from __future__ import annotations
import argparse
from pathlib import Path
import pandas as pd

from bot3 import bot3


def load_m5(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    required = {"time", "Open", "High", "Low", "Close"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{path}: missing columns {sorted(missing)}")
    df["time"] = pd.to_datetime(df["time"], utc=True, errors="raise")
    for col in ("Open", "High", "Low", "Close"):
        df[col] = pd.to_numeric(df[col], errors="raise")
    df = df.drop_duplicates("time", keep="last").set_index("time").sort_index()
    if (df[["Open", "High", "Low", "Close"]] <= 0).any().any():
        raise ValueError(f"{path}: prices must be positive")
    if (df.High < df[["Open", "Close", "Low"]].max(axis=1)).any():
        raise ValueError(f"{path}: invalid candle high")
    if (df.Low > df[["Open", "Close", "High"]].min(axis=1)).any():
        raise ValueError(f"{path}: invalid candle low")
    if len(df) < 800:
        raise ValueError(f"{path}: need at least 800 M5 bars for warm-up and meaningful context")
    return df


def resample_ohlc(m5: pd.DataFrame, rule: str) -> pd.DataFrame:
    out = m5.resample(rule, label="left", closed="left").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last"}
    )
    counts = m5.Close.resample(rule, label="left", closed="left").count()
    expected = 3 if rule == "15min" else 12
    return out.loc[counts == expected].dropna()


def backtest_pair(pair: str, m5: pd.DataFrame, spread_pips: float) -> list[dict]:
    """Spread is modelled as a fixed full bid/ask spread; fills occur next M5 open."""
    if spread_pips <= 0:
        raise ValueError("spread_pips must be positive")
    pip = 0.01 if "JPY" in pair else 0.0001
    half_spread = spread_pips * pip / 2
    m15_all, h1_all = resample_ohlc(m5, "15min"), resample_ohlc(m5, "1h")
    results = []
    i = 800
    while i < len(m5) - 2:
        candle_time = m5.index[i]
        decision_time = candle_time + pd.Timedelta(minutes=5)
        if not bot3.in_session(decision_time.to_pydatetime()):
            i += 1
            continue
        m15 = m15_all.loc[m15_all.index + pd.Timedelta(minutes=15) <= decision_time]
        h1 = h1_all.loc[h1_all.index + pd.Timedelta(hours=1) <= decision_time]
        if len(m15) < 60 or len(h1) < 60:
            i += 1
            continue
        history = m5.iloc[:i + 1]
        close = float(history.Close.iloc[-1])
        synthetic_bid, synthetic_ask = close - half_spread, close + half_spread
        signal = bot3.build_signal(
            pair, history, m15, h1, close, decision_time,
            synthetic_bid, synthetic_ask, decision_time,
        )
        if not signal:
            i += 1
            continue

        # Conservative executable fill at the next candle open, including half-spread.
        next_open = float(m5.Open.iloc[i + 1])
        fill = next_open + half_spread if signal["direction"] == "BUY" else next_open - half_spread
        if abs(fill - signal["entry"]) > bot3.MAX_ENTRY_DRIFT_ATR * signal["atr"]:
            i += 1
            continue
        stop = float(signal["stop"])
        risk = fill - stop if signal["direction"] == "BUY" else stop - fill
        if risk <= 0 or risk > bot3.MAX_STOP_ATR * signal["atr"]:
            i += 1
            continue
        target = fill + risk * bot3.MIN_RR if signal["direction"] == "BUY" else fill - risk * bot3.MIN_RR

        outcome, exit_time, pnl_r = "OPEN", "", None
        for j in range(i + 1, len(m5)):
            bar = m5.iloc[j]
            # Convert midpoint OHLC to executable exit-side prices.
            if signal["direction"] == "BUY":
                stop_hit = float(bar.Low) - half_spread <= stop
                target_hit = float(bar.High) - half_spread >= target
                gap_stop = float(bar.Open) - half_spread <= stop
                gap_target = float(bar.Open) - half_spread >= target
            else:
                stop_hit = float(bar.High) + half_spread >= stop
                target_hit = float(bar.Low) + half_spread <= target
                gap_stop = float(bar.Open) + half_spread >= stop
                gap_target = float(bar.Open) + half_spread <= target

            if stop_hit and target_hit:
                outcome, exit_time, pnl_r = "AMBIGUOUS", m5.index[j].isoformat(), None
                break
            if gap_stop or stop_hit:
                exit_px = (float(bar.Open) - half_spread if signal["direction"] == "BUY" else float(bar.Open) + half_spread) if gap_stop else stop
                pnl = (exit_px - fill) if signal["direction"] == "BUY" else (fill - exit_px)
                outcome, exit_time, pnl_r = "LOSS", m5.index[j].isoformat(), pnl / risk
                break
            if gap_target or target_hit:
                exit_px = (float(bar.Open) - half_spread if signal["direction"] == "BUY" else float(bar.Open) + half_spread) if gap_target else target
                pnl = (exit_px - fill) if signal["direction"] == "BUY" else (fill - exit_px)
                outcome, exit_time, pnl_r = "WIN", m5.index[j].isoformat(), pnl / risk
                break

        results.append({
            "pair": pair, "direction": signal["direction"],
            "signal_time_utc": decision_time.isoformat(),
            "fill_time_utc": m5.index[i + 1].isoformat(),
            "entry_fill": fill, "stop": stop, "target": target,
            "spread_pips_assumed": spread_pips, "outcome": outcome,
            "exit_time_utc": exit_time, "result_R": pnl_r,
        })
        # One trade at a time per pair to avoid overlapping trade accounting.
        if outcome in ("WIN", "LOSS", "AMBIGUOUS") and exit_time:
            exit_idx = m5.index.get_loc(pd.Timestamp(exit_time))
            i = max(i + 1, exit_idx + 1)
        else:
            break
    return results


def main():
    parser = argparse.ArgumentParser(description="Bot 3 historical M5 CSV backtest")
    parser.add_argument("--pair", required=True, help="e.g. EURUSD or USDJPY")
    parser.add_argument("--csv", required=True, type=Path, help="M5 CSV with time,Open,High,Low,Close")
    parser.add_argument("--spread-pips", required=True, type=float, help="assumed full bid/ask spread in pips")
    parser.add_argument("--out", type=Path, default=Path("bot3_backtest_results.csv"))
    args = parser.parse_args()
    bars = load_m5(args.csv)
    rows = backtest_pair(args.pair.upper(), bars, args.spread_pips)
    out = pd.DataFrame(rows)
    out.to_csv(args.out, index=False)
    closed = out[out.outcome.isin(["WIN", "LOSS"])] if not out.empty else out
    wins = int((closed.outcome == "WIN").sum()) if not closed.empty else 0
    losses = int((closed.outcome == "LOSS").sum()) if not closed.empty else 0
    ambiguous = int((out.outcome == "AMBIGUOUS").sum()) if not out.empty else 0
    total_r = float(closed.result_R.sum()) if not closed.empty else 0.0
    win_rate = 100 * wins / (wins + losses) if wins + losses else 0.0
    print(f"Pair: {args.pair.upper()} | M5 bars: {len(bars)}")
    print(f"Signals: {len(out)} | Wins: {wins} | Losses: {losses} | Ambiguous: {ambiguous}")
    print(f"Win rate (closed unambiguous): {win_rate:.1f}% | Net R: {total_r:.2f}")
    print(f"Results saved: {args.out}")
    print("OPEN trades are censored, not counted. Ambiguous same-bar stop/target hits are excluded.")


if __name__ == "__main__":
    main()
