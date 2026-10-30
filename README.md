# Forex Alert Bot

## Current behaviour
- Signal-only Telegram alerts; the bot does not place trades.
- Scans 27 major and cross-currency pairs using completed M5 candles, with M15 and H1 trend confirmation.
- Uses completed OANDA v20 midpoint candles for M5/M15/H1. Yahoo Finance is not used as a fallback for trade alerts.
- Requires the `OANDA_API_TOKEN` GitHub Actions secret. If it is missing, the bot sends a pause notice and sends no trade alerts.
- Rejects stale/incomplete candles and requires a recent completed M5 EMA 9/21 crossover.
- Detects confirmed H1 swing highs/lows using two closed candles on each side; rejects setups when the planned target is too close to the opposing H1 swing level.
- Filters for RSI, ATR/volatility, aligned and sufficiently separated higher-timeframe trends, entry extension, and signal-candle direction.
- Stop-loss uses the wider of 1.5 ATR or a recent six-candle M5 swing high/low plus a 0.15 ATR buffer; setups needing more than 3 ATR of stop distance are rejected.
- Minimum planned reward-to-risk is 1:1.5; target distance scales with the calculated stop distance.
- Limits alerts to **5 per UTC day** and at most **1 alert per pair per UTC day**.
- Logs alerts and estimates later outcomes from Yahoo Finance M5 candles. These estimates are not broker-verified.

## Important limitations and safety
- OANDA midpoint candles are broker-sourced market data, but are not your executed MT4/FTMO fills. Prices, spreads, slippage, commissions and outcome labels can still differ.
- An alert is a rule-based candidate, not a prediction or guarantee of a winning trade. A score of 6/6 is a filter score, not a 100% probability or measured win rate.
- If stop and target are both touched in one candle, the result is marked ambiguous rather than guessed.
- The daily alert cap is not a daily-loss monitor and cannot enforce FTMO drawdown limits; the bot does not know account equity, executed trades, commissions, or live broker P/L.
- Wider stops require smaller position sizes for the same risk limit. Never keep the same lot size after the stop widens. The alert recommends risk at or below 0.25% of current equity, but cannot calculate broker-specific lot size.
- Before considering an alert, verify the live broker price and spread. Skip it if the price has moved more than 0.20 ATR from the reference or the signal is older than 10 minutes.
- No automatic execution is enabled.

## GitHub Actions secrets
Configure `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, and `OANDA_API_TOKEN` under repository Settings → Secrets and variables → Actions. Create an OANDA API personal access token in the OANDA account you intend to use. Set the repository Actions variable `OANDA_ENVIRONMENT` to `live` for a live OANDA account or leave it unset for `practice`. Do not paste the token into chat or commit it to the repository. The bot uses OANDA midpoint candles; it does not verify your MT4 execution price, spread, fills, or commissions.

## Running and validation
The workflow runs offline regression tests with `python -m unittest -v test_bot.py`, then runs `python Forex_alert.py` using the OANDA API token. It is scheduled every five minutes on weekdays, and can also be started manually.
