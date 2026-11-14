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
        # A sweep must take the level AND close back inside the range.
        # A mere wick through a level is not enough to count as a rejected sweep.
        swept_high = bool(((prior.high > rh) & (prior.close < rh)).any())
        swept_low = bool(((prior.low < rl) & (prior.close > rl)).any())
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
                directional_body = float((impulse.close - impulse.open).clip(lower=0).sum())
                made_progress = (
                    float(impulse.close.iloc[-1]) > float(origin.high)
                    and float(impulse.close.iloc[-1]) >= float(impulse.high.max()) - 0.35 * atr
                    and int((impulse.close > impulse.open).sum()) >= 2
                    and directional_body >= 0.80 * atr
                )
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
                directional_body = float((impulse.open - impulse.close).clip(lower=0).sum())
                made_progress = (
                    float(impulse.close.iloc[-1]) < float(origin.low)
                    and float(impulse.close.iloc[-1]) <= float(impulse.low.min()) + 0.35 * atr
                    and int((impulse.close < impulse.open).sum()) >= 2
                    and directional_body >= 0.80 * atr
                )
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
    prev = x.iloc[-2]
    if side == "SELL":
        aligned = (cur.close < cur.ema21 < cur.ema50 < cur.ema200
                   and cur.ema21 < prev.ema21 and cur.ema50 <= prev.ema50)
    else:
        aligned = (cur.close > cur.ema21 > cur.ema50 > cur.ema200
                   and cur.ema21 > prev.ema21 and cur.ema50 >= prev.ema50)
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
            broke = (float(impulse.close) < rl and float(impulse.close) < float(impulse.open)
                     and abs(float(impulse.close)-float(impulse.open)) >= 0.55*atr)
            retest = float(cur.high) >= rl and float(cur.low) <= rl + 0.20*atr
            rejected = float(cur.close) < rl and float(cur.close) < float(cur.open)
            if broke and retest and rejected:
                return {"entry":float(cur.close), "origin_low":rl,
                        "origin_high":rh, "sweep_extreme":float(x.high.iloc[break_i: ].max()),
                        "atr":atr, "setup":"TREND_CONSOLIDATION_BREAK_RETEST"}
        else:
            broke = (float(impulse.close) > rh and float(impulse.close) > float(impulse.open)
                     and abs(float(impulse.close)-float(impulse.open)) >= 0.55*atr)
            retest = float(cur.low) <= rh and float(cur.high) >= rh - 0.20*atr
            rejected = float(cur.close) > rh and float(cur.close) > float(cur.open)
            if broke and retest and rejected:
                return {"entry":float(cur.close), "origin_low":rl,
                        "origin_high":rh, "sweep_extreme":float(x.low.iloc[break_i:].min()),
                        "atr":atr, "setup":"TREND_CONSOLIDATION_BREAK_RETEST"}
    return None



def find_liquidity_sweep_retest(df: pd.DataFrame, side: str,
                                range_lookback: int = 12,
                                max_pattern_bars: int = 30) -> dict | None:
    """Video-based range sweep, decisive reversal, retest and confirmation.

    SELL: establish a compact range, wick above its high and close back inside,
    confirm bearish displacement, then retest/reject the range high.
    BUY is the exact inverse at the range low. Only the latest completed M5
    candle may trigger; a sweep alone never produces a signal.
    """
    if side not in ("BUY", "SELL") or len(df) < range_lookback + 15:
        return None
    x = df.reset_index(drop=True)
    latest = len(x) - 1
    first = max(range_lookback + 2, latest - max_pattern_bars)

    # Most recent complete sequence first. The range is fixed before the sweep.
    for sweep_i in range(latest - 3, first - 1, -1):
        base = x.iloc[sweep_i - range_lookback:sweep_i]
        if len(base) != range_lookback:
            continue
        rh, rl = float(base.high.max()), float(base.low.min())
        atr = float(indicators(x.iloc[:sweep_i + 1])["atr"].iloc[-1])
        if not pd.notna(atr) or atr <= 0 or rh <= rl or rh - rl > 4.0 * atr:
            continue

        sweep = x.iloc[sweep_i]
        if side == "SELL":
            # A genuine grab above range high, followed by a close back inside.
            if not (float(sweep.high) > rh and float(sweep.close) < rh):
                continue
            sweep_extreme = float(sweep.high)
            displacement_i = None
            for j in range(sweep_i + 1, min(latest, sweep_i + 4) + 1):
                bar = x.iloc[j]
                body = abs(float(bar.close) - float(bar.open))
                if (float(bar.close) < float(bar.open)
                        and float(bar.close) < rh - 0.20 * atr
                        and body >= 0.60 * atr):
                    displacement_i = j
                    break
            if displacement_i is None or latest <= displacement_i:
                continue

            # Retest the swept range high from below; confirmation must be last.
            bar = x.iloc[latest]
            touched = float(bar.high) >= rh - 0.15 * atr and float(bar.low) < rh
            confirmed = (float(bar.close) < float(bar.open)
                         and float(bar.close) < rh
                         and float(bar.close) < float(bar.high))
            if not (touched and confirmed):
                continue
            origin_candidates = [
                j for j in range(sweep_i, displacement_i)
                if float(x.close.iloc[j]) > float(x.open.iloc[j])
            ]
            origin = x.iloc[origin_candidates[-1]] if origin_candidates else sweep
            return {
                "entry": float(bar.close), "origin_low": float(origin.low),
                "origin_high": float(origin.high), "sweep_extreme": sweep_extreme,
                "atr": atr, "range_high": rh, "range_low": rl,
                "setup": "LIQUIDITY_SWEEP_RANGE_RETEST",
            }
        else:
            # A genuine grab below range low, followed by a close back inside.
            if not (float(sweep.low) < rl and float(sweep.close) > rl):
                continue
            sweep_extreme = float(sweep.low)
            displacement_i = None
            for j in range(sweep_i + 1, min(latest, sweep_i + 4) + 1):
                bar = x.iloc[j]
                body = abs(float(bar.close) - float(bar.open))
                if (float(bar.close) > float(bar.open)
                        and float(bar.close) > rl + 0.20 * atr
                        and body >= 0.60 * atr):
                    displacement_i = j
                    break
            if displacement_i is None or latest <= displacement_i:
                continue

            # Retest the swept range low from above; confirmation must be last.
            bar = x.iloc[latest]
            touched = float(bar.low) <= rl + 0.15 * atr and float(bar.high) > rl
            confirmed = (float(bar.close) > float(bar.open)
                         and float(bar.close) > rl
                         and float(bar.close) > float(bar.low))
            if not (touched and confirmed):
                continue
            origin_candidates = [
                j for j in range(sweep_i, displacement_i)
                if float(x.close.iloc[j]) < float(x.open.iloc[j])
            ]
            origin = x.iloc[origin_candidates[-1]] if origin_candidates else sweep
            return {
                "entry": float(bar.close), "origin_low": float(origin.low),
                "origin_high": float(origin.high), "sweep_extreme": sweep_extreme,
                "atr": atr, "range_high": rh, "range_low": rl,
                "setup": "LIQUIDITY_SWEEP_RANGE_RETEST",
            }
    return None

def make_signal(m5: pd.DataFrame, m15: pd.DataFrame, h1: pd.DataFrame, pip: float) -> dict | None:
    """Allow only the three user-approved M5 patterns, with M15/H1 agreement."""
    if len(m5) < 210 or len(m15) < 55 or len(h1) < 55:
        return None
    a = indicators(m5).dropna()
    if len(a) < 3:
        return None
    cur = a.iloc[-1]
    direction = trend(m15)
    if not direction or trend(h1) != direction:
        return None

    # The only permitted live setups. Each detector requires a completed
    # M5 confirmation candle at the end of its sequence.
    candidates = (
        find_origin_retest(a, direction),
        find_liquidity_sweep_retest(a, direction),
        find_impulse_origin_retest(a, direction),
    )
    allowed = {
        "TWO_SIDED_SWEEP_ORIGIN_RETEST",
        "LIQUIDITY_SWEEP_RANGE_RETEST",
        "IMPULSE_ORIGIN_RETEST",
    }
    pattern = next((p for p in candidates if p and p.get("setup") in allowed), None)
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

    # For the video-based range sweep, aim at the opposite range boundary.
    # Reject the setup if that structural target cannot still deliver >= 1.5R.
    actual_rr = MIN_RR
    if pattern.get("setup") == "LIQUIDITY_SWEEP_RANGE_RETEST":
        target = float(pattern["range_high"] if direction == "BUY" else pattern["range_low"])
        reward = (target - entry) if direction == "BUY" else (entry - target)
        actual_rr = reward / risk if risk > 0 else 0.0
        if actual_rr < MIN_RR:
            return None

    if risk <= 0 or not valid_stop_distance(risk, pip, atr):
        return None
    return {
        "direction": direction, "signal_close": entry,
        "stop": float(stop), "target": float(target),
        "risk_pips": round(risk / pip, 1), "rr": round(actual_rr, 2),
        "ema21": float(cur.ema21), "atr": atr, "setup": pattern["setup"],
        "origin_low": float(pattern["origin_low"]),
        "origin_high": float(pattern["origin_high"]),
    }

