# Forex Bot 3 — independent build

Bot 3 is a separate signal-only M5 pullback-continuation system. It does not import, edit, or share state with Bot 1 or Bot 2.

## Rules
- H1 establishes direction; M15 must independently agree. Disagreement means no trade.
- M5 must show a recent EMA21 pullback, then a strong closed candle breaking the previous candle high/low in trend direction.
- Stop is beyond the latest confirmed M5 swing pivot with an ATR buffer; target is at least 1R.
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
