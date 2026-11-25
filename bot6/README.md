# Bot 6 — Impulse Origin + Retest

![Bot 6 entry reference](entry_reference.svg)

The strategy follows the user's GBP/USD chart example. A strong directional displacement breaks nearby M15 structure; Bot 6 marks the last opposing candle immediately before that displacement as the origin zone. It waits for price to revisit that exact candle zone, then requires M15 continuation and a separate M5 rejection/continuation candle before a signal. BUY is the exact inverse of SELL.

## Required entry sequence
1. H1 trend direction is mandatory: BUY only when H1 close is above EMA200 and EMA21 is above EMA50; SELL only when H1 close is below EMA200 and EMA21 is below EMA50. If H1 is unclear, no trade.
2. On M15, require a decisive displacement candle that closes through the previous five-candle structure in the H1 direction.
3. Locate the final opposing candle in the five candles immediately before the displacement. For SELL, this is the last bullish candle and its open-to-high area is the origin zone. For BUY, this is the last bearish candle and its low-to-open area is the origin zone.
4. Wait for a later completed M15 candle to revisit and hold the origin zone. A touch alone is not an entry.
5. Require M15 continuation in the H1 direction after the retest.
6. Require M5 to revisit the same origin zone and then print a separate rejection/continuation candle: close in the trade direction, break the prior M5 candle extreme, and close beyond the origin-zone boundary and EMA21.
7. Only then calculate entry, stop and target and consider a Telegram alert. Never enter on the initial displacement or merely on a zone touch.

## Risk and filters
- Scans 27 major and cross pairs.
- H1 direction is mandatory; M15 defines the structure break and retest; M5 provides the execution confirmation.
- Stop loss is beyond the latest confirmed M5 swing and beyond the origin-zone boundary, with a spread/volatility buffer.
- Rejects unusually small or large structural risk (below 0.5 M5 ATR or above 4 M15 ATR).
- Planned target is 1:1.5 reward-to-risk.
- Maximum five alerts per UTC day; one alert per pair per UTC day.
- Maximum one pending JPY-cross trade.
- Requires fresh completed M5/M15/H1 candles and a fresh live quote close to the confirmed M5 entry.
- Does not chase an entry after price has moved away.
- Every alert is logged; outcomes are resolved from later completed M5 candles. If SL and TP both touch in one candle, outcome is marked AMBIGUOUS rather than guessed. Unresolved trades expire after 24 hours.

## Data and safety
- Feed: BiQuote public FX OHLC plus its live quote endpoint; not broker-verified.
- Manual signals only. Check the broker's live price and spread before entry.
- Existing trade log and state files are retained; historical results are not reset.
- The emergency STOP kill-switch can be restored by adding `bot6/STOP`; do not do this unless you intentionally want to pause all Bot 6 scans.

## Current operating status
The live scanner is configured in `.github/workflows/bot6-15m.yml` to run every five minutes on weekdays and during the Sunday forex reopen window (UTC), with a manual run option. GitHub scheduling is best-effort, not guaranteed real-time. The workflow requires Actions secrets `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`, and persists `state.json`, `signals.csv` and evidence after each run.

## Historical validation
A separate walk-forward backtest is provided by `backtest.py` and `.github/workflows/bot6-historical-validation.yml`. This strategy change must be backtested on fresh historical data before its performance is assessed. A screening test is not proof of profitability. Public historical/live-feed outcomes may differ from executable broker prices. Use paper trading first.
