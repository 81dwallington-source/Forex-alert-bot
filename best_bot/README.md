# Bot 5 — single-pattern impulse-origin retest scanner

The live signal-only scanner is `live_bot.py`, run by `.github/workflows/best-bot-live.yml`.

## The only permitted setup (M5)

**BUY:** identify the last bearish candle immediately before a strong bullish displacement leg (3–7 candles). Require at least three bullish candles, meaningful cumulative bullish body and range expansion. Then wait for a deep pullback (50–85% of the impulse range) into the exact origin candle body. Alert only after a completed bullish rejection candle touches that body and closes above it and above the prior candle close.

**SELL:** exact inverse — last bullish candle immediately before a strong bearish displacement leg; wait for a 50–85% pullback into that exact candle body; alert only after a completed bearish rejection closes below the body and prior candle close.

No entry on the impulse itself. No broad substitute zone, early touch alert, or alternate setup family. Reject incomplete, ambiguous, stale, overextended or invalid patterns.

## Filters and risk
- M5 completed candles define the 3–7 candle displacement leg, the immediately preceding opposing origin candle, the 50–85% pullback and the confirmed retest of that exact candle body.
- Fresh completed M15 and H1 trend must both agree with the trade direction. If either is unclear or they disagree, reject the trade.
- Entry is the near edge of the exact origin candle body: BUY at its upper body edge; SELL at its lower body edge.
- BUY stop is below the latest confirmed M5 swing low; SELL stop is above the latest confirmed M5 swing high, each with a volatility buffer.
- Keep the stop beyond the latest confirmed M5 swing plus a volatility buffer. Reject, never tighten, any structural stop under 8 pips or under 0.65 ATR, or wider than 3 ATR. Take profit is at least 1:1.5 from the structural stop.
- Maximum entry drift remains 0.20 ATR.
- Public BiQuote OHLC is not broker-verified; confirm broker price and spread before manual entry. Risk ceiling 0.25%.
- Signal-only; no auto-trading, watchlists or 1-minute entries.

## Operation and records
- 27 major/cross pairs; scheduled five-minute scans (GitHub schedule starts are best-effort).
- Maximum five alerts per UTC day and one per pair per UTC day.
- Every alert is recorded in `live_signals.csv`; pending and resolved outcomes are stored in `live_state.json`.
- Outcomes use later completed public-feed M5 candles; same-candle SL/TP ambiguity is not guessed.
