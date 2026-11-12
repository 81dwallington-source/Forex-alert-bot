"""Selective M5 trend-pullback strategy. Uses completed candles only."""
from __future__ import annotations
import pandas as pd

EMA_FAST, EMA_MID, EMA_SLOW = 21, 50, 200
ATR_PERIOD = 14
SWING_LOOKBACK = 8
MIN_RR = 1.5
MIN_STOP_PIPS = 8.0
MIN_STOP_ATR = 0.65
MAX_STOP_ATR = 3.0


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


def m5_trend_aligned(df: pd.DataFrame, side: str) -> bool:
    """Require price and all three M5 EMAs to agree; reject countertrend entries."""
    if len(df) < EMA_SLOW + 3:
        return False
    x = indicators(df).dropna()
    if len(x) < 3:
        return False
    cur, prev = x.iloc[-1], x.iloc[-2]
    if side == "BUY":
        return bool(
            cur.close > cur.ema21 > cur.ema50 > cur.ema200
            and cur.ema21 > prev.ema21
            and cur.ema50 > prev.ema50
            and cur.ema200 >= prev.ema200
        )
    if side == "SELL":
        return bool(
            cur.close < cur.ema21 < cur.ema50 < cur.ema200
            and cur.ema21 < prev.ema21
            and cur.ema50 < prev.ema50
            and cur.ema200 <= prev.ema200
        )
    return False


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


def staircase_confirmed(df: pd.DataFrame, side: str, lookback: int = 36) -> bool:
    """Require actual swing pivots plus improving recent price structure."""
    w = df.iloc[-lookback:].reset_index(drop=True)
    if len(w) < 12:
        return False
    highs, lows = [], []
    for i in range(2, len(w)-2):
        if w.high.iloc[i] > w.high.iloc[i-2:i].max() and w.high.iloc[i] > w.high.iloc[i+1:i+3].max():
            highs.append(float(w.high.iloc[i]))
        if w.low.iloc[i] < w.low.iloc[i-2:i].min() and w.low.iloc[i] < w.low.iloc[i+1:i+3].min():
            lows.append(float(w.low.iloc[i]))
    if len(highs) < 2 or len(lows) < 2:
        return False
    mid = len(w)//2
    old, recent = w.iloc[:mid], w.iloc[mid:]
    if side == "BUY":
        return bool(max(recent.high) > max(old.high) and recent.low.mean() > old.low.mean())
    if side == "SELL":
        return bool(min(recent.low) < min(old.low) and recent.high.mean() < old.high.mean())
    return False


def valid_stop_distance(risk: float, pip: float, atr: float) -> bool:
    """Reject tight stops and stops too wide for current volatility."""
    if pip <= 0 or atr <= 0 or risk <= 0:
        return False
    minimum = max(MIN_STOP_PIPS * pip, MIN_STOP_ATR * atr)
    return minimum <= risk <= MAX_STOP_ATR * atr


def make_signal(m5: pd.DataFrame, m15: pd.DataFrame, h1: pd.DataFrame, pip: float) -> dict | None:
    if len(m5) < 210 or len(m15) < 55 or len(h1) < 55:
        return None
    a = indicators(m5).dropna()
    if len(a) < 3:
        return None
    cur, prev = a.iloc[-1], a.iloc[-2]
    direction = trend(m15)
    if not direction or trend(h1) != direction:
        return None

    # New hard filter: M5 must independently agree with M15 and H1.
    if not m5_trend_aligned(a, direction):
        return None
    if not staircase_confirmed(a, direction, lookback=36):
        return None

    recent = a.iloc[-4:-1]
    touched = (recent.low <= recent.ema21 * 1.0015).any() if direction == "BUY" else (recent.high >= recent.ema21 * 0.9985).any()
    if not touched:
        return None
    if direction == "BUY":
        confirmed = cur.close > cur.open and cur.close > prev.high and cur.close > cur.ema21
        swing = confirmed_swing(a, "LOW")
        stop = swing - max(0.25 * float(cur.atr), 1.0 * pip)
        risk = float(cur.close) - stop
        target = float(cur.close) + MIN_RR * risk
    else:
        confirmed = cur.close < cur.open and cur.close < prev.low and cur.close < cur.ema21
        swing = confirmed_swing(a, "HIGH")
        stop = swing + max(0.25 * float(cur.atr), 1.0 * pip)
        risk = stop - float(cur.close)
        target = float(cur.close) - MIN_RR * risk
    if not confirmed or risk <= 0:
        return None
    stop_pips = risk / pip
    if not valid_stop_distance(risk, pip, float(cur.atr)):
        return None
    return {
        "direction": direction, "signal_close": float(cur.close),
        "stop": float(stop), "target": float(target), "risk_pips": round(stop_pips, 1),
        "rr": MIN_RR, "ema21": float(cur.ema21), "atr": float(cur.atr),
    }
