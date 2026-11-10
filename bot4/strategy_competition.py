"""Predeclared Bot 3 filter/RR/spread competition; research only, never sends alerts."""
from __future__ import annotations
import argparse
from pathlib import Path
import pandas as pd
from bot3 import bot3
from bot3.backtest import load_m5, backtest_pair, prepare_backtest_context

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
    "htf_alignment_momentum": lambda s: (
        s.get("m15_directional_trend_gap_atr", 0) >= 0.05 and
        s.get("h1_directional_trend_gap_atr", 0) >= 0.05 and
        s.get("directional_momentum_6_atr", 0) >= 0.20
    ),
    "trend_pullback_confirmation": lambda s: (
        s.get("m15_directional_trend_gap_atr", 0) >= 0.05 and
        s.get("h1_directional_trend_gap_atr", 0) >= 0.05 and
        1 <= s.get("pullback_age_bars", 99) <= 4 and
        s.get("confirmation_body_ratio", 0) >= 0.55
    ),
    "momentum_room_confirmation": lambda s: (
        s.get("directional_momentum_3_atr", 0) >= 0.10 and
        s.get("directional_momentum_6_atr", 0) >= 0.20 and
        s.get("opposing_distance_24_atr", 0) >= 1.00 and
        s.get("confirmation_body_ratio", 0) >= 0.55
    ),
    "shallow_fresh_pullback": lambda s: (
        1 <= s.get("pullback_age_bars", 99) <= 4 and
        0.05 <= s.get("pullback_depth_atr", -1) <= 0.30 and
        s.get("confirmation_body_ratio", 0) >= 0.55
    ),
    "high_conviction_confluence": lambda s: (
        s.get("m15_directional_trend_gap_atr", 0) >= 0.10 and
        s.get("h1_directional_trend_gap_atr", 0) >= 0.10 and
        s.get("directional_momentum_6_atr", 0) >= 0.25 and
        s.get("opposing_distance_24_atr",  0) >= 0.80 and
        s.get("confirmation_body_ratio", 0) >= 0.65
    ),
    "combined_quality": lambda s: (
        s.get("directional_momentum_3_atr", 0) >= 0.15 and
        s.get("directional_bar_fraction_6", 0) >= 0.50 and
        s.get("m15_directional_trend_gap_atr", 0) >= 0.10 and
        s.get("h1_directional_trend_gap_atr", 0) >= 0.10 and
        s.get("opposing_distance_24_atr", 0) >= 0.80 and
        s.get("confirmation_body_ratio", 0) >= 0.60
    ),
    "h1_dominant_momentum": lambda s: (
        s.get("h1_directional_trend_gap_atr", 0) >= 0.12 and
        s.get("directional_momentum_6_atr", 0) >= 0.20
    ),
    "h1_strong_pullback": lambda s: (
        s.get("h1_directional_trend_gap_atr", 0) >= 0.10 and
        1 <= s.get("pullback_age_bars", 99) <= 3 and
        0.05 <= s.get("pullback_depth_atr", -1) <= 0.40
    ),
    "breakout_with_room": lambda s: (
        s.get("breakout_margin_12_atr", 0) >= 0.03 and
        s.get("opposing_distance_24_atr", 0) >= 1.00 and
        s.get("confirmation_body_ratio", 0) >= 0.55
    ),
    "momentum_breakout": lambda s: (
        s.get("directional_momentum_3_atr", 0) >= 0.15 and
        s.get("directional_momentum_6_atr", 0) >= 0.25 and
        s.get("breakout_margin_12_atr", 0) >= 0.02
    ),
    "tight_pullback_strong_trend": lambda s: (
        s.get("h1_directional_trend_gap_atr", 0) >= 0.12 and
        s.get("m15_directional_trend_gap_atr", 0) >= 0.08 and
        1 <= s.get("pullback_age_bars", 99) <= 3 and
        0.05 <= s.get("pullback_depth_atr", -1) <= 0.30 and
        s.get("confirmation_body_ratio", 0) >= 0.60
    ),
    "balanced_confluence": lambda s: (
        s.get("h1_directional_trend_gap_atr", 0) >= 0.08 and
        s.get("m15_directional_trend_gap_atr", 0) >= 0.05 and
        s.get("directional_momentum_6_atr", 0) >= 0.15 and
        s.get("opposing_distance_24_atr", 0) >= 0.80 and
        s.get("confirmation_body_ratio", 0) >= 0.55
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


def _run_pair_train_validation(pair, data_dir, rr_values, spread_values):
    """Run one independent pair in its own process; preserve per-pair chronology."""
    original_build, original_rr = bot3.build_signal, bot3.MIN_RR
    detail_rows = []
    configs = [(name, rule, float(rr), float(mult))
               for name, rule in CANDIDATES.items()
               for rr in rr_values
               for mult in spread_values]
    try:
        path = data_dir / f"{pair}_M5.csv"
        if not path.exists():
            raise FileNotFoundError(f"Required pair history missing: {path}")
        data = load_m5(path)
        if len(data) < 30000:
            raise ValueError(f"{pair}: only {len(data)} candles; insufficient for chronological train/validation/test")
        n = len(data)
        train_end, validation_end = int(n * .60), int(n * .80)
        boundaries = {
            "TRAIN": (0, train_end),
            "VALIDATION": (train_end, validation_end),
            "FINAL_TEST": (validation_end, n),
        }
        base_spread = SPREADS[pair]
        # Candidate choice is made only on the middle validation block.
        # Signal generation is the expensive part of this competition. The
        # market inputs and signal features are identical across filter
        # candidates and RR targets at a given candle/spread. Cache the
        # unfiltered point-in-time signal once, then apply each predeclared
        # candidate rule to that immutable result. Spread remains in the
        # cache key because live bid/ask eligibility is spread-sensitive.
        for period in ("TRAIN", "VALIDATION"):
            start, end = boundaries[period]
            warm_start = max(0, start - 1000)
            frame = data.iloc[warm_start:end].copy()
            period_start = data.index[start]
            # Shared, immutable market timeline/indicator context: it does not
            # depend on candidate rules, RR, or spread assumptions.
            prepared_context = prepare_backtest_context(frame)
            signal_cache = {}
            cache_hits = 0
            cache_misses = 0

            def cached_build(*a, **kw):
                nonlocal cache_hits, cache_misses
                pair_key = str(a[0])
                history = a[1]
                bid = float(a[6]) if len(a) > 6 and a[6] is not None else None
                ask = float(a[7]) if len(a) > 7 and a[7] is not None else None
                key = (pair_key, history.index[-1], bid, ask)
                if key not in signal_cache:
                    signal_cache[key] = original_build(*a, **kw)
                    cache_misses += 1
                else:
                    cache_hits += 1
                return signal_cache[key]

            for candidate, rule, rr, spread_mult in configs:
                def filtered(*a, _rule=rule, **kw):
                    signal = cached_build(*a, **kw)
                    return signal if signal and _rule(signal) else None
                bot3.build_signal, bot3.MIN_RR = filtered, rr
                rows = backtest_pair(
                    pair, frame, base_spread * spread_mult, 0.2,
                    prepared=prepared_context,
                )
                rows = [r for r in rows if pd.Timestamp(r["signal_time_utc"]) >= period_start]
                detail_rows.append({
                    "pair":pair, "period":period, "candidate":candidate, "rr":rr,
                    "spread_multiplier":spread_mult, "spread_pips":round(base_spread*spread_mult,3),
                    "slippage_pips_per_fill":0.2, **metrics(rows)
                })
                print(pair, period, candidate, "RR", rr, "spread x", spread_mult,
                      metrics(rows), flush=True)
            print(f"{pair} {period} signal-cache: {cache_misses} misses, {cache_hits} hits, "
                  f"{len(signal_cache)} cached point-in-time signals", flush=True)

    finally:
        bot3.build_signal, bot3.MIN_RR = original_build, original_rr
    return detail_rows

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("research-data"))
    parser.add_argument("--out-dir", type=Path, default=Path("research-results/competition"))
    parser.add_argument("--pairs", default=",".join(SPREADS))
    parser.add_argument("--rr", default="1.0,1.5")
    parser.add_argument("--spread-multipliers", default="1.0,1.5")
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    original_build, original_rr = bot3.build_signal, bot3.MIN_RR
    detail_rows, test_rows = [], []
    configs = [(name, rule, float(rr), float(mult))
               for name, rule in CANDIDATES.items()
               for rr in args.rr.split(",")
               for mult in args.spread_multipliers.split(",")]
    try:
        from concurrent.futures import ProcessPoolExecutor
        pairs = [p.strip().upper() for p in args.pairs.split(",") if p.strip()]
        # Pair histories are independent; each worker preserves its own trade sequence.
        with ProcessPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(_run_pair_train_validation, pair, args.data_dir,
                                args.rr.split(","), args.spread_multipliers.split(","))
                    for pair in pairs]
            for job in jobs:
                detail_rows.extend(job.result())
    finally:
        bot3.build_signal, bot3.MIN_RR = original_build, original_rr

    detail = pd.DataFrame(detail_rows)
    if detail.empty:
        raise RuntimeError("No train/validation candidate results produced")
    detail.to_csv(args.out_dir / "candidate_train_validation.csv", index=False)
    val = detail[detail.period == "VALIDATION"].copy()
    grouped = val.groupby(["candidate","rr","spread_multiplier"], dropna=False).agg(
        pairs=("pair","nunique"), trades=("trades","sum"), wins=("wins","sum"),
        losses=("losses","sum"), net_R=("net_R","sum"),
        profitable_pairs=("net_R",lambda s:int((s>0).sum())),
        max_pair_drawdown_R=("max_drawdown_R","max"),
    ).reset_index()
    grouped["win_rate_pct"] = 100 * grouped.wins / (grouped.wins + grouped.losses).replace(0, pd.NA)
    # Predeclared selection: sufficient sample, positive expectancy, PF proxy
    # (net R) and broad pair participation. No final-test values influence ranking.
    grouped["eligible_on_validation"] = (
        (grouped.trades >= 100) & (grouped.win_rate_pct >= 70) & (grouped.net_R > 0) &
        (grouped.profitable_pairs >= (grouped.pairs * .60).apply(lambda x:int(x + .999)))
    )
    eligible = grouped[grouped.eligible_on_validation].sort_values(
        ["net_R","win_rate_pct","trades"], ascending=[False,False,False])
    grouped.to_csv(args.out_dir / "validation_candidate_ranking.csv", index=False)
    if eligible.empty:
        print("NO CANDIDATE PASSED VALIDATION. Final test intentionally not opened.", flush=True)
        raise SystemExit("BOT 3 NOT VALIDATED: no predeclared candidate met validation gates")

    winner = eligible.iloc[0]
    chosen_name, chosen_rr, chosen_mult = str(winner.candidate), float(winner.rr), float(winner.spread_multiplier)
    chosen_rule = CANDIDATES[chosen_name]
    print(f"LOCKED candidate from validation only: {chosen_name}, RR={chosen_rr}, spread multiplier={chosen_mult}", flush=True)

    # One locked candidate is evaluated once on the final chronological 20%.
    for pair in [p.strip().upper() for p in args.pairs.split(",") if p.strip()]:
        data = load_m5(args.data_dir / f"{pair}_M5.csv")
        start = int(len(data) * .80)
        frame = data.iloc[max(0,start-1000):].copy()
        period_start = data.index[start]
        def filtered(*a, _rule=chosen_rule, **kw):
            signal = original_build(*a, **kw)
            return signal if signal and _rule(signal) else None
        bot3.build_signal, bot3.MIN_RR = filtered, chosen_rr
        for spread_mult in (chosen_mult,):
            rows = backtest_pair(pair, frame, SPREADS[pair] * spread_mult, 0.2)
            rows = [r for r in rows if pd.Timestamp(r["signal_time_utc"]) >= period_start]
            test_rows.append({"pair":pair,"period":"FINAL_UNTOUCHED_TEST",
                "candidate":chosen_name,"rr":chosen_rr,"spread_multiplier":spread_mult,
                "spread_pips":round(SPREADS[pair]*spread_mult,3),
                "slippage_pips_per_fill":0.2,**metrics(rows)})
            print("FINAL TEST", pair, metrics(rows), flush=True)
    bot3.build_signal, bot3.MIN_RR = original_build, original_rr
    final = pd.DataFrame(test_rows)
    final.to_csv(args.out_dir / "locked_candidate_final_test.csv", index=False)
    trades, wins, losses = int(final.trades.sum()), int(final.wins.sum()), int(final.losses.sum())
    net_r = float(final.net_R.sum())
    win_rate = 100*wins/(wins+losses) if wins+losses else 0.0
    profitable_pairs = int((final.net_R > 0).sum())
    print(f"LOCKED FINAL TEST: {trades} closed trades, {win_rate:.2f}% wins, {net_r:.2f}R net, {profitable_pairs}/{len(final)} profitable pairs", flush=True)
    if trades < 50 or win_rate < 70.0 or net_r <= 0 or profitable_pairs < max(1,int(len(final)*.60 + .999)):
        raise SystemExit("BOT 3 NOT VALIDATED: locked final test failed sample/profitability/diversification gate")
    print("PASS: locked candidate passed final chronological test. This is research validation, not a guarantee of live performance.", flush=True)

if __name__ == "__main__":
    main()
