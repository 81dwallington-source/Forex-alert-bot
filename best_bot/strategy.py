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
    # M15/H1 provider windows contain about 190 candles. Use EMA21/50 alignment
    # and EMA21 slope so those real feed windows can confirm trend without
    # pretending a 200-period EMA exists in the returned history.
    if len(df) < 55:
        return None
    close = pd.to_numeric(df["close"], errors="coerce")
    ema21 = close.ewm(span=EMA_FAST, adjust=False).mean()
    ema50 = close.ewm(span=EMA_MID, adjust=False).mean()
    if close.isna().any():
        return None
    if close.iloc[-1] > ema21.iloc[-1] > ema50.iloc[-1] and ema21.iloc[-1] > ema21.iloc[-2]:
        return "BUY"
    if close.iloc[-1] < ema21.iloc[-1] < ema50.iloc[-1] and ema21.iloc[-1] < ema21.iloc[-2]:
        return "SELL"
    return None


def confirmed_swing(df: pd.DataFrame, side: str, lookback: int = 36) -> float:
    """Latest confirmed two-bars-left/two-bars-right M5 swing; conservative fallback."""
    window = df.iloc[-lookback:]
    pivots = []
    for i in range(2, len(window) - 2):
        if side == "LOW":
            value = float(window.low.iloc[i])
            if value < float(window.low.iloc[i-2:i].min()) and value < float(window.low.iloc[i+1:i+3].min()):
                pivots.append(value)
        else:
            value = float(window.high.iloc[i])
            if value > float(window.high.iloc[i-2:i].max()) and value > float(window.high.iloc[i+1:i+3].max()):
                pivots.append(value)
    if pivots:
        return pivots[-1]
    tail = df.iloc[-(SWING_LOOKBACK+1):-1]
    return float(tail.low.min() if side == "LOW" else tail.high.max())


def make_signal(m5: pd.DataFrame, m15: pd.DataFrame, h1: pd.DataFrame, pip: float) -> dict | None:
    # M5 requires EMA200 history; higher timeframes use the independently
    # aligned EMA21/50 trend filter above.
    if len(m5) < 210 or len(m15) < 55 or len(h1) < 55:
        return None
    a = indicators(m5).dropna()
    if len(a) < 3:
        return None
    cur, prev = a.iloc[-1], a.iloc[-2]
    direction = trend(m15)
    if not direction or trend(h1) != direction:
        return None

    recent = a.iloc[-4:-1]
    touched = (recent.low <= recent.ema21 * 1.0015).any() if direction == "BUY" else (recent.high >= recent.ema21 * 0.9985).any()
    if not touched:
        return None
    if direction == "BUY":
        confirmed = cur.close > cur.open and cur.close > prev.high and cur.close > cur.ema21
        swing = confirmed_swing(a, "LOW")
        stop = swing - max(0.15 * float(cur.atr), 0.5 * pip)
        risk = float(cur.close) - stop
        target = float(cur.close) + MIN_RR * risk
    else:
        confirmed = cur.close < cur.open and cur.close < prev.low and cur.close < cur.ema21
        swing = confirmed_swing(a, "HIGH")
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
