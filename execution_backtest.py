"""Conservative OHLC execution simulator for exported historical bot signals.

This module does not generate signals. It evaluates signals produced by a
separate, point-in-time strategy replay. Entry is the next M5 bar open (never
the signal candle close), spread and slippage are charged on both sides,
and a candle touching both SL and TP is resolved as an SL-first loss.
"""
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd


@dataclass
class TradeResult:
    pair: str
    direction: str
    signal_time: str
    entry_time: str
    exit_time: str
    entry_fill: float
    exit_fill: float
    stop: float
    target: float
    outcome: str
    r_multiple: float
    bars_held: int
    exit_reason: str


def _normalise_ohlc(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"time", "pair", "open", "high", "low", "close"}
    missing = required - set(frame.columns.str.lower())
    if missing:
        raise ValueError(f"OHLC CSV missing columns: {', '.join(sorted(missing))}")
    frame = frame.copy()
    frame.columns = [str(c).lower() for c in frame.columns]
    frame["time"] = pd.to_datetime(frame["time"], utc=True, errors="raise")
    for col in ("open", "high", "low", "close"):
        frame[col] = pd.to_numeric(frame[col], errors="raise")
    if (frame[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("OHLC prices must all be positive")
    malformed = ((frame.high < frame[["open", "close", "low"]].max(axis=1)) |
                 (frame.low > frame[["open", "close", "high"]].min(axis=1)))
    if malformed.any():
        raise ValueError(f"Found {int(malformed.sum())} malformed OHLC rows")
    return frame.sort_values(["pair", "time"]).drop_duplicates(["pair", "time"], keep="last")


def _normalise_signals(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"time", "pair", "direction", "entry", "stop", "target"}
    frame = frame.copy()
    frame.columns = [str(c).lower() for c in frame.columns]
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Signals CSV missing columns: {', '.join(sorted(missing))}")
    frame["time"] = pd.to_datetime(frame["time"], utc=True, errors="raise")
    for col in ("entry", "stop", "target"):
        frame[col] = pd.to_numeric(frame[col], errors="raise")
    frame["direction"] = frame["direction"].str.upper()
    if not frame["direction"].isin(["BUY", "SELL"]).all():
        raise ValueError("Signal direction must be BUY or SELL")
    if (frame[["entry", "stop", "target"]] <= 0).any().any():
        raise ValueError("Signal prices must be positive")
    return frame.sort_values(["time", "pair"])


def simulate(signals: pd.DataFrame, candles: pd.DataFrame, *,
             spread: float = 0.0, slippage: float = 0.0,
             commission_r: float = 0.0, max_hold_bars: int = 288,
             test_start: str | None = None) -> list[TradeResult]:
    """Replay exported signals. Spread/slippage are price units, charged each side.

    Signal time is the close timestamp of the signal candle. Entry is at the
    first later candle open. Intrabar stop/target collisions are stop-first.
    """
    if spread < 0 or slippage < 0 or commission_r < 0 or max_hold_bars < 1:
        raise ValueError("Costs must be non-negative and max_hold_bars >= 1")
    sigs, bars = _normalise_signals(signals), _normalise_ohlc(candles)
    split = pd.Timestamp(test_start) if test_start else None
    if split is not None:
        split = split.tz_localize("UTC") if split.tzinfo is None else split.tz_convert("UTC")
    results: list[TradeResult] = []
    for sig in sigs.itertuples(index=False):
        if split is not None and sig.time < split:
            continue
        series = bars[bars.pair == sig.pair].reset_index(drop=True)
        later = series.index[series.time > sig.time].tolist()
        if not later:
            continue
        start = later[0]
        entry_bar = series.iloc[start]
        half_cost = spread / 2.0 + slippage
        long = sig.direction == "BUY"
        entry_fill = float(entry_bar.open) + (half_cost if long else -half_cost)
        stop, target = float(sig.stop), float(sig.target)
        risk = entry_fill - stop if long else stop - entry_fill
        if risk <= 0:
            continue
        end = min(len(series), start + max_hold_bars)
        exit_fill = None
        exit_time = None
        reason = "TIME_EXIT"
        outcome = "TIME_EXIT"
        exit_idx = end - 1
        for idx in range(start, end):
            bar = series.iloc[idx]
            if long:
                hit_stop, hit_target = float(bar.low) <= stop, float(bar.high) >= target
            else:
                hit_stop, hit_target = float(bar.high) >= stop, float(bar.low) <= target
            if hit_stop:  # conservative if both levels occur in one OHLC bar
                raw = stop
                reason, outcome, exit_idx = "STOP", "LOSS", idx
            elif hit_target:
                raw = target
                reason, outcome, exit_idx = "TARGET", "WIN", idx
            else:
                continue
            exit_fill = raw + (-half_cost if long else half_cost)
            exit_time = bar.time
            break
        if exit_fill is None:
            last = series.iloc[exit_idx]
            raw = float(last.close)
            exit_fill = raw + (-half_cost if long else half_cost)
            exit_time = last.time
        pnl = (exit_fill - entry_fill) if long else (entry_fill - exit_fill)
        r_mult = pnl / risk - commission_r
        if reason == "TIME_EXIT":
            outcome = "WIN" if r_mult > 0 else "LOSS" if r_mult < 0 else "BREAK_EVEN"
        results.append(TradeResult(
            pair=str(sig.pair), direction=sig.direction, signal_time=sig.time.isoformat(),
            entry_time=entry_bar.time.isoformat(), exit_time=exit_time.isoformat(),
            entry_fill=entry_fill, exit_fill=exit_fill, stop=stop, target=target,
            outcome=outcome, r_multiple=r_mult, bars_held=exit_idx - start + 1,
            exit_reason=reason))
    return results


def report(results: Iterable[TradeResult]) -> dict:
    rows = list(results)
    if not rows:
        return {"trades": 0, "warning": "No trades could be simulated; check signal/candle overlap."}
    rs = [r.r_multiple for r in rows]
    wins = sum(x > 0 for x in rs)
    gross_win = sum(x for x in rs if x > 0)
    gross_loss = -sum(x for x in rs if x < 0)
    equity, peak, max_dd = 0.0, 0.0, 0.0
    for value in rs:
        equity += value
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return {
        "trades": len(rows), "wins": wins, "losses": sum(x < 0 for x in rs),
        "break_even": sum(x == 0 for x in rs),
        "win_rate_pct": round(100 * wins / len(rows), 2),
        "net_r": round(sum(rs), 3), "expectancy_r": round(sum(rs) / len(rows), 4),
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss else None,
        "max_drawdown_r": round(max_dd, 3),
        "average_r": round(sum(rs) / len(rows), 4),
        "note": "Simulation only; results depend on input data quality and cost assumptions. Not broker fills or a profitability guarantee."
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--signals", required=True, help="CSV: time,pair,direction,entry,stop,target")
    parser.add_argument("--candles", required=True, help="CSV: time,pair,open,high,low,close")
    parser.add_argument("--spread", type=float, default=0.0, help="Full spread in price units")
    parser.add_argument("--slippage", type=float, default=0.0, help="Per-side slippage in price units")
    parser.add_argument("--commission-r", type=float, default=0.0, help="Round-trip commission expressed in R")
    parser.add_argument("--max-hold-bars", type=int, default=288)
    parser.add_argument("--test-start", help="UTC timestamp; only signals on/after this date are included")
    parser.add_argument("--out", default="backtest_results.csv")
    args = parser.parse_args()
    trades = simulate(pd.read_csv(args.signals), pd.read_csv(args.candles),
                      spread=args.spread, slippage=args.slippage,
                      commission_r=args.commission_r, max_hold_bars=args.max_hold_bars,
                      test_start=args.test_start)
    pd.DataFrame([asdict(t) for t in trades]).to_csv(args.out, index=False)
    print(json.dumps(report(trades), indent=2))
    print(f"Trade ledger: {args.out}")


if __name__ == "__main__":
    main()
