# Bot 6 — 15-minute pullback entries

- Separate scanner and records from the existing bots.
- Scans the same 27 major/cross pairs using completed 15-minute candles.
- Requires 15M EMA21/50/200 alignment and matching H1 trend (EMA21/50/200).
- Waits for a recent pullback into the EMA21/EMA50 area, then a completed rejection/continuation candle that closes beyond the prior candle.
- Structural stop beyond the recent confirmed 15M swing with an ATR buffer; rejects risk below 0.65 ATR or above 2.5 ATR rather than squeezing the stop.
- Target is 1.5R. Maximum five alerts per UTC day and one per pair per day.
- Sends to the existing TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID secrets and therefore the same Telegram bot/chat.
- Logs every signal and resolves WIN/LOSS only from later completed 15M candles. If SL and TP are both touched in one candle, marks AMBIGUOUS rather than guessing.
- Uses public BiQuote FX OHLC, not broker-verified. Signal-only; no auto-trading.
