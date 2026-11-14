# Bot 3 visual setup references

These are chart examples supplied for the approved two-sided consolidation breakout pattern. The screenshots are visual examples only; do not reuse their historical price levels.

## Mandatory two-sided consolidation breakout entry

1. Detect a clear consolidation range from Bot 3's own M5 market data.
2. Require aggressive, decisive breaks beyond BOTH boundaries of the same range. Either side may break first.
3. Reject weak, slow, marginal, or wick-only breaks. A single-sided break does not qualify.
4. Identify the exact origin candle that started the resulting directional move.
5. Wait for price to return to that exact origin candle. This is the entry area; do not substitute a broad zone.
6. Require confirmation at the origin-candle retest before alerting.
7. Calculate entry, stop-loss, and take-profit from current market data. Never copy levels from the screenshots.

No qualifying two-sided break, no origin-candle retest, or no confirmation = NO TRADE.

## Supplied visual examples

- AUD/USD: boxed consolidation followed by a two-sided range break.
- EUR/NZD: aggressive break below the range followed by a strong reclaim through the opposite boundary. The supplied screenshot is M15; Bot 3 execution remains M5.
- GBP/JPY: aggressive breaks above and below the same boxed range, followed by a bearish move.
- NZD/CAD (FXCM, M15): a clearly boxed range is marked in yellow. Price first pushes above the range high, then later breaks below the range low with a decisive bearish move. This is an additional visual example of a two-sided range break, not a standalone entry. Bot 3 must still wait for the final bearish move's exact origin candle to be retested and confirmed on its M5 execution data before alerting. Do not copy the screenshot's prices or use the M15 chart as the execution timeframe.
- NZD/JPY: additional chart example supplied for the two-sided breakout pattern. Treat it as a visual reference only; Bot 3 must independently detect both aggressive breaks and wait for the exact origin-candle retest and confirmation on M5.

## USD/CAD example (FXCM, M15)

The yellow box marks the consolidation range. Price first pushes above the upper boundary, then later sweeps/breaks below the lower boundary with a sharp bearish move. The later candles recover back into and above the range, so the chart is a useful visual example of a two-sided range break followed by a reclaim. It is not a trade signal by itself: Bot 3 must identify the final directional break on its own M5 data, locate that move's exact origin candle, and wait for the return to that candle plus confirmation before alerting. Do not copy the screenshot's price levels or execute from M15.

These references supplement the other approved Bot 3 setup rules; they do not replace them.
