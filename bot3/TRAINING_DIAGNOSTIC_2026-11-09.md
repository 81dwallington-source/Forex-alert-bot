# Bot 3 — Training-only diagnostic (2026-11-09)

## Scope and data boundary

Source: GitHub Actions run `42485610914`, artifact `bot3-multi-pair-validation` (artifact ID `13625935274`).

This note uses only the `TRAIN` rows from `competition/candidate_train_validation.csv`. The validation rows were used only to confirm the recorded rejection outcome; no validation values were used to tune a new rule. The final-test period was not opened, read, summarized, or used.

No live-bot files or strategy rules were changed.

## Why the run failed

The seven pair-history downloads completed and the competition reached its end. It exited with the deliberate message:

`BOT 3 NOT VALIDATED: no predeclared candidate met validation gates`

The gate requires at least 50 validation trades, positive net R, and profitable results across at least 60% of the seven pairs. No candidate met all conditions. This is a strategy-evidence failure, not a download or Python crash.

## Training-only evidence (RR 1.0, base assumed spread)

| Candidate | Trades | Wins | Losses | Win rate | Net R | Profitable pairs |
|---|---:|---:|---:|---:|---:|---:|
| Baseline | 396 | 187 | 209 | 47.22% | -23.63R | 2/7 |
| Buy only | 152 | 77 | 75 | 50.66% | +4.88R | 3/7 |
| Sell only | 244 | 110 | 134 | 45.08% | -29.42R | 4/7 |
| High-conviction confluence | 150 | 66 | 84 | 44.00% | -18.20R | 2/7 |
| Combined quality | 179 | 74 | 105 | 41.34% | -22.78R | 1/7 |

The buy-only aggregate is not a robust edge. Its pair breakdown is:

| Pair | Trades | Wins | Losses | Net R |
|---|---:|---:|---:|---:|
| EURUSD | 38 | 17 | 21 | -4.39R |
| GBPUSD | 22 | 13 | 9 | +3.79R |
| USDJPY | 86 | 43 | 43 | -0.72R |
| USDCHF | 2 | 2 | 0 | +1.99R |
| USDCAD | 3 | 2 | 1 | +5.22R |
| AUDUSD | 1 | 0 | 1 | -1.01R |
| NZDUSD | 0 | 0 | 0 | 0.00R |

Most of the apparent gain outside GBPUSD comes from tiny samples (USDCHF: 2 trades; USDCAD: 3 trades). EURUSD is negative, and USDJPY supplies 86 trades with slightly negative net R. Buy-only must not be promoted as the Bot 3 strategy.

At RR 1.0, buy-only remained positive in aggregate at 1.5x assumed spread (+4.83R), but trade count fell from 152 to 45. This is a material sensitivity warning, not validation.

## Next safe research step

Do not add more arbitrary thresholds or relax the acceptance gate. The next iteration should add a **training-only trade-path diagnostic** to the backtest output, without changing entries, exits, or candidate rules:

1. Record maximum favourable excursion (MFE) and maximum adverse excursion (MAE), in R and ATR, from executable fill until exit/censoring.
2. Record whether the stop is hit before price reaches +0.5R, +0.75R, and +1.0R; preserve same-bar ambiguity rather than assuming an intrabar path.
3. Record confirmation-candle range and close extension relative to ATR, plus swing-stop distance relative to ATR.
4. Report the diagnostics by pair and direction, and compare training segments chronologically.
5. Form only a small number of hypotheses from training. Freeze them before a fresh validation check. If no candidate passes, stop; keep final-test data sealed.

## Decision

**Bot 3 remains not validated and not ready for live release.** The current evidence does not support a 70% win-rate claim, nor does it justify enabling buy-only.