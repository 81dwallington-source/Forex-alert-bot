# Fresh Best Bot — research build

This is an independent clean-room strategy branch. It is **not live and does not send Telegram alerts** yet.

## Rules
- M5 entry; M15 and H1 must independently agree on EMA 21/50/200 trend.
- Recent EMA21 pullback followed by a directional close beyond the previous M5 candle extreme.
- Stop beyond the last eight completed M5 candles' swing, with an ATR buffer.
- Reject stops below 4 pips or above 2.5 ATR; target at least 1.5R.
- One position at a time per pair in the historical simulation.
- No SMC, liquidity-sweep, or 1-minute logic.

## Research protocol
Use Dukascopy historical bid candles, keep broker spread assumptions explicit, enter at the next M5 open, and count same-bar stop/target collisions as losses. Split chronologically: first 70% development, final 30% untouched holdout. Require at least 30 holdout trades before reporting a holdout win rate. This is a research gate, not proof of future profitability.

## Run
From the repository root, run `python download_historical_data.py --years 1 --data-dir historical_data`, then `python best_bot/backtest.py --data-dir historical_data --spread-pips 1.2`. The GitHub Actions workflow can run this manually and upload the trade ledger.

## Data and deployment
Historical feed is not broker-verified. No live deployment or Telegram alerts until the data pipeline, out-of-sample results, and paper-forward testing pass. No performance result is claimed yet.
