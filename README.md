# Forex Alert Bot

## Current behaviour
- Signal-only alerts to Telegram; it does not place trades.
- Scans 27 major and cross-currency pairs on M5, with M15 and H1 trend confirmation.
- Uses completed candles and rejects stale or incomplete data.
- Filters for a recent M5 EMA 9/21 crossover, RSI, ATR/volatility, aligned higher-timeframe trend, weak/choppy trend separation, entry extension, and signal-candle direction.
- Stop-loss uses the wider of 1.5 ATR or a recent six-candle M5 swing high/low plus a 0.15 ATR buffer; setups needing more than 3 ATR of stop distance are rejected.
- Minimum planned reward-to-risk is 1:1.5; targets scale with the actual stop distance.
- Limits alerts to 3 per UTC day and at most 1 alert per pair per UTC day.
- Logs signals and attempts to label outcomes from later Yahoo Finance M5 candles.

## Important limitations
- Market data is from Yahoo Finance, not a broker feed. Prices, spreads, fills, and outcome labels may differ from MT4/FTMO.
- Outcome labels based on Yahoo candles are explicitly unverified. If stop and target are both touched in one candle, the result is marked ambiguous rather than guessed.
- The daily alert cap is not a daily-loss monitor and cannot enforce FTMO account drawdown limits because the bot does not know account equity, executed trades, commissions, or live broker P/L.
- Wider stops mean smaller position sizes for the same account-risk limit; never keep the same lot size after the stop widens.
- Do not treat a signal as a guaranteed trade or a win-rate promise. Check live broker price and spread before considering any trade.
- No automatic execution is enabled.

## GitHub Actions secrets
Configure `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` under repository Settings → Secrets and variables → Actions.

## Validation
GitHub Actions runs the offline regression tests before each scheduled/manual scan.
