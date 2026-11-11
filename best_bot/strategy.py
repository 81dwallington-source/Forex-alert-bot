"""Fresh M5 trend-pullback strategy. Signals use completed candles only."""
from __future__ import annotations
import pandas as pd

EMA_FAST, EMA_MID, EMA_SLOW = 21, 50, 200
ATR_PERIOD = 14
SWING_LOOKBACK = 8
MIN_RR = 1.5
MIN_STOP_PIPS = 4.0
MAX_STOP_ATR = 2.5


def indicators(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    for c in ("open", "high", "low", "close"):
        if c not in x:
            raise ValueError(f"Missing OHLC column: {c}")
        x[c] = pd.to_numeric(x[c], errors="raise")
    x["ema21"] = x.close.ewm(span=EMA_FAST, adjust=False).mean()
    x["ema50"] = x.close.ewm(span=EMA_MID, adjust=False).mean()
    x["ema200"] = x.close.ewm(span=EMA_SLOW, adjust=False).mean()
    prev = x.close.shift()
    tr = pd.concat([x.high-x.low, (x.high-prev).abs(), (x.low-prev).abs()], axis=1).max(axis=1)
    x["atr"] = tr.rolling(ATR_PERIOD).mean()
    return x


def trend(df: pd.DataFrame) -> str | None:
    x = indicators(df).dropna()
    if len(x) < 5:
        return None
    a, b = x.iloc[-1], x.iloc[-2]
    if a.close > a.ema21 > a.ema50 > a.ema200 and a.ema21 > b.ema21:
        return "BUY"
    if a.close < a.ema21 < a.ema50 < a.ema200 and a.ema21 < b.ema21:
        return "SELL"
    return None


def make_signal(m5: pd.DataFrame, m15: pd.DataFrame, h1: pd.DataFrame, pip: float) -> dict | None:
    # Input frames must end at the latest fully closed candle available at signal time.
    if min(len(m5), len(m15), len(h1)) < 210:
        return None
    a = indicators(m5).dropna()
    if len(a) < 3:
        return None
    cur, prev = a.iloc[-1], a.iloc[-2]
    direction = trend(m15)
    if not direction or trend(h1) != direction:
        return None

    # Price must have pulled back to EMA21 recently, then close back through
    # the prior candle's extreme in the direction of the aligned higher trend.
    recent = a.iloc[-4:-1]
    touched = (recent.low <= recent.ema21 * 1.0015).any() if direction == "BUY" else (recent.high >= recent.ema21 * 0.9985).any()
    if not touched:
        return None
    if direction == "BUY":
        confirmed = cur.close > cur.open and cur.close > prev.high and cur.close > cur.ema21
        swing = float(a.low.iloc[-(SWING_LOOKBACK+1):-1].min())
        stop = swing - max(0.15 * float(cur.atr), 0.5 * pip)
        risk = float(cur.close) - stop
        target = float(cur.close) + MIN_RR * risk
    else:
        confirmed = cur.close < cur.open and cur.close < prev.low and cur.close < cur.ema21
        swing = float(a.high.iloc[-(SWING_LOOKBACK+1):-1].max())
        stop = swing + max(0.15 * float(cur.atr), 0.5 * pip)
        risk = stop - float(cur.close)
        target = float(cur.close) - MIN_RR * risk
    if not confirmed or risk <= 0:
        return None
    stop_pips = risk / pip
    if stop_pips < MIN_STOP_PIPS or risk > MAX_STOP_ATR * float(cur.atr):
        return None
    return {
        "direction": direction, "signal_close": float(cur.close),
        "stop": float(stop), "target": float(target), "risk_pips": round(stop_pips, 1),
        "rr": MIN_RR, "ema21": float(cur.ema21), "atr": float(cur.atr),
    }
