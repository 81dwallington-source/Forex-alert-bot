# Forex Bot 3 — independent build

Bot 3 is a separate signal-only M5 system. It can alert only on the three explicitly approved chart structures below. It does not import, edit, or share state with Bot 1 or Bot 2.

## Rules
- H1 establishes direction; M15 must independently agree. Disagreement means no trade.
- M5 must match one of exactly three structures: (1) confirmed swing liquidity sweep, rejection back through the level and close-break; (2) strong impulse followed by a retest of the last opposing origin candle and confirmation close-break; or (3) compact consolidation, completed breakout, boundary retest and confirmation close-break. No EMA-only or generic pullback signal is permitted.
- H1 trend alignment is mandatory and M15 must independently agree. M5 patterns cannot override either higher timeframe.
- Entry, stop and target are calculated from the bot’s own completed candles and confirmed M5 swing structure; manually drawn screenshot levels are ignored. Stop includes an ATR buffer; target is at least 1R.
- Reject stale M5 candles, oversized confirmation candles, distant entries, and stops wider than 3.5 ATR.
- UK session 05:00–23:00; maximum 15 alerts per UK day and one alert per pair per day.
- Signal-only; no automatic execution. Risk advisory is 0.25% maximum, but broker-specific position sizing is not enforced.

## Data limitations
The feed is the BiQuote public OHLC endpoint. It is third-party and not broker-verified. Spread and execution quality are not independently known. Verify every alert against MT4/MT5 bid/ask. This is not a proven profitable system.

## Files
- bot3/bot3.py: independent scanner
- bot3/test_bot3.py: offline regression tests
- .github/workflows/forex-bot-3.yml: separate scheduled/manual workflow
- bot3_state.json and bot3_signal_log.csv: isolated Bot 3 state and ledger

The scheduled workflow performs twelve five-minute scans during each hourly weekday worker. GitHub Actions timing is best-effort, not guaranteed real-time.
