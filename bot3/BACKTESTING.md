# Bot 3 historical backtesting

This is a research tool only. It does not send Telegram alerts, change Bot 1/2, or activate a workflow.

## Input data
Prepare one CSV per currency pair with **M5** candles and these exact columns:

`time,Open,High,Low,Close`

- `time` must be the candle **open** timestamp in UTC (ISO 8601 recommended).
- Use completed, point-in-time historical candles from one consistent provider.
- Include several weeks at minimum; the script requires 800 bars for warm-up. A longer sample spanning different market conditions is preferable.
- Do not mix broker feeds or silently fill missing candles.

## Run
From the repository root:

```bash
python -m pip install -r bot3/requirements.txt
python -m bot3.backtest --pair EURUSD --csv data/EURUSD_M5.csv --spread-pips 1.2
```

Use a realistic **full bid/ask spread** for that pair and session. Repeat with a conservative wider spread (for example, 1.5x the typical spread) as a sensitivity check. JPY pairs use 0.01 pip units; other pairs use 0.0001.

## Execution assumptions and limitations
- M15/H1 are resampled from M5 and only bars fully closed by the decision timestamp are exposed to the strategy. This avoids using future higher-timeframe candles.
- Signal is formed after a completed M5 candle; simulated entry is the next M5 open plus/minus half the assumed spread.
- Spread is charged on entry and on executable exit-side OHLC. Slippage, commissions, swaps, variable spreads, latency, rejected orders and broker-specific pricing are not modelled.
- Stop/target checks use subsequent candles only. If both levels are touched in one M5 bar, result is marked AMBIGUOUS and excluded from win/loss totals. Trades still open at the end are marked OPEN and excluded.
- The script tests each pair independently. It does not simulate the live global 15-alert daily cap across all pairs.
- It uses the current Bot 3 rules, so it is not an independent proof that the rules have an edge.

Do not call the result profitable based on a small sample. Review trade charts, use a separate out-of-sample period, compare multiple spread assumptions, and require a sufficiently large sample before considering live alerts. Historical results cannot guarantee future performance.
