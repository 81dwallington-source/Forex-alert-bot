# Forex Alert Bot

## Current behaviour
- Signal-only Telegram alerts; no automatic trading. Telegram is reserved for qualifying trade alerts and outcome-tracking notices; routine scan summaries remain in GitHub Actions logs, and no watchlist/candidate alerts are sent.
- Scans 27 major and cross-currency pairs using completed M5 candles, with M15 and H1 trend confirmation.
- Uses a third-party public biquote OHLC endpoint. The provider's underlying market-data provenance is not independently verified; it is **not broker-verified** and may be delayed, unavailable, rate-limited, or differ from MT4 broker prices.
- Rejects stale/incomplete candles and fails closed if the public feed returns missing, malformed, or insufficient candle data.
- Requires aligned M15/H1 trends, fresh M5 continuation or pullback confirmation, and filters for RSI, ATR/volatility, EMA separation, entry extension, and candle direction.
- Detects confirmed H1 swing highs/lows and rejects setups when the target is too close to the opposing H1 swing.
- Uses structure/ATR stops, a minimum planned reward-to-risk of 1.5:1, and limits alerts to 5 per UTC day and at most 1 per pair per UTC day.
- Logs alerts and estimates later outcomes from biquote public-feed M5 candles. These are candle-based estimates, not executed-trade results.

## Important limitations and safety
- Public-feed prices are not your broker's bid/ask or execution prices. Spreads, slippage, commissions, broker candle differences and feed delays can change whether a setup is tradable.
- Check every alert against MT4 before entry. Skip it if the broker price has moved more than 0.20 ATR from the reference or the signal is older than 10 minutes.
- An alert is a rule-based candidate, not a prediction or guarantee. A score of 6/6 is a filter score, not a win probability.
- If stop and target are both touched in one candle, the result is marked ambiguous rather than guessed.
- The bot cannot read account equity, executed trades, commissions, broker pip values, or enforce position size or drawdown limits.
- No automatic execution is enabled.

## GitHub Actions secrets
Configure `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` under repository Settings → Secrets and variables → Actions. No market-data API key is required for this public feed. Never commit bot credentials.

## Running and validation
The workflow runs offline regression tests with `python -m unittest -v test_bot.py`, then runs `python Forex_alert.py`. It is scheduled every five minutes on weekdays and around the Sunday forex reopen (UTC coverage for both UK daylight-saving and standard time), and can also be started manually. GitHub Actions cron is best-effort, so start times can be delayed or skipped during platform load; it is not a guaranteed real-time scheduler.


## Performance reporting
- Each scan reports public-candle estimate TP/SL counts, estimated hit rate, average/expectancy in R, maximum drawdown in R, pending and ambiguous counts.
- These estimates are not broker fills, verified trade outcomes, or proof of profitability. Ambiguous candles are excluded.
- Broker-verified records are counted only when explicitly labelled VERIFIED_WIN, VERIFIED_LOSS, or VERIFIED_BREAK_EVEN. The bot does not infer these labels from public candles.


## Bot 3 historical backtesting

A conservative historical tester is available as `backtest_bot.py`. It calls the live
Bot 3 `analyse_pair()` rules against rolling historical candles; it does not send
Telegram messages or download market data.

Export **UTC** candle-open timestamps and OHLC columns from MT5 (or another trusted
source) into these files under `historical_data/`:

- `EURUSD_M5.csv`, `EURUSD_M15.csv`, `EURUSD_H1.csv`
- Repeat for each pair being tested (for example `GBPUSD_M5.csv`).

Run:

```bash
python -m pip install -r requirements.txt
python backtest_bot.py --data-dir historical_data --pairs EUR/USD,GBP/USD,USD/JPY --spread-pips 1.2
```

The runner uses the next M5 candle open for entry, applies an assumed constant
spread adversely on entry and exit, counts same-candle SL/TP collisions as losses,
and reports the chronological first 70% separately from the final 30%.
It also writes `backtest_trades.csv`.

**Important limitations:** results are historical simulations, not broker fills.
The default 1.2-pip spread is only an assumption and should be replaced with
broker/session-specific values. Commissions, swaps, variable spreads, latency,
and slippage beyond the configured spread are not modelled. The report is not
a 70% win-rate claim. Use enough history across different market conditions and
keep the final out-of-sample period untouched while refining rules. The public
biquote live feed currently provides only a short rolling candle window, which
is not enough by itself to establish a reliable multi-month win rate.
