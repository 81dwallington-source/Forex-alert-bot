# Bot 1 — H1 Pullback + M5 Mini Break of Structure

## Agreed entry sequence
1. Find a recent completed H1 bullish or bearish impulse candle that closes beyond the preceding five-candle structure.
2. Wait for a completed H1 pullback into the 38.2%–70.5% retracement area. The impulse extreme must remain intact.
3. On M5, wait for a completed candle to close through the latest confirmed local swing in the H1 direction.
4. Send the alert after that M5 candle closes. No additional retest is required.

BUY is the bullish inverse of SELL. The scanner covers 28 major and cross-currency pairs. No EMA, RSI, ADX or indicator filters are used.

## Alert fields
M5 close entry, structural M5 swing stop with a volatility buffer, and a 1.5R target. Signal alerts only; no automatic orders. Duplicate alerts for the same pair and M5 candle are suppressed.

## Operation
- GitHub Actions schedule: every five minutes, all days; manual run is available.
- Requires repository Actions secrets TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.
- Scanner state and signal log are committed after each scan.
- Add bot1/STOP to pause the scanner.

GitHub Actions scheduling is best-effort, not guaranteed exact five-minute delivery. BiQuote public prices are not broker-verified and can differ from broker bid/ask, spread and execution. Check the broker quote before acting. Successful alert delivery proves the pipeline, not strategy profitability; forward-test before risking money.
