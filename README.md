# Forex Alert Bot — Bot 1 only

This repository now contains one active signal bot.

## Strategy
- H1: identify a strong bullish or bearish impulse and wait for its pullback.
- M5: wait for a mini break of structure in the H1 direction.
- Trigger: alert only after the M5 candle closes beyond the confirmed swing.
- BUY and SELL logic are mirrored.
- 28 major and cross-currency FX pairs.
- No EMA, RSI, ADX or extra indicator filters.

## Operation
GitHub Actions runs the scanner on a five-minute schedule (best-effort) and can also be started manually. Telegram secrets required:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

The bot sends alerts only; it does not place trades. Every alert includes entry, structural stop loss and a 1.5R take profit. Duplicate signals are suppressed and signal history is stored in `bot1/signals.csv`.

Public BiQuote data is not broker-verified. GitHub Actions scheduling is not guaranteed to run at an exact five-minute interval. Validate alerts against the broker feed and forward-test before risking money.
