# Bot 5 — single-pattern consolidation scanner

The live signal-only scanner is `live_bot.py`, run by `.github/workflows/best-bot-live.yml`.

## The only permitted setup (M5)

**SELL:** identify a compact established consolidation range; price must sweep above the range high and close back below it; then a decisive bearish candle must close below the opposite range low. Identify the **last bullish candle after the high sweep and before the bearish push**. Wait for price to return to that exact candle's body. Alert SELL only after a completed bearish rejection candle touches that body and closes below it.

**BUY:** exact inverse — sweep below the range low, then decisively break above the range high; identify the last bearish candle before the bullish push; wait for a retest of that candle's body and a completed bullish rejection close.

No entry on either breakout. No alert if the sequence is incomplete, ambiguous, stale, too extended, or invalid.

## Filters and risk
- M5 completed candles define the pattern and entry confirmation.
- M15 and H1 trend must agree with direction.
- Entry, structural stop and 1.5R target are calculated from current market data.
- Stop is beyond the sweep extreme / origin structure plus a volatility buffer; invalid stop distances are rejected.
- Maximum entry drift remains 0.20 ATR.
- Public BiQuote OHLC is not broker-verified; confirm broker price and spread before manual entry. Risk ceiling 0.25%.
- Signal-only; no auto-trading, watchlists or 1-minute entries.

## Operation and records
- 27 major/cross pairs; five-minute scans (GitHub schedule starts are best-effort).
- Maximum five alerts per UTC day and one per pair per UTC day.
- Every alert is recorded in `live_signals.csv`; pending and resolved outcomes are stored in `live_state.json`.
- Outcomes use later completed public-feed M5 candles; same-candle SL/TP ambiguity is not guessed.
