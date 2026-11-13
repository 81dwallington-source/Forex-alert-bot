# Best Bot — three-pattern live scanner

The active signal-only scanner is `live_bot.py`, run by `.github/workflows/best-bot-live.yml`.

## Mandatory chart patterns

Every alert must be classified as exactly one of these completed-candle M5 patterns. Indicator alignment alone is never a setup and must never create a signal.

1. **Two-sided liquidity sweep + origin-candle retest** — price takes both edges of a compact range and closes back inside after each sweep; a decisive displacement candle breaks out; price returns to the last opposing candle's body and rejects it.
2. **Strong impulse + origin-candle retest** — a clear multi-candle directional impulse moves at least 1.5 ATR from its launch candle, with meaningful directional candle bodies and a close near the impulse extreme; price later returns to the launch candle body and rejects it.
3. **Trend + consolidation + breakout + retest** — M5 EMA21/50/200 stack and EMA21/50 slope agree; a compact consolidation breaks with a decisive candle; price retests the broken range edge and closes back in the breakout direction.

All three patterns have BUY and SELL versions. The bot calculates its own entry, structural stop and 1.5R target. If the pattern is incomplete, unclear, unclassified, stale, too extended, or requires an invalid stop distance, it rejects the trade. No generic EMA-only fallback is permitted.

## Confirmation and risk controls

- M5 entries use completed candles only.
- M15 and H1 trend must agree; neither timeframe can override the other.
- M5 pattern is mandatory. M5 EMA alignment by itself is not a trigger.
- Stops remain beyond the relevant sweep/origin structure plus a volatility buffer. Stops that are too tight or too wide are rejected rather than squeezed.
- Maximum entry drift is 0.20 ATR from the signal close.
- Public BiQuote OHLC data is not broker-verified. Confirm broker price and spread before any manual trade; suggested risk ceiling is 0.25%.
- Signal-only: no automatic execution, no watchlists, no 1-minute entries.

## Operation and records

- 27 major/cross pairs.
- Each runner scans on five-minute intervals; GitHub scheduled starts are best-effort and can be delayed.
- Maximum five alerts per UTC day and one per pair per UTC day.
- Every alert must display its exact recognised pattern and origin candle body.
- Every signal is written to `live_signals.csv`; pending and resolved outcomes are stored in `live_state.json`.
- Outcome tracking uses later completed public-feed M5 candles. If SL and TP are touched in the same candle, the result is marked ambiguous rather than guessed.

## Research

`backtest.py` evaluates historical Dukascopy candles with an explicit assumed spread, next-M5-open entries and stop-first same-candle handling. It separates development data from an untouched holdout. Historical results do not establish future profitability; no win-rate claim is made without a sufficiently large, realistic out-of-sample sample using broker-specific costs.

## Files

- `strategy.py` — three pattern detectors and signal construction.
- `live_bot.py` — live data scan, Telegram alerts, signal/outcome ledger.
- `test_strategy.py` and `test_live_bot.py` — regression tests run before each scheduled scan.
- `backtest.py` — historical research only.
