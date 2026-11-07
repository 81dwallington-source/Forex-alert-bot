"""Predeclared Bot 3 filter/RR/spread competition; research only, never sends alerts."""
from __future__ import annotations
import argparse
from pathlib import Path
import pandas as pd
from bot3 import bot3
from bot3.backtest import load_m5, backtest_pair

# Fixed hypotheses: no thresholds are fitted from the results of this run.
CANDIDATES = {
    "baseline": lambda s: True,
    "sell_only": lambda s: s.get("direction") == "SELL",
    "buy_only": lambda s: s.get("direction") == "BUY",
    "momentum_confirmed": lambda s: (
        s.get("directional_momentum_3_atr", 0) >= 0.15 and
        s.get("directional_momentum_6_atr", 0) >= 0.25 and
        s.get("directional_bar_fraction_6", 0) >= 0.50
    ),
    "strong_htf_trend": lambda s: (
        s.get("m15_directional_trend_gap_atr", 0) >= 0.10 and
        s.get("h1_directional_trend_gap_atr", 0) >= 0.10
    ),
    "clean_confirmation": lambda s: (
        s.get("confirmation_body_ratio", 0) >= 0.65 and
        (s.get("confirmation_close_location", 0) >= 0.80 if s.get("direction") == "BUY"
         else s.get("confirmation_close_location", 1) <= 0.20)
    ),
    "room_to_target": lambda s: s.get("opposing_distance_24_atr", 0) >= 0.80,
    "fresh_pullback": lambda s: (
        1 <= s.get("pullback_age_bars", 99) <= 3 and
        0.05 <= s.get("pullback_depth_atr", -1) <= 0.45
    ),
    "combined_quality": lambda s: (
        s.get("directional_momentum_3_atr", 0) >= 0.15 and
        s.get("directional_bar_fraction_6", 0) >= 0.50 and
        s.get("m15_directional_trend_gap_atr", 0) >= 0.10 and
        s.get("h1_directional_trend_gap_atr", 0) >= 0.10 and
        s.get("opposing_distance_24_atr", 0) >= 0.80 and
        s.get("confirmation_body_ratio", 0) >= 0.60
    ),
}

SPREADS = {
    "EURUSD":1.2,"GBPUSD":1.5,"USDJPY":1.3,"USDCHF":1.5,"USDCAD":1.6,
    "AUDUSD":1.4,"NZDUSD":1.8,"EURGBP":1.5,"EURJPY":2.0,"EURCHF":2.0,
    "EURAUD":2.5,"EURCAD":2.5,"EURNZD":3.0,"GBPJPY":2.5,"GBPCHF":2.5,
    "GBPAUD":3.0,"GBPCAD":3.0,"GBPNZD":3.5,"AUDJPY":2.0,"AUDCHF":2.0,
    "AUDCAD":2.0,"AUDNZD":2.5,"NZDJPY":2.5,"NZDCHF":2.5,"NZDCAD":2.5,
    "CADJPY":2.0,"CHFJPY":2.5,
}

def metrics(rows):
    closed = [r for r in rows if r.get("outcome") in ("WIN", "LOSS") and r.get("result_R") is not None]
    rs = [float(r["result_R"]) for r in closed]
    wins = sum(r["outcome"] == "WIN" for r in closed)
    gross_win = sum(x for x in rs if x > 0)
    gross_loss = -sum(x for x in rs if x < 0)
    equity = peak = drawdown = 0.0
    for value in rs:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return {
        "trades": len(closed), "wins": wins, "losses": len(closed) - wins,
        "win_rate_pct": round(100 * wins / len(closed), 2) if closed else None,
        "net_R": round(sum(rs), 2),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
        "max_drawdown_R": round(drawdown, 2),
        "ambiguous": sum(r.get("outcome") == "AMBIGUOUS" for r in rows),
        "open": sum(r.get("outcome") == "OPEN" for r in rows),
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("research-data"))
    parser.add_argument("--out-dir", type=Path, default=Path("research-results/competition"))
    parser.add_argument("--pairs", default=",".join(SPREADS))
    parser.add_argument("--rr", default="1.0,1.5")
    parser.add_argument("--spread-multipliers", default="1.0,1.5")
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    original_build = bot3.build_signal
    original_rr = bot3.MIN_RR
    report = []
    try:
        for pair in [p.strip().upper() for p in args.pairs.split(",") if p.strip()]:
            path = args.data_dir / f"{pair}_M5.csv"
            if not path.exists():
                print(f"SKIP {pair}: missing {path}", flush=True)
                continue
            data = load_m5(path)
            split = int(len(data) * 0.70)
            split_time = data.index[split]
            periods = {"IS": data.iloc[:split], "OOS": data.iloc[max(0, split - 1000):]}
            base_spread = SPREADS.get(pair)
            if base_spread is None:
                print(f"SKIP {pair}: no predeclared spread", flush=True)
                continue
            for period, frame in periods.items():
                for candidate, rule in CANDIDATES.items():
                    def filtered(*a, _rule=rule, **kw):
                        signal = original_build(*a, **kw)
                        return signal if signal and _rule(signal) else None
                    bot3.build_signal = filtered
                    for rr in [float(x) for x in args.rr.split(",")]:
                        bot3.MIN_RR = rr
                        for spread_mult in [float(x) for x in args.spread_multipliers.split(",")]:
                            spread = base_spread * spread_mult
                            rows = backtest_pair(pair, frame.copy(), spread, 0.2)
                            if period == "OOS":
                                rows = [r for r in rows if pd.Timestamp(r["signal_time_utc"]) >= split_time]
                            result = metrics(rows)
                            report.append({
                                "pair": pair, "period": period, "candidate": candidate,
                                "rr": rr, "spread_multiplier": spread_mult,
                                "spread_pips": round(spread, 3), "slippage_pips_per_fill": 0.2,
                                "split_time": str(split_time), **result,
                            })
                            print(pair, period, candidate, "RR", rr, "spread x", spread_mult, result, flush=True)
    finally:
        bot3.build_signal = original_build
        bot3.MIN_RR = original_rr

    detail = pd.DataFrame(report)
    detail.to_csv(args.out_dir / "candidate_results.csv", index=False)
    if detail.empty:
        raise RuntimeError("No candidate results produced")
    grouped = detail.groupby(["period", "candidate", "rr", "spread_multiplier"], dropna=False).agg(
        pairs=("pair", "nunique"), trades=("trades", "sum"), wins=("wins", "sum"),
        losses=("losses", "sum"), net_R=("net_R", "sum"),
        profitable_pairs=("net_R", lambda s: int((s > 0).sum())),
        median_pair_net_R=("net_R", "median"),
    ).reset_index()
    grouped["win_rate_pct"] = 100 * grouped.wins / (grouped.wins + grouped.losses).replace(0, pd.NA)
    grouped["eligible_for_review"] = (
        (grouped["period"] == "OOS") & (grouped["trades"] >= 100) &
        (grouped["net_R"] > 0) & (grouped["profitable_pairs"] >= (grouped["pairs"] * 0.60).apply(lambda x: int(x + 0.999)))
    )
    grouped["target_70pct_met"] = (
        (grouped["period"] == "OOS") & (grouped["trades"] >= 100) &
        (grouped["win_rate_pct"] >= 70.0) & (grouped["net_R"] > 0) &
        (grouped["profitable_pairs"] >= (grouped["pairs"] * 0.60).apply(lambda x: int(x + 0.999)))
    )
    grouped["not_a_live_approval"] = True
    grouped.to_csv(args.out_dir / "candidate_summary.csv", index=False)
    print("Research complete. Eligibility is a screening gate only; no candidate is automatically deployed.", flush=True)

if __name__ == "__main__":
    main()
