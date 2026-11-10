# Forex Bot 4 — independent strategy research

Bot 4 is a separate research track on branch `bot4-strategy-research`.
It does not replace Bot 1, Bot 2, or Bot 3 and does not send live alerts.

## Objective
Find a robust strategy that can demonstrate at least a 70% win rate on a
sufficient sample, while remaining profitable after realistic spread and
slippage assumptions. This is a research target, not a guarantee.

## Research controls
- 27 major/minor FX pairs, M5 entries with M15/H1 context.
- Predeclared candidate filters; no tuning from final-test results.
- Chronological 60% train / 20% validation / 20% untouched final test.
- Candidate selection uses validation only; the selected candidate is then
  evaluated once on the final chronological segment.
- Full spread and adverse slippage are modelled.
- Ambiguous same-candle stop/target outcomes are excluded from win-rate totals.
- No live alerts until a candidate passes validation and final-test gates.

## Candidate family
Momentum, trend strength, breakout room, pullback freshness/depth,
confirmation candle quality, and combinations of those features. This is an
initial search family, not a claim that every forex strategy has been tested.

## Run
`python -m bot4.strategy_competition --data-dir research-data --out-dir research-results/bot4`
