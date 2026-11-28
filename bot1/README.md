# Bot 1 — H1 Pullback + M5 Mini Break of Structure

## Entry sequence
1. Identify a recent completed H1 impulse candle that closes beyond the preceding five-candle structure.
2. Wait for a completed H1 pullback into the 38.2%–70.5% retracement area while the impulse extreme remains intact.
3. On M5, wait for a completed candle to close through a confirmed local swing in the H1 direction.
4. Send a signal after the M5 candle closes. BUY is the bullish inverse of SELL.

No EMA, RSI, ADX or other indicator filters. The scanner covers 28 major and cross-currency pairs.

## Alerts
M5 close entry, structural M5 swing stop with a volatility buffer, and a 1.5R target. Signal alerts only; no automatic orders. Duplicate alerts for the same pair and M5 candle are suppressed.

## Schedule and requirements
- GitHub Actions cron is configured for every five minutes (UTC).
- Each scheduled run performs one complete scan, rather than leaving a long-running six-hour worker.
- GitHub Actions scheduling is best-effort: GitHub can delay or drop scheduled starts, so this is not a guaranteed exact 5-minute 24/7 service.
- Requires repository Actions secrets TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.
- State and signal log are committed after each scan.
- Add bot1/STOP to pause scanning.

## Data and risk
BiQuote public OHLC and quote feeds are not broker-verified and can differ from broker bid/ask, spread and execution. Check the broker quote before acting. Alerts are not orders. Successful workflow execution and Telegram delivery do not prove profitability; forward-test before risking money.
