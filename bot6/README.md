# Bot 6 — M5 compression breakout and retest

- Independent signal strategy, separate from Bot 5.
- Scans 27 major/cross pairs every five minutes using completed M5 candles.
- Directional filter: H1 and M15 must both agree using EMA21/50/200 alignment. If either disagrees or is unclear, no trade.
- Identifies a compact 12-candle M5 range, then requires a decisive breakout close at least 0.10 ATR beyond the range, with a strong body and close near the candle extreme.
- Does not enter on the breakout. Waits for a later retest of the broken range boundary, then a separate completed M5 confirmation candle that closes beyond the prior candle in the breakout direction.
- Structural stop beyond the recent M5 swing with an ATR/spread buffer; rejects stops below 0.65 ATR or above 2.5 ATR.
- Fixed planned target of at least 1:1.5. No forced signals and no stop squeezing.
- Maximum five alerts per UTC day and one alert per pair per UTC day.
- M5 completed candle must be no more than 5 minutes old at scan time.
- Before alerting, a fresh live quote must post-date the M5 close and remain within the tighter of 2 pips or 0.15 ATR of the planned entry; otherwise reject rather than chase.
- Only one pending JPY-cross trade at a time, preventing stacked AUDJPY/EURJPY-style JPY exposure.
- Logs every alert and resolves outcomes from subsequent completed M5 candles. If SL and TP are both touched in one candle, records AMBIGUOUS rather than guessing; expires unresolved trades after 24 hours.
- Uses public BiQuote FX OHLC, not broker-verified. Signal-only; no auto-trading. Check broker price and spread before entry.
