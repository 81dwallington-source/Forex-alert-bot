# Bot 7 — Trend Pullback Continuation (Paper Forward Test)

Bot 7 is a separate strategy, not a patch to Bot 6. It is signal-only and starts in **PAPER TEST** mode.

## Setup rules
- M5 execution; H1 and M15 are checked independently and must both show the same EMA21/50/200 directional stack. If they disagree or either is mixed, reject the setup.
- M15 ADX must be at least 20 to avoid weak/choppy conditions.
- Wait for price to pull back into the M5 EMA21 area, then require a completed M5 candle to reclaim EMA21 and break the previous candle high/low with a decisive body.
- RSI must be in a directional but not extreme band.
- Entry is the completed confirmation candle close; a fresh live quote must remain within the tighter of 2 pips or 0.15 ATR. No chasing.
- Stop is beyond a confirmed M5 swing with a volatility buffer. Reject risk below 0.75 ATR or above 2.2 ATR.
- Target is 1.5R; the risk policy is no more than 0.25% of account equity per trade. Bot 7 does not calculate lot size or execute trades.
- Maximum three paper alerts per UTC day; one pending signal per pair.
- Logs every paper signal and grades it from subsequent completed M5 bars. If SL and TP both touch in one candle, outcome is AMBIGUOUS, never guessed.

## Data and limitations
Uses BiQuote public OHLC and live tick endpoint, not a broker-verified execution feed. There is no automatic economic-calendar/news filter in this version, so major-news periods must be avoided manually. Backtest results have not yet been established. Telegram messages are labelled PAPER ONLY and must not be traded with real money. This is a forward-test framework, not a promise of profitability.

## Promotion criteria
Do not switch to live alerts until there is a sufficiently large sample (minimum 100 resolved paper trades), positive expectancy after conservative spread/slippage assumptions, and separate out-of-sample review. Report WIN/LOSS/AMBIGUOUS/EXPIRED honestly.
