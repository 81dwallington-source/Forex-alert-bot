"""Training-only trade-path diagnostics for Bot 3. Does not change signal rules."""
from __future__ import annotations
import argparse
from pathlib import Path
import pandas as pd

from bot3 import bot3
from bot3.backtest import load_m5, backtest_pair

SPREADS = {
    "EURUSD":1.2,"GBPUSD":1.5,"USDJPY":1.3,"USDCHF":1.5,"USDCAD":1.6,
    "AUDUSD":1.4,"NZDUSD":1.8,"EURGBP":1.5,"EURJPY":2.0,"EURCHF":2.0,
    "EURAUD":2.5,"EURCAD":2.5,"EURNZD":3.0,"GBPJPY":2.5,"GBPCHF":2.5,
    "GBPAUD":3.0,"GBPCAD":3.0,"GBPNZD":3.5,"AUDJPY":2.0,"AUDCHF":2.0,
    "AUDCAD":2.0,"AUDNZD":2.5,"NZDJPY":2.5,"NZDCHF":2.5,"NZDCAD":2.5,
    "CADJPY":2.0,"CHFJPY":2.5,
}

def touch_order(m5, entry_idx, exit_idx, direction, fill, stop, risk, spread, threshold_r):
    """First threshold touch from entry through exit candle; same-bar conflicts stay ambiguous."""
    end = min(exit_idx + 1, len(m5))
    favourable_level = fill + threshold_r * risk if direction == "BUY" else fill - threshold_r * risk
    for j in range(entry_idx, end):
        bar = m5.iloc[j]
        if direction == "BUY":
            favourable = float(bar.High) >= favourable_level
            adverse = float(bar.Low) <= stop
        else:
            favourable = float(bar.Low) + spread <= favourable_level
            adverse = float(bar.High) + spread >= stop
        if favourable and adverse:
            return "SAME_BAR_AMBIGUOUS"
        if adverse:
            return "STOP_FIRST"
        if favourable:
            return "FAVOURABLE_FIRST"
    return "NEITHER_BEFORE_CENSOR"

def diagnose(pair, m5, spread_pips, slippage_pips):
    pip = 0.01 if "JPY" in pair else 0.0001
    spread = spread_pips * pip
    # Strict chronological split: the diagnostic receives only the first 60% of history.
    train_end = int(len(m5) * 0.60)
    train = m5.iloc[:train_end].copy()
    if len(train) < 30000:
        raise ValueError(f"{pair}: insufficient training history ({len(train)} bars)")
    prepared = bot3.add_indicators(train)
    rows = backtest_pair(pair, train, spread_pips, slippage_pips)
    out = []
    for row in rows:
        fill_time = pd.Timestamp(row["fill_time_utc"])
        entry_idx = train.index.get_indexer([fill_time])[0]
        if entry_idx < 0:
            raise ValueError(f"{pair}: fill timestamp not found: {fill_time}")
        exit_time_text = row.get("exit_time_utc") or ""
        closed = row.get("outcome") in ("WIN", "LOSS", "AMBIGUOUS") and bool(exit_time_text)
        if closed:
            exit_time = pd.Timestamp(exit_time_text)
            exit_idx = train.index.get_indexer([exit_time])[0]
            if exit_idx < 0:
                raise ValueError(f"{pair}: exit timestamp not found: {exit_time}")
            # Exclude the exit candle from MFE/MAE because OHLC cannot reveal its intrabar order.
            path = train.iloc[entry_idx:exit_idx]
            exit_bar = train.iloc[exit_idx]
        else:
            exit_idx = len(train)
            path = train.iloc[entry_idx:]
            exit_bar = None

        direction = row["direction"]
        fill = float(row["entry_fill"])
        stop = float(row["stop"])
        risk = abs(fill - stop)
        signal_atr = float(prepared["ATR"].iloc[entry_idx - 1]) if entry_idx > 0 else float("nan")
        if risk <= 0 or not pd.notna(signal_atr) or signal_atr <= 0:
            continue

        if len(path):
            if direction == "BUY":
                mfe = max(0.0, float(path.High.max()) - fill)
                mae = max(0.0, fill - float(path.Low.min()))
            else:
                mfe = max(0.0, fill - (float(path.Low.min()) + spread))
                mae = max(0.0, (float(path.High.max()) + spread) - fill)
        else:
            mfe = mae = 0.0

        exit_bar_range_r = None
        if exit_bar is not None:
            exit_bar_range_r = float(exit_bar.High - exit_bar.Low) / risk

        result = dict(row)
        result.update({
            "spread_multiplier": round(spread_pips / SPREADS[pair], 2),
            "signal_atr": signal_atr,
            "stop_distance_atr": risk / signal_atr,
            "pre_exit_bars": len(path),
            "pre_exit_mfe_R": mfe / risk,
            "pre_exit_mae_R": mae / risk,
            "pre_exit_mfe_atr": mfe / signal_atr,
            "pre_exit_mae_atr": mae / signal_atr,
            "exit_bar_range_R_excluded": exit_bar_range_r,
            "touch_0_50R": touch_order(train, entry_idx, exit_idx, direction, fill, stop, risk, spread, 0.50),
            "touch_0_75R": touch_order(train, entry_idx, exit_idx, direction, fill, stop, risk, spread, 0.75),
            "touch_1_00R": touch_order(train, entry_idx, exit_idx, direction, fill, stop, risk, spread, 1.00),
        })
        out.append(result)
    return out

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--out-dir", type=Path, default=Path("training-diagnostics"))
    parser.add_argument("--pairs", default=",".join(SPREADS))
    parser.add_argument("--spread-multipliers", default="1.0,1.5")
    parser.add_argument("--slippage-pips", type=float, default=0.2)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    all_rows = []
    for pair in [p.strip().upper() for p in args.pairs.split(",") if p.strip()]:
        if pair not in SPREADS:
            raise ValueError(f"Unknown pair: {pair}")
        data = load_m5(args.data_dir / f"{pair}_M5.csv")
        for mult_text in args.spread_multipliers.split(","):
            mult = float(mult_text)
            spread_pips = SPREADS[pair] * mult
            rows = diagnose(pair, data, spread_pips, args.slippage_pips)
            all_rows.extend(rows)
            print(f"{pair} spread x{mult:g}: {len(rows)} training trades", flush=True)

    trades = pd.DataFrame(all_rows)
    if trades.empty:
        raise SystemExit("No training trades produced; no validation/final-test data was accessed.")
    trades.to_csv(args.out_dir / "training_trade_path_diagnostics.csv", index=False)

    closed = trades[trades.outcome.isin(["WIN", "LOSS"])].copy()
    grouped = trades.groupby(["pair", "direction", "spread_multiplier"], dropna=False).agg(
        trades=("outcome", "size"),
        wins=("outcome", lambda s: int((s == "WIN").sum())),
        losses=("outcome", lambda s: int((s == "LOSS").sum())),
        ambiguous=("outcome", lambda s: int((s == "AMBIGUOUS").sum())),
        open_censored=("outcome", lambda s: int((s == "OPEN").sum())),
        median_stop_distance_atr=("stop_distance_atr", "median"),
        median_pre_exit_mfe_R=("pre_exit_mfe_R", "median"),
        median_pre_exit_mae_R=("pre_exit_mae_R", "median"),
        pct_stop_first_before_half_R=("touch_0_50R", lambda s: 100 * (s == "STOP_FIRST").mean()),
        pct_same_bar_ambiguous_at_half_R=("touch_0_50R", lambda s: 100 * (s == "SAME_BAR_AMBIGUOUS").mean()),
    ).reset_index()
    grouped["win_rate_pct"] = 100 * grouped.wins / (grouped.wins + grouped.losses).replace(0, pd.NA)
    grouped.to_csv(args.out_dir / "training_pair_direction_summary.csv", index=False)

    print("\nTRAINING-ONLY SUMMARY")
    print(grouped.to_string(index=False))
    print("\nFinal-test period was not loaded or evaluated.", flush=True)

if __name__ == "__main__":
    main()
