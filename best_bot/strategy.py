"""M5 two-sided sweep, displacement and origin-candle retest strategy."""
from __future__ import annotations
import pandas as pd

EMA_FAST, EMA_MID, EMA_SLOW = 21, 50, 200
ATR_PERIOD = 14
SWING_LOOKBACK = 8
MIN_RR = 1.5
MIN_STOP_PIPS = 8.0
MIN_STOP_ATR = 0.65
MAX_STOP_ATR = 3.0
MAX_CONFIRMATION_EXTENSION_ATR = 0.50
PULLBACK_LOOKBACK = 5
STOP_STRUCTURE_LOOKBACK = 12


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


def pullback_structure_extreme(df: pd.DataFrame, side: str, lookback: int = STOP_STRUCTURE_LOOKBACK) -> float:
    """Use the full recent pullback/confirmation structure, not a tiny local pivot."""
    window = df.iloc[-lookback:]
    if len(window) < lookback:
        raise ValueError("Insufficient candles for pullback stop structure")
    if side == "HIGH":
        return float(window.high.max())
    if side == "LOW":
        return float(window.low.min())
    raise ValueError("side must be HIGH or LOW")


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



def find_origin_retest(df: pd.DataFrame, side: str, range_bars: int = 12,
                       max_pattern_bars: int = 30) -> dict | None:
    """Find a two-sided consolidation sweep, displacement, then origin-candle retest.

    Uses completed candles only. A BUY requires both range edges to be swept,
    then a bullish displacement through range high and a confirmed retest of
    the last bearish candle before displacement. SELL is the exact inverse.
    """
    if side not in ("BUY", "SELL") or len(df) < range_bars + 8:
        return None
    x = df.reset_index(drop=True)
    start = max(range_bars + 2, len(x) - max_pattern_bars)
    # Search the most recent candidate pattern first.
    for breakout_i in range(len(x) - 2, start, -1):
        # Build the range before an eight-candle sweep window. This allows
        # the two liquidity grabs to occur separately, not only in two bars.
        sweep_start = breakout_i - 8
        base = x.iloc[sweep_start-range_bars:sweep_start]
        if len(base) != range_bars or sweep_start < range_bars:
            continue
        rh, rl = float(base.high.max()), float(base.low.min())
        atr_series = indicators(x.iloc[:breakout_i+1])["atr"]
        atr = float(atr_series.iloc[-1])
        if not atr or atr <= 0 or (rh-rl) > 4.0*atr:
            continue
        prior = x.iloc[sweep_start:breakout_i]
        # Both edges must have been taken during the pattern before displacement.
        swept_high = bool((prior.high > rh).any())
        swept_low = bool((prior.low < rl).any())
        if not (swept_high and swept_low):
            continue
        impulse = x.iloc[breakout_i]
        body = abs(float(impulse.close)-float(impulse.open))
        if side == "BUY":
            displacement = (impulse.close > rh and impulse.close > impulse.open
                            and body >= 0.60*atr)
            opposite = [j for j in range(max(start, breakout_i-4), breakout_i)
                        if x.open.iloc[j] > x.close.iloc[j]]
            if not displacement or not opposite:
                continue
            origin_i = opposite[-1]
            origin = x.iloc[origin_i]
            zone_low, zone_high = sorted((float(origin.open), float(origin.close)))
            sweep_extreme = float(prior.low.min())
            retest = x.iloc[breakout_i+1:]
            if retest.empty:
                continue
            for _, bar in retest.iterrows():
                touched = float(bar.low) <= zone_high and float(bar.high) >= zone_low
                confirmed = float(bar.close) > float(bar.open) and float(bar.close) > zone_high
                if touched and confirmed and bar.name == retest.index[-1]:
                    return {"entry":float(bar.close),"origin_low":float(origin.low),
                            "origin_high":float(origin.high),"sweep_extreme":sweep_extreme,
                            "atr":atr,"range_high":rh,"range_low":rl,"setup":"TWO_SIDED_SWEEP_ORIGIN_RETEST"}
        else:
            displacement = (impulse.close < rl and impulse.close < impulse.open
                            and body >= 0.60*atr)
            opposite = [j for j in range(max(start, breakout_i-4), breakout_i)
                        if x.open.iloc[j] < x.close.iloc[j]]
            if not displacement or not opposite:
                continue
            origin_i = opposite[-1]
            origin = x.iloc[origin_i]
            zone_low, zone_high = sorted((float(origin.open), float(origin.close)))
            sweep_extreme = float(prior.high.max())
            retest = x.iloc[breakout_i+1:]
            if retest.empty:
                continue
            for _, bar in retest.iterrows():
                touched = float(bar.high) >= zone_low and float(bar.low) <= zone_high
                confirmed = float(bar.close) < float(bar.open) and float(bar.close) < zone_low
                if touched and confirmed and bar.name == retest.index[-1]:
                    return {"entry":float(bar.close),"origin_low":float(origin.low),
                            "origin_high":float(origin.high),"sweep_extreme":sweep_extreme,
                            "atr":atr,"range_high":rh,"range_low":rl,"setup":"TWO_SIDED_SWEEP_ORIGIN_RETEST"}
    return None


def find_impulse_origin_retest(df: pd.DataFrame, side: str,
                              max_pattern_bars: int = 28) -> dict | None:
    """Find a directional impulse, pullback to its launch candle, then rejection.

    The latest completed candle must retest the launch candle body and close
    back in the direction of the impulse. BUY/SELL are exact mirrors.
    """
    if side not in ("BUY", "SELL") or len(df) < 18:
        return None
    x = df.reset_index(drop=True)
    latest = len(x) - 1
    first_origin = max(2, latest - max_pattern_bars)
    # Most recent launch zones first; require a completed pullback after the impulse.
    for origin_i in range(latest - 5, first_origin - 1, -1):
        origin = x.iloc[origin_i]
        if side == "BUY" and float(origin.close) >= float(origin.open):
            continue
        if side == "SELL" and float(origin.close) <= float(origin.open):
            continue
        zone_low, zone_high = sorted((float(origin.open), float(origin.close)))
        for impulse_end in range(origin_i + 3, latest):
            impulse = x.iloc[origin_i + 1:impulse_end + 1]
            if len(impulse) < 3:
                continue
            atr = float(indicators(x.iloc[:impulse_end + 1])["atr"].iloc[-1])
            if not pd.notna(atr) or atr <= 0:
                continue
            if side == "BUY":
                impulse_size = float(impulse.high.max()) - float(origin.close)
                made_progress = float(impulse.close.iloc[-1]) > float(origin.high)
                pullback_low = float(x.low.iloc[impulse_end + 1:].min())
                latest_bar = x.iloc[latest]
                touched = float(latest_bar.low) <= zone_high and float(latest_bar.high) >= zone_low
                rejected = float(latest_bar.close) > float(latest_bar.open) and float(latest_bar.close) > zone_high
                if (impulse_size >= 1.5 * atr and made_progress and touched and rejected
                        and pullback_low >= float(origin.low) - 0.35 * atr):
                    return {"entry": float(latest_bar.close), "origin_low": float(origin.low),
                            "origin_high": float(origin.high),
                            "sweep_extreme": float(x.low.iloc[origin_i:latest + 1].min()),
                            "atr": atr, "setup": "IMPULSE_ORIGIN_RETEST"}
            else:
                impulse_size = float(origin.close) - float(impulse.low.min())
                made_progress = float(impulse.close.iloc[-1]) < float(origin.low)
                pullback_high = float(x.high.iloc[impulse_end + 1:].max())
                latest_bar = x.iloc[latest]
                touched = float(latest_bar.high) >= zone_low and float(latest_bar.low) <= zone_high
                rejected = float(latest_bar.close) < float(latest_bar.open) and float(latest_bar.close) < zone_low
                if (impulse_size >= 1.5 * atr and made_progress and touched and rejected
                        and pullback_high <= float(origin.high) + 0.35 * atr):
                    return {"entry": float(latest_bar.close), "origin_low": float(origin.low),
                            "origin_high": float(origin.high),
                            "sweep_extreme": float(x.high.iloc[origin_i:latest + 1].max()),
                            "atr": atr, "setup": "IMPULSE_ORIGIN_RETEST"}
    return None


def find_trend_consolidation_break_retest(df: pd.DataFrame, side: str,
                                          range_bars: int = 10,
                                          max_break_bars: int = 5) -> dict | None:
    """Trend-aligned consolidation break followed by a retest of the range edge.

    BUY mirrors SELL: EMA stack establishes direction, a compact pause forms,
    price closes out of the pause, then the latest completed candle retests
    the broken edge and rejects it in the trend direction.
    """
    if side not in ("BUY", "SELL") or len(df) < 210:
        return None
    x = indicators(df).dropna().reset_index(drop=True)
    if len(x) < range_bars + max_break_bars + 3:
        return None
    cur = x.iloc[-1]
    if side == "SELL":
        aligned = cur.close < cur.ema21 < cur.ema50 < cur.ema200
    else:
        aligned = cur.close > cur.ema21 > cur.ema50 > cur.ema200
    if not aligned:
        return None

    # A breakout must have occurred shortly before the current retest candle.
    for break_i in range(len(x)-2, max(10, len(x)-max_break_bars-1), -1):
        base = x.iloc[break_i-range_bars:break_i]
        if len(base) != range_bars:
            continue
        rh, rl = float(base.high.max()), float(base.low.min())
        atr = float(x.atr.iloc[break_i])
        if not pd.notna(atr) or atr <= 0 or rh-rl > 2.5*atr:
            continue
        impulse = x.iloc[break_i]
        if side == "SELL":
            broke = float(impulse.close) < rl and float(impulse.close) < float(impulse.open)
            retest = float(cur.high) >= rl and float(cur.low) <= rl + 0.20*atr
            rejected = float(cur.close) < rl and float(cur.close) < float(cur.open)
            if broke and retest and rejected:
                return {"entry":float(cur.close), "origin_low":rl,
                        "origin_high":rh, "sweep_extreme":float(x.high.iloc[break_i: ].max()),
                        "atr":atr, "setup":"TREND_CONSOLIDATION_BREAK_RETEST"}
        else:
            broke = float(impulse.close) > rh and float(impulse.close) > float(impulse.open)
            retest = float(cur.low) <= rh and float(cur.high) >= rh - 0.20*atr
            rejected = float(cur.close) > rh and float(cur.close) > float(cur.open)
            if broke and retest and rejected:
                return {"entry":float(cur.close), "origin_low":rl,
                        "origin_high":rh, "sweep_extreme":float(x.low.iloc[break_i:].min()),
                        "atr":atr, "setup":"TREND_CONSOLIDATION_BREAK_RETEST"}
    return None

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

    # M15 and H1 must agree. M5 entry is decided by the requested pattern,
    # not by a separate EMA-stack or staircase filter.
    # Required sequence: consolidation -> both edges swept -> displacement
    # through one edge -> retest and rejection of the displacement-origin candle.
    pattern = find_origin_retest(a, direction)
    if not pattern:
        pattern = find_impulse_origin_retest(a, direction)
    if not pattern:
        pattern = find_trend_consolidation_break_retest(a, direction)
    if not pattern:
        return None
    atr = float(pattern["atr"])
    entry = float(pattern["entry"])
    buffer = max(0.25 * atr, 1.0 * pip)
    if direction == "BUY":
        stop = min(float(pattern["sweep_extreme"]), float(pattern["origin_low"])) - buffer
        risk = entry - stop
        target = entry + MIN_RR * risk
    else:
        stop = max(float(pattern["sweep_extreme"]), float(pattern["origin_high"])) + buffer
        risk = stop - entry
        target = entry - MIN_RR * risk
    if risk <= 0:
        return None
    stop_pips = risk / pip
    # If the proper structural stop is too wide, reject the trade; never
    # pull the stop inside the swing just to force an alert.
    if not valid_stop_distance(risk, pip, atr):
        return None
    return {
        "direction": direction, "signal_close": entry,
        "stop": float(stop), "target": float(target), "risk_pips": round(stop_pips, 1),
        "rr": MIN_RR, "ema21": float(cur.ema21), "atr": atr,
        "setup": pattern.get("setup", "ORIGIN_CANDLE_RETEST"),
        "origin_low": float(pattern["origin_low"]), "origin_high": float(pattern["origin_high"]),
    }
