# Bot 6 — Institutional-style liquidity sweep and FVG retest

Bot 6 has been changed from its compression-breakout strategy to a selective, institutional-style price-action proxy. It remains signal-only: it does not place broker orders.

## Required sequence
1. H1 and M15 trend alignment must agree; disagreement means no trade.
2. On M5, price must sweep a recent 20-candle high/low and close back through that level.
3. Within the next four completed M5 candles, require decisive displacement that breaks nearby structure (BOS).
4. Require a three-candle fair-value gap (FVG) created by the displacement.
5. Price must later retrace into that FVG and hold the relevant side.
6. A separate, latest completed M5 candle must confirm continuation before an alert is considered.

A liquidity sweep alone is never an entry. The bot detects patterns in public OHLC data; it cannot see bank orders, dealer flows or prove that a bank caused a move.

## Risk and filters
- Scans 27 major and cross pairs.
- H1 and M15 alignment is mandatory.
- Structural stop is beyond the sweep extreme with an ATR/spread-style buffer.
- Rejects risk smaller than 0.65 ATR or larger than 3.0 ATR.
- Planned target is 1:1.5 reward-to-risk.
- Maximum five alerts per UTC day; one alert per pair per UTC day.
- Maximum one pending JPY-cross trade.
- Requires fresh completed M5/M15/H1 candles and a fresh live quote close to the planned entry.
- Does not chase an entry after price has moved away.
- Every alert is logged; outcomes are resolved from later completed M5 candles. If SL and TP both touch in one candle, outcome is marked AMBIGUOUS rather than guessed. Unresolved trades expire after 24 hours.

## Data and safety
- Feed: BiQuote public FX OHLC plus its live quote endpoint; not broker-verified.
- Manual signals only. Check the broker's live price and spread before entry.
- The existing trade log and state files are retained; historical Bot 6 results are not reset.
- The STOP kill-switch file is intentionally retained during validation.

## Current operating status
The scheduled scanner remains paused while the replacement strategy is validated. The GitHub Actions workflow currently performs syntax validation only; it does not send Telegram alerts or execute trades.


## Historical validation added

A separate walk-forward backtest is provided by `backtest.py` and `.github/workflows/bot6-historical-validation.yml`. It downloads independent Dukascopy M5/H1 history for seven major pairs, builds completed M15 bars, uses only higher-timeframe candles that had closed at each M5 decision point, enters at the next M5 open, applies an assumed full spread, and treats same-candle stop/target touches conservatively as losses. Results are split chronologically: the final 30% of trades is held out from development. The workflow fails closed if fewer than 30 out-of-sample trades are available or out-of-sample expectancy is not positive.

Dukascopy is an independent historical feed, not the BiQuote live feed or the user's broker. The backtest is a screening test, not proof of profitability. The live STOP file remains in place regardless of a passing backtest; review forward paper results and broker execution differences before considering reactivation.
