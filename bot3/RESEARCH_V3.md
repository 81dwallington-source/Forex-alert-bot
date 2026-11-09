# Bot 3 Research V3 — Research Only

Status: diagnostic build; NOT a live-bot change and NOT a validated strategy.

## Data boundary

The prior historical run contains chronological blocks:
- Training: first 60% (2024-01-01 through 2025-09-16 on the sampled M5 files).
- Validation: next 20% (through 2026-04-13).
- Final test: last 20%, intentionally excluded from this research.

No final-test values may be loaded, summarized, ranked, or used to select a rule.

## Findings from the existing training/validation artifact

At RR 1.0 and base assumed spreads:
- Baseline training: 396 trades, 47.22% wins, -23.63R.
- Baseline validation: 62 trades, 50.00% wins, -0.63R.
- Buy-only training: 152 trades, 50.66% wins, +4.88R.
- Buy-only validation: 25 trades, 56.00% wins, +2.76R.

Buy-only validation is not robustly diversified:
- USDJPY: 11 trades, 8 wins, +4.91R.
- EURUSD: 5 trades, 2 wins, -1.06R.
- GBPUSD: 6 trades, 3 wins, -0.07R.
- USDCAD: 2 trades, 1 win, -0.01R.
- AUDUSD: 1 trade, 0 wins, -1.01R.
- USDCHF and NZDUSD: no trades.

The buy-only improvement is therefore dominated by one pair and one small sample. Training does not confirm a stable broad edge: EURUSD buy-only was -4.39R, GBPUSD +3.79R, USDJPY -0.72R, and USDCAD +5.22R from only three trades.

Other predeclared validation candidates also failed the existing broad acceptance gate. Positive aggregate validation R alone is insufficient.

## Research V3 protocol

1. Keep all work on this separate branch. Never edit the live bot or its alert workflow.
2. Use training only to develop a small number of predeclared hypotheses. Do not optimize dozens of thresholds against validation.
3. Record each candidate's pair, direction, trade count, win/loss, net R, profit factor, drawdown, and spread sensitivity.
4. Reject candidates whose aggregate performance is driven by one pair, one direction, or too few trades.
5. Lock the candidate and its parameters before using validation.
6. Use validation once as a genuine chronological selection check. If it fails, stop and return to training research; do not open final test.
7. Keep final-test data sealed until one candidate has passed the predeclared validation gate.
8. Do not claim a 70% win rate without a sufficiently large, independently validated sample. Report actual trade counts and confidence intervals.

## Current decision

NO STRATEGY IS APPROVED. The existing evidence does not justify switching Bot 3 to buy-only, nor relaxing filters to increase alert volume. The next research iteration should diagnose why entry signals lose by examining trade-level adverse/favourable excursion, stop distance relative to ATR and swing structure, and whether the entry confirmation is already extended at the close. Those diagnostics must be computed on training data first.
