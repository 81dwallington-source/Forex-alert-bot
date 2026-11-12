# Best Bot — live scanner and research

The active live signal bot is `live_bot.py`, run by `.github/workflows/best-bot-live.yml`.

## Live strategy
- M5 entry on completed candles only.
- M15 and H1 must both agree using EMA21/EMA50 alignment and EMA21 slope.
- M5 must independently agree: price above EMA21 > EMA50 > EMA200 for BUY, or below EMA21 < EMA50 < EMA200 for SELL; the EMA slopes must also agree.
- Requires a confirmed M5 swing-structure progression as well as a recent EMA21 pullback and a bullish/bearish close beyond the previous M5 candle extreme.
- Stop beyond the latest confirmed M5 swing, plus a volatility buffer.
- Reject stop distances below 8 pips (or 0.65 ATR if larger) and above 3 ATR; target 1.5R.
- 27 major/cross pairs; maximum five alerts per UTC day and one per pair per day.
- Signal-only. No automatic execution, no watchlists, no 1-minute entries.

## Operation
The GitHub Actions workflow scans every five minutes on weekdays and during the Sunday reopen window. Actions scheduling is best-effort and can be delayed. It uses repository secrets `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. Every alert is written to `live_signals.csv`; state and pending outcome tracking are in `live_state.json`.

The scanner uses BiQuote public OHLC data, not broker-verified prices. Check MT4 spread and price before acting. Skip an alert if the live broker price has moved more than 0.20 ATR or the signal is older than 10 minutes. Suggested risk ceiling is 0.25% per trade.

## Research and performance
`backtest.py` evaluates historical Dukascopy candles with an explicit assumed spread, next-M5-open entries and stop-first same-candle handling. It separates the first 70% development period from the final 30% untouched holdout and requires at least 30 holdout trades. It does not establish future profitability. No 60% win-rate claim is made until independently demonstrated on a sufficiently large, realistic out-of-sample sample with broker-specific costs.

## Files
- `strategy.py` — strategy logic.
- `live_bot.py` — live data scan, Telegram alerts, signal/outcome ledger.
- `test_strategy.py` and `test_live_bot.py` — regression tests, both executed by pytest before each scheduled live scan.
- `backtest.py` — historical research only.
