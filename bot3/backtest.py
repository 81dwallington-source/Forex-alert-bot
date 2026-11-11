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
    # A zero-range M5 candle is still a real time interval: it can represent
    # no price movement. Keep it so M15/H1 resampling does not lose otherwise
    # complete bars or create artificial gaps in the indicator history.
    df.attrs["raw_rows"] = raw_count
    df.attrs["flat_rows"] = flat_count
    df.attrs["flat_rows_removed"] = 0
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



def prepare_backtest_context(m5: pd.DataFrame):
    """Prepare candidate-independent indicators and eligible decision timeline once."""
    prepared_m5 = bot3.add_indicators(m5)
    m15_all = bot3.add_indicators(resample_ohlc(prepared_m5, "15min"))
    h1_all = bot3.add_indicators(resample_ohlc(prepared_m5, "1h"))
    decisions = []
    for i in range(800, len(prepared_m5) - 2):
        candle_time = prepared_m5.index[i]
        decision_time = candle_time + pd.Timedelta(minutes=5)
        if not bot3.in_session(decision_time.to_pydatetime()):
            continue
        if prepared_m5.index[i + 1] != candle_time + pd.Timedelta(minutes=5):
            continue
        m15_end = m15_all.index.searchsorted(
            decision_time - pd.Timedelta(minutes=15), side="right"
        )
        h1_end = h1_all.index.searchsorted(
            decision_time - pd.Timedelta(hours=1), side="right"
        )
        if m15_end >= 60 and h1_end >= 60:
            decisions.append((i, m15_end, h1_end))
    return prepared_m5, m15_all, h1_all, decisions

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


def backtest_pair(pair: str, m5: pd.DataFrame, spread_pips: float, slippage_pips: float = 0.2, prepared=None) -> list[dict]:
    """Dukascopy input is BID OHLC. Model ASK as BID + full spread; apply adverse slippage on fills."""
    if spread_pips <= 0 or slippage_pips < 0:
        raise ValueError("spread_pips must be positive and slippage_pips cannot be negative")
    pip = 0.01 if "JPY" in pair else 0.0001
    spread = spread_pips * pip
    slippage = slippage_pips * pip
    # Reuse candidate-independent indicators and the eligible M5 decision
    # timeline across every filter/RR/spread candidate in this period.
    if prepared is None:
        prepared = prepare_backtest_context(m5)
    m5, m15_all, h1_all, decisions = prepared
    results = []
    next_allowed_i = 800
    # Convert the full OHLC columns once. The old loop repeatedly created
    # pandas slices and copied the entire remaining history for every trade.
    # NumPy slices below are views, preserving the exact candle ordering and
    # first-hit logic while avoiding repeated full-tail conversions.
    all_opens = m5["Open"].to_numpy(dtype=float)
    all_highs = m5["High"].to_numpy(dtype=float)
    all_lows = m5["Low"].to_numpy(dtype=float)
    for i, m15_end, h1_end in decisions:
        # Preserve the original one-open-trade-per-pair rule after an exit.
        if i < next_allowed_i:
            continue
        candle_time = m5.index[i]
        decision_time = candle_time + pd.Timedelta(minutes=5)
        m15 = m15_all.iloc[m15_end - 60:m15_end]
        h1 = h1_all.iloc[h1_end - 60:h1_end]
        history = m5.iloc[max(0, i - 59):i + 1]
        close = float(history.Close.iloc[-1])
        synthetic_bid, synthetic_ask = close, close + spread
        synthetic_mid = close + spread / 2
        signal = bot3.build_signal(
            pair, history, m15, h1, synthetic_mid, decision_time,
            synthetic_bid, synthetic_ask, decision_time,
        )
        if not signal:
            continue

        # Executable fill at next BID candle open: BUY pays full spread; SELL receives bid.
        next_open = float(m5.Open.iloc[i + 1])
        fill = next_open + spread + slippage if signal["direction"] == "BUY" else next_open - slippage
        # Both next_open and signal.entry are BID prices. Comparing their
        # difference keeps spread from artificially changing signal eligibility.
        if abs(next_open - signal["entry"]) > bot3.MAX_ENTRY_DRIFT_ATR * signal["atr"]:
            continue
        # Signal structure is calculated from BID OHLC. BUY stops execute on BID;
        # SELL stops execute on ASK and therefore need the FULL spread added.
        stop = bid_stop_to_executable(float(signal["stop"]), signal["direction"], spread)
        risk = fill - stop if signal["direction"] == "BUY" else stop - fill
        # Reject structurally tiny stops: require at least 0.45 ATR while
        # retaining the confirmed swing-based stop from the signal builder.
        if risk < 0.45 * signal["atr"] or risk > bot3.MAX_STOP_ATR * signal["atr"]:
            continue
        target = fill + risk * bot3.MIN_RR if signal["direction"] == "BUY" else fill - risk * bot3.MIN_RR

        # Vectorised first-hit scan: identical candle-by-candle precedence,
        # but avoids millions of Python-level pandas row iterations.
        outcome, exit_time, pnl_r = "OPEN", "", None
        opens = all_opens[i + 1:]
        highs = all_highs[i + 1:]
        lows = all_lows[i + 1:]
        if signal["direction"] == "BUY":
            stop_hits = lows <= stop
            target_hits = highs >= target
            gap_stops = opens <= stop
            gap_targets = opens >= target
            executable_opens = opens
        else:
            executable_opens = opens + spread
            stop_hits = highs + spread >= stop
            target_hits = lows + spread <= target
            gap_stops = executable_opens >= stop
            gap_targets = executable_opens <= target

        hit_positions = __import__("numpy").flatnonzero(stop_hits | target_hits)
        if len(hit_positions):
            k = int(hit_positions[0])
            hit_time = m5.index[i + 1 + k].isoformat()
            if stop_hits[k] and target_hits[k]:
                outcome, exit_time, pnl_r = "AMBIGUOUS", hit_time, None
            elif gap_stops[k] or stop_hits[k]:
                raw_exit = executable_opens[k] if gap_stops[k] else stop
                exit_px = raw_exit - slippage if signal["direction"] == "BUY" else raw_exit + slippage
                pnl = (exit_px - fill) if signal["direction"] == "BUY" else (fill - exit_px)
                outcome, exit_time, pnl_r = "LOSS", hit_time, pnl / risk
            else:
                raw_exit = executable_opens[k] if gap_targets[k] else target
                exit_px = raw_exit - slippage if signal["direction"] == "BUY" else raw_exit + slippage
                pnl = (exit_px - fill) if signal["direction"] == "BUY" else (fill - exit_px)
                outcome, exit_time, pnl_r = "WIN", hit_time, pnl / risk

        results.append({
            "pair": pair, "direction": signal["direction"],
            "signal_time_utc": decision_time.isoformat(),
            "fill_time_utc": m5.index[i + 1].isoformat(),
            "entry_fill": fill, "stop": stop, "target": target,
            "m5_ema21_slope_atr": signal.get("m5_ema21_slope_atr"),
            "m5_directional_ema21_slope_atr": signal.get("m5_directional_ema21_slope_atr"),
            "m15_ema20_50_gap_atr": signal.get("m15_ema20_50_gap_atr"),
            "h1_ema20_50_gap_atr": signal.get("h1_ema20_50_gap_atr"),
            "m15_directional_trend_gap_atr": signal.get("m15_directional_trend_gap_atr"),
            "h1_directional_trend_gap_atr": signal.get("h1_directional_trend_gap_atr"),
            "confirmation_body_ratio": signal.get("confirmation_body_ratio"),
            "confirmation_close_location": signal.get("confirmation_close_location"),
            "confirmation_range_atr": signal.get("confirmation_range_atr"),
            "directional_momentum_3_atr": signal.get("directional_momentum_3_atr"),
            "directional_momentum_6_atr": signal.get("directional_momentum_6_atr"),
            "directional_bar_fraction_6": signal.get("directional_bar_fraction_6"),
            "prior_range_6_atr": signal.get("prior_range_6_atr"),
            "breakout_margin_12_atr": signal.get("breakout_margin_12_atr"),
            "opposing_distance_24_atr": signal.get("opposing_distance_24_atr"),
            "pullback_distance_atr": signal.get("pullback_distance_atr"),
            "pullback_depth_atr": signal.get("pullback_depth_atr"),
            "pullback_age_bars": signal.get("pullback_age_bars"),
            "atr_regime_60": signal.get("atr_regime_60"),
            "spread_pips_assumed": spread_pips, "slippage_pips_assumed": slippage_pips, "outcome": outcome,
            "exit_time_utc": exit_time, "result_R": pnl_r,
        })
        # One trade at a time per pair to avoid overlapping trade accounting.
        if outcome in ("WIN", "LOSS", "AMBIGUOUS") and exit_time:
            exit_idx = m5.index.get_loc(pd.Timestamp(exit_time))
            next_allowed_i = max(i + 1, exit_idx + 1)
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
    print(f"Data QA: retained {bars.attrs.get('flat_rows', 0)} zero-range bars; "
          f"{bars.attrs.get('gaps_over_5m', 0)} gaps longer than 5 minutes")
    print(f"Signals: {len(out)} | Wins: {wins} | Losses: {losses} | Ambiguous: {ambiguous}")
    print(f"Win rate (closed unambiguous): {win_rate:.1f}% | Net R: {total_r:.2f}")
    print(f"Execution model: BID OHLC + {args.spread_pips:.2f} pip full spread + {args.slippage_pips:.2f} pip adverse slippage per fill")
    print(f"Results saved: {args.out}")
    print("OPEN trades are censored, not counted. Ambiguous same-bar stop/target hits are excluded.")


if __name__ == "__main__":
    main()
