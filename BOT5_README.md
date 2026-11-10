# Forex Bot 5 — independent strategy research

Bot 5 is isolated on branch `bot5-independent-strategy-lab`. It does not change Bot 3 or Bot 4 and does not send Telegram alerts.

## Research question
Test whether a selective, volatility-normalized trend/momentum pullback family can survive out-of-sample testing better than simpler baseline rules. The candidate set is predeclared; it is not described as a proven "best strategy".

## Why this family
Published FX research finds momentum effects in currencies, but also finds that technical-rule performance weakens out of sample and can disappear after transaction costs. Therefore the project tests momentum/trend continuation under explicit costs rather than assuming published historical effects remain tradable. See Hutchinson et al. (2022), *Technical trading rule profitability in currencies: It's all about momentum*, and Menkhoff et al. (2012), *Currency momentum strategies*.

## Fixed test design
- 27 major/minor pairs; M5 entries with M15/H1 context.
- 36 months of Dukascopy BID candles.
- Chronological 60% train / 20% validation / 20% locked final test.
- Candidate selection uses validation only; final test is run once after selection.
- Full assumed spread plus adverse slippage; ambiguous same-candle exits excluded from win-rate denominator and reported separately.
- One open trade per pair at a time; no overlapping trade accounting.
- Minimum final-test gate: 50 closed unambiguous trades, 70% wins, positive net R, and at least 60% of tested pairs profitable. Failure means no deployment.
- No 1-pip stop; stops remain based on confirmed swing structure with ATR sanity bounds in the shared engine.
- No SMC or liquidity-sweep requirement.

## Candidate hypotheses
Baseline pullback, H1 momentum, multi-timeframe trend, momentum plus room, breakout confirmation, fresh shallow pullback, trend-momentum pullback, high-conviction low-chase, and volatility-normalized trend.

These are research candidates, not guaranteed profitable strategies. A pass is not a promise of future returns. The shared execution engine and data-source limitations must be reviewed before any live deployment.

## Run
`python -m bot5.strategy_competition --data-dir research-data --out-dir research-results/bot5`
