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
    if df["time"].duplicated().any():
        raise ValueError(f"{path}: duplicate timestamps found")
    df = df.set_index("time").sort_index()
    raw_count = len(df)
    flat = (df[["Open", "High", "Low", "Close"]].nunique(axis=1) == 1)
    flat_count = int(flat.sum())
    # The Dukascopy export contains long runs of zero-range bars despite the
    # downloader's include-flats=false option. Exclude them from the test.
    df = df.loc[~flat].copy()
    df.attrs["raw_rows"] = raw_count
    df.attrs["flat_rows_removed"] = flat_count
    df.attrs["gaps_over_5m"] = int((df.index.to_series().diff() > pd.Timedelta(minutes=5)).sum())
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


def bid_stop_to_executable(stop_bid: float, direction: str, full_spread: float) -> float:
    """Convert BID-candle structure levels to the side on which the stop executes.

    BUY stops execute on BID, so the BID-derived level is unchanged.
    SELL stops execute on ASK, so add the full BID/ASK spread.
    """
    if direction == "BUY":
        return stop_bid
    if direction == "SELL":
        return stop_bid + full_spread
    raise ValueError(f"Unsupported direction: {direction}")


def backtest_pair(pair: str, m5: pd.DataFrame, spread_pips: float, slippage_pips: float = 0.2) -> list[dict]:
    """Dukascopy input is BID OHLC. Model ASK as BID + full spread; apply adverse slippage on fills."""
    if spread_pips <= 0 or slippage_pips < 0:
        raise ValueError("spread_pips must be positive and slippage_pips cannot be negative")
    pip = 0.01 if "JPY" in pair else 0.0001
    spread = spread_pips * pip
    slippage = slippage_pips * pip
    # Calculate indicators once per timeframe. The signal engine recognises
    # these prepared columns and reuses them for every historical decision.
    m5 = bot3.add_indicators(m5)
    m15_all = bot3.add_indicators(resample_ohlc(m5, "15min"))
    h1_all = bot3.add_indicators(resample_ohlc(m5, "1h"))
    results = []
    i = 800
    while i < len(m5) - 2:
        candle_time = m5.index[i]
        decision_time = candle_time + pd.Timedelta(minutes=5)
        if not bot3.in_session(decision_time.to_pydatetime()):
            i += 1
            continue
        # Never enter across a missing/closed M5 interval.
        if m5.index[i + 1] != candle_time + pd.Timedelta(minutes=5):
            i += 1
            continue
        m15 = m15_all.loc[m15_all.index + pd.Timedelta(minutes=15) <= decision_time]
        h1 = h1_all.loc[h1_all.index + pd.Timedelta(hours=1) <= decision_time]
        if len(m15) < 60 or len(h1) < 60:
            i += 1
            continue
        history = m5.iloc[:i + 1]
        close = float(history.Close.iloc[-1])
        synthetic_bid, synthetic_ask = close, close + spread
        synthetic_mid = close + spread / 2
        signal = bot3.build_signal(
            pair, history, m15, h1, synthetic_mid, decision_time,
            synthetic_bid, synthetic_ask, decision_time,
        )
        if not signal:
            i += 1
            continue

        # Executable fill at next BID candle open: BUY pays full spread; SELL receives bid.
        next_open = float(m5.Open.iloc[i + 1])
        fill = next_open + spread + slippage if signal["direction"] == "BUY" else next_open - slippage
        # Both next_open and signal.entry are BID prices. Comparing their
        # difference keeps spread from artificially changing signal eligibility.
        if abs(next_open - signal["entry"]) > bot3.MAX_ENTRY_DRIFT_ATR * signal["atr"]:
            i += 1
            continue
        # Signal structure is calculated from BID OHLC. BUY stops execute on BID;
        # SELL stops execute on ASK and therefore need the FULL spread added.
        stop = bid_stop_to_executable(float(signal["stop"]), signal["direction"], spread)
        risk = fill - stop if signal["direction"] == "BUY" else stop - fill
        if risk <= 0 or risk > bot3.MAX_STOP_ATR * signal["atr"]:
            i += 1
            continue
        target = fill + risk * bot3.MIN_RR if signal["direction"] == "BUY" else fill - risk * bot3.MIN_RR

        outcome, exit_time, pnl_r = "OPEN", "", None
        for j in range(i + 1, len(m5)):
            bar = m5.iloc[j]
            # BUY exits execute on BID; SELL exits execute on ASK = BID + full spread.
            # stop/target are already expressed on the appropriate executable side.
            if signal["direction"] == "BUY":
                stop_hit = float(bar.Low) <= stop
                target_hit = float(bar.High) >= target
                gap_stop = float(bar.Open) <= stop
                gap_target = float(bar.Open) >= target
            else:
                stop_hit = float(bar.High) + spread >= stop
                target_hit = float(bar.Low) + spread <= target
                gap_stop = float(bar.Open) + spread >= stop
                gap_target = float(bar.Open) + spread <= target

            if stop_hit and target_hit:
                outcome, exit_time, pnl_r = "AMBIGUOUS", m5.index[j].isoformat(), None
                break
            if gap_stop or stop_hit:
                exit_px = (float(bar.Open) if signal["direction"] == "BUY" else float(bar.Open) + spread) if gap_stop else stop
                exit_px = exit_px - slippage if signal["direction"] == "BUY" else exit_px + slippage
                pnl = (exit_px - fill) if signal["direction"] == "BUY" else (fill - exit_px)
                outcome, exit_time, pnl_r = "LOSS", m5.index[j].isoformat(), pnl / risk
                break
            if gap_target or target_hit:
                exit_px = (float(bar.Open) if signal["direction"] == "BUY" else float(bar.Open) + spread) if gap_target else target
                exit_px = exit_px - slippage if signal["direction"] == "BUY" else exit_px + slippage
                pnl = (exit_px - fill) if signal["direction"] == "BUY" else (fill - exit_px)
                outcome, exit_time, pnl_r = "WIN", m5.index[j].isoformat(), pnl / risk
                break

        results.append({
            "pair": pair, "direction": signal["direction"],
            "signal_time_utc": decision_time.isoformat(),
            "fill_time_utc": m5.index[i + 1].isoformat(),
            "entry_fill": fill, "stop": stop, "target": target,
            "m5_ema21_slope_atr": signal.get("m5_ema21_slope_atr"),
            "m15_ema20_50_gap_atr": signal.get("m15_ema20_50_gap_atr"),
            "h1_ema20_50_gap_atr": signal.get("h1_ema20_50_gap_atr"),
            "confirmation_body_ratio": signal.get("confirmation_body_ratio"),
            "confirmation_close_location": signal.get("confirmation_close_location"),
            "pullback_distance_atr": signal.get("pullback_distance_atr"),
            "spread_pips_assumed": spread_pips, "slippage_pips_assumed": slippage_pips, "outcome": outcome,
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
    parser.add_argument("--slippage-pips", type=float, default=0.2, help="adverse slippage per fill in pips; default 0.2")
    parser.add_argument("--out", type=Path, default=Path("bot3_backtest_results.csv"))
    args = parser.parse_args()
    bars = load_m5(args.csv)
    rows = backtest_pair(args.pair.upper(), bars, args.spread_pips, args.slippage_pips)
    out = pd.DataFrame(rows)
    out.to_csv(args.out, index=False)
    closed = out[out.outcome.isin(["WIN", "LOSS"])] if not out.empty else out
    wins = int((closed.outcome == "WIN").sum()) if not closed.empty else 0
    losses = int((closed.outcome == "LOSS").sum()) if not closed.empty else 0
    ambiguous = int((out.outcome == "AMBIGUOUS").sum()) if not out.empty else 0
    total_r = float(closed.result_R.sum()) if not closed.empty else 0.0
    win_rate = 100 * wins / (wins + losses) if wins + losses else 0.0
    print(f"Pair: {args.pair.upper()} | usable M5 bars: {len(bars)} | raw: {bars.attrs.get('raw_rows', len(bars))}")
    print(f"Data QA: removed {bars.attrs.get('flat_rows_removed', 0)} zero-range bars; "
          f"{bars.attrs.get('gaps_over_5m', 0)} gaps longer than 5 minutes")
    print(f"Signals: {len(out)} | Wins: {wins} | Losses: {losses} | Ambiguous: {ambiguous}")
    print(f"Win rate (closed unambiguous): {win_rate:.1f}% | Net R: {total_r:.2f}")
    print(f"Execution model: BID OHLC + {args.spread_pips:.2f} pip full spread + {args.slippage_pips:.2f} pip adverse slippage per fill")
    print(f"Results saved: {args.out}")
    print("OPEN trades are censored, not counted. Ambiguous same-bar stop/target hits are excluded.")


if __name__ == "__main__":
    main()
