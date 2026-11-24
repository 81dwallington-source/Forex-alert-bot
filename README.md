# Forex Alert Bot — Best Bot live scanner

The default branch contains multiple signal-only workflows, including **Best Bot** and **Bot 6**. Bot 6's separate scanner is documented in `bot6/README.md`.

## Live operation
- GitHub Actions scans every 5 minutes on weekdays and during the Sunday forex reopen window (UTC). GitHub scheduling is best-effort, not a guaranteed real-time service.
- Scans 27 major and cross-currency pairs.
- Uses completed M5 candles for entries; M15 and H1 must independently confirm the same direction.
- Strategy: only the three approved M5 pattern families can alert: (1) two-sided consolidation breakout: both boundaries of the same compact M5 range must be decisively broken, followed by a return to the exact origin candle of the final directional displacement and completed confirmation; (2) video-based liquidity sweep of one established range edge, close back inside, decisive reversal displacement, retest/rejection of the swept boundary and completed-candle confirmation, targeting the opposite range edge only when it still offers at least 1.5R; (3) impulse plus origin-candle retest. The liquidity sweep alone never triggers an alert. M15 and H1 must agree on direction; H1 alignment is mandatory. Entries use completed M5 candles. For the impulse-continuation pattern, the origin is the last opposing candle immediately before the strong 3–7 candle displacement leg; Telegram sends a one-time APPROACHING ORIGIN CANDLE pre-alert within 0.75 ATR, then an entry alert only after price retests that exact body and a completed M5 rejection confirms. A decisive break beyond the origin wick sends an INVALIDATED notice. Pre-alerts are informational and do not count toward the daily trade cap.
- Stops sit beyond the latest confirmed M5 swing with an ATR buffer. Rejects stops under 8 pips or above 3 ATR. Target is 1.5R.
- Maximum 5 new alerts per UTC day and one per pair per UTC day. No watchlist alerts and no automatic trading.
- Every signal is logged in `best_bot/live_signals.csv`; pending outcomes are tracked in `best_bot/live_state.json`. Public-candle TP/SL outcomes are estimates, not broker-verified executions.

## Telegram screenshot review
- The runner checks the configured Telegram chat for incoming photos between five-minute market scans.
- Add a repository Actions secret named `OPENAI_API_KEY` to enable image analysis. Existing `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` secrets are also required.
- Send a TradingView screenshot as a photo to the same private Telegram chat used for Best Bot alerts. The bot replies with a visual classification against the three approved setup families.
- Screenshots are visual references only. They do not change the strategy, generate live BUY/SELL signals, or supply entry/SL/TP levels. M5 screenshots cannot prove M15/H1 alignment.
- Image analysis uses the OpenAI API and may incur API charges. The API key must be kept in GitHub Actions secrets and never placed in source code.
- Telegram updates are processed only from the configured chat. The update offset is saved in `best_bot/telegram_update_offset.json`.

## Visual pattern reference — USDJPY M5 (15 November 2026)
- Added the user's USDJPY 5-minute chart as a bearish liquidity-sweep example: a compact consolidation near the local high, a brief push/wick above the range high, a sharp bearish rejection/displacement back below the range, then continued downside.
- The screenshot is a visual teaching reference only. Its displayed prices, manually drawn box, timestamps and chart levels are not fixed bot inputs. The bot must calculate all levels from its own live M5 candles and still pass M15/H1 direction alignment, freshness, stop-distance, reward/risk and live-price-drift checks.
- Never alert on the sweep candle or displacement alone; if the retest and confirmation do not occur, there is no trade.

## Telegram
Repository Actions secrets must be named `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. The live scanner sends signal-only alerts. Never share bot tokens or API keys in chat or commit them to the repository.

## Data and execution warning
The scanner currently uses the public BiQuote OHLC endpoint. Its prices are **not broker-verified** and may differ from MT4 bid/ask prices, spread, slippage and execution. Check every alert against your broker before entry; skip it if the broker price has moved more than 0.20 ATR from the reference or the alert is older than 10 minutes. Risk no more than 0.25% per trade. The bot cannot size positions or enforce account risk.

## Performance status
A successful live scan and Telegram delivery prove the code path is operating; they do **not** prove the strategy is profitable. No 60% win rate is claimed. The historical backtest is a separate research gate and must pass an adequately sized untouched out-of-sample sample with realistic broker-specific costs before any performance claim. The current live feed is not broker history.

## Files
- `best_bot/strategy.py` — entry, confirmation and structural stop rules.
- `best_bot/live_bot.py` — live scanner, Telegram delivery and outcome ledger.
- `best_bot/photo_review.py` — Telegram screenshot receiver and visual chart reviewer.
- `.github/workflows/best-bot-live.yml` — scheduled scan and photo listener.
