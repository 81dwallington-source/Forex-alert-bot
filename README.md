# Forex Alert Bot — Best Bot live scanner

The default branch now runs one active signal-only scanner: **Best Bot**.

## Live operation
- GitHub Actions scans every 5 minutes on weekdays and during the Sunday forex reopen window (UTC). GitHub scheduling is best-effort, not a guaranteed real-time service.
- Scans 27 major and cross-currency pairs.
- Uses completed M5 candles for entries; M15 and H1 must independently confirm the same direction.
- Strategy: M5 consolidation must sweep BOTH range edges (each break closes back inside), then make decisive displacement through one edge, then retest/reject the candle body that launched that move. No one-sided breakout or generic impulse/trend-break fallback alerts. M15 and H1 must agree on direction; entries use completed M5 candles.
- Stops sit beyond the latest confirmed M5 swing (two candles on each side to confirm) with an ATR buffer. Rejects stops under 4 pips or above 2.5 ATR. Target is 1.5R.
- Maximum 5 new alerts per UTC day and one per pair per UTC day. No watchlist alerts and no automatic trading.
- Every signal is logged in `best_bot/live_signals.csv`; pending outcomes are tracked in `best_bot/live_state.json`. Public-candle TP/SL outcomes are estimates, not broker-verified executions.

## Telegram
Repository Actions secrets must be named `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. A one-off Telegram test succeeded on 11 November 2026. The live scanner's first run also successfully sent a qualifying EURJPY BUY alert. [View that successful scan](https://github.com/81dwallington-source/Forex-alert-bot/actions/runs/43068374733).

## Data and execution warning
The scanner currently uses the public BiQuote OHLC endpoint. Its prices are **not broker-verified** and may differ from MT4 bid/ask prices, spread, slippage and execution. Check every alert against your broker before entry; skip it if the broker price has moved more than 0.20 ATR from the reference or the alert is older than 10 minutes. Risk no more than 0.25% per trade. The bot cannot size positions or enforce account risk.

## Performance status
A successful live scan and Telegram delivery prove the code path is operating; they do **not** prove the strategy is profitable. No 60% win rate is claimed. The historical backtest in `best_bot/backtest.py` is a separate research gate and must pass an adequately sized untouched out-of-sample sample with realistic broker-specific costs before any performance claim. The current live feed is not broker history.

## Files
- `best_bot/strategy.py` — entry, confirmation and structural stop rules.
- `best_bot/live_bot.py` — live scanner, Telegram delivery and outcome ledger.
- `.github/workflows/best-bot-live.yml` — 5-minute schedule and manual trigger.
- `best_bot/backtest.py` — historical research simulator; not a live signal source.

Old Bot 3 historical workflow has been retired from the default branch. Historical source files and past logs are retained for audit; they are not scheduled as active bots.
