"""M5 two-sided sweep, displacement and origin-candle retest strategy."""
from __future__ import annotations
import pandas as pd

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
    prev = x.close.shift()
    tr = pd.concat([x.high-x.low, (x.high-prev).abs(), (x.low-prev).abs()], axis=1).max(axis=1)
    x["atr"] = tr.rolling(ATR_PERIOD).mean()
    return x

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


def impulse_retracement(side: str, origin_level: float,
                        impulse_extreme: float, pullback_extreme: float) -> float:
    """Measure pullback against the complete origin-to-impulse move."""
    if side == "BUY":
        full_move = impulse_extreme - origin_level
        retraced = impulse_extreme - pullback_extreme
    elif side == "SELL":
        full_move = origin_level - impulse_extreme
        retraced = pullback_extreme - impulse_extreme
    else:
        return 0.0
    if full_move <= 0:
        return 0.0
    return retraced / full_move


def valid_deep_pullback(retracement: float, depth_atr: float, bars: int) -> bool:
    """Fail closed: no impulse entry on a shallow, tiny, or one/two-bar dip."""
    return bool(0.50 <= retracement <= 0.85 and depth_atr >= 0.80 and bars >= 3)



def find_origin_retest(df: pd.DataFrame, side: str, range_bars: int = 12,
                       max_pattern_bars: int = 30) -> dict | None:
    """Two-sided range sweep, then retest of the FIRST candle that starts displacement.

    BUY: sweep below range low, then break above range high. The origin is the
    first bullish candle after the low sweep that begins the sustained upward leg.
    SELL is the exact inverse: sweep above range high, then break below range low;
    origin is the first bearish candle after the high sweep.
    """
    if side not in ("BUY", "SELL") or len(df) < range_bars + 8:
        return None
    x = df.reset_index(drop=True)
    start = max(range_bars + 2, len(x) - max_pattern_bars)
    for breakout_i in range(len(x) - 2, start, -1):
        sweep_start = breakout_i - 8
        base = x.iloc[sweep_start-range_bars:sweep_start]
        if len(base) != range_bars or sweep_start < range_bars:
            continue
        rh, rl = float(base.high.max()), float(base.low.min())
        atr = float(indicators(x.iloc[:breakout_i+1])["atr"].iloc[-1])
        if not pd.notna(atr) or atr <= 0 or (rh-rl) > 4.0*atr:
            continue
        prior = x.iloc[sweep_start:breakout_i]
        high_sweep_mask = (prior.high > rh) & (prior.close < rh)
        low_sweep_mask = (prior.low < rl) & (prior.close > rl)
        if not (bool(high_sweep_mask.any()) and bool(low_sweep_mask.any())):
            continue
        high_sweep_i = int(high_sweep_mask[high_sweep_mask].index[-1])
        low_sweep_i = int(low_sweep_mask[low_sweep_mask].index[-1])
        impulse = x.iloc[breakout_i]
        body = abs(float(impulse.close)-float(impulse.open))
        if side == "BUY":
            if not (low_sweep_i > high_sweep_i and impulse.close > rh
                    and impulse.close > impulse.open and body >= 0.60*atr):
                continue
            # The first bullish candle after the downside sweep is the launch candle.
            candidates = [j for j in range(low_sweep_i + 1, breakout_i)
                          if float(x.close.iloc[j]) > float(x.open.iloc[j])]
            if not candidates:
                continue
            origin_i = candidates[0]
            # Reject a one-candle pop: require a sustained upward leg from the origin.
            leg = x.iloc[origin_i:breakout_i+1]
            if int((leg.close > leg.open).sum()) < 2 or float(leg.close.iloc[-1]) <= rh:
                continue
            origin = x.iloc[origin_i]
            zone_low, zone_high = sorted((float(origin.open), float(origin.close)))
            retest = x.iloc[breakout_i+1:]
            if retest.empty:
                continue
            last = retest.iloc[-1]
            touched = float(last.low) <= zone_high and float(last.high) >= zone_low
            confirmed = float(last.close) > float(last.open) and float(last.close) > zone_high
            if touched and confirmed:
                return {"entry":zone_high,"origin_low":float(origin.low),
                        "origin_high":float(origin.high),"origin_body_low":zone_low,
                        "origin_body_high":zone_high,"origin_time":str(df.index[origin_i]),
                        "sweep_extreme":float(prior.low.min()),"atr":atr,
                        "range_high":rh,"range_low":rl,"setup":"TWO_SIDED_SWEEP_ORIGIN_RETEST"}
        else:
            if not (high_sweep_i > low_sweep_i and impulse.close < rl
                    and impulse.close < impulse.open and body >= 0.60*atr):
                continue
            # The first bearish candle after the upside sweep is the launch candle.
            candidates = [j for j in range(high_sweep_i + 1, breakout_i)
                          if float(x.close.iloc[j]) < float(x.open.iloc[j])]
            if not candidates:
                continue
            origin_i = candidates[0]
            leg = x.iloc[origin_i:breakout_i+1]
            if int((leg.close < leg.open).sum()) < 2 or float(leg.close.iloc[-1]) >= rl:
                continue
            origin = x.iloc[origin_i]
            zone_low, zone_high = sorted((float(origin.open), float(origin.close)))
            retest = x.iloc[breakout_i+1:]
            if retest.empty:
                continue
            last = retest.iloc[-1]
            touched = float(last.high) >= zone_low and float(last.low) <= zone_high
            confirmed = float(last.close) < float(last.open) and float(last.close) < zone_low
            if touched and confirmed:
                return {"entry":zone_low,"origin_low":float(origin.low),
                        "origin_high":float(origin.high),"origin_body_low":zone_low,
                        "origin_body_high":zone_high,"origin_time":str(origin.get("time", origin_i)),
                        "sweep_extreme":float(prior.high.max()),"atr":atr,
                        "range_high":rh,"range_low":rl,"setup":"TWO_SIDED_SWEEP_ORIGIN_RETEST"}
    return None


def find_two_sided_origin_approach(df: pd.DataFrame, side: str,
                                   range_bars: int = 12,
                                   max_pattern_bars: int = 30) -> dict | None:
    """Return the first launch candle for a completed two-sided range sweep/breakout,
    but only while price has not yet retested its body.
    """
    if side not in ("BUY", "SELL") or len(df) < range_bars + 10:
        return None
    x = df.reset_index(drop=True)
    start = max(range_bars + 2, len(x) - max_pattern_bars)
    for breakout_i in range(len(x) - 1, start, -1):
        sweep_start = breakout_i - 8
        base = x.iloc[sweep_start-range_bars:sweep_start]
        if len(base) != range_bars or sweep_start < range_bars:
            continue
        rh, rl = float(base.high.max()), float(base.low.min())
        atr = float(indicators(x.iloc[:breakout_i+1])["atr"].iloc[-1])
        if not pd.notna(atr) or atr <= 0 or (rh-rl) > 4.0*atr:
            continue
        prior = x.iloc[sweep_start:breakout_i]
        hs = (prior.high > rh) & (prior.close < rh)
        ls = (prior.low < rl) & (prior.close > rl)
        if not (bool(hs.any()) and bool(ls.any())):
            continue
        hi = int(hs[hs].index[-1]); lo = int(ls[ls].index[-1])
        if side == "BUY":
            if not (lo > hi and float(x.close.iloc[breakout_i]) > rh
                    and float(x.close.iloc[breakout_i]) > float(x.open.iloc[breakout_i])):
                continue
            origins = [j for j in range(lo+1, breakout_i)
                       if float(x.close.iloc[j]) > float(x.open.iloc[j])]
            if not origins: continue
            oi = origins[0]
            low, high = sorted((float(x.open.iloc[oi]), float(x.close.iloc[oi])))
            later = x.iloc[breakout_i+1:]
            if later.empty or float(later.low.min()) <= high: continue
        else:
            if not (hi > lo and float(x.close.iloc[breakout_i]) < rl
                    and float(x.close.iloc[breakout_i]) < float(x.open.iloc[breakout_i])):
                continue
            origins = [j for j in range(hi+1, breakout_i)
                       if float(x.close.iloc[j]) < float(x.open.iloc[j])]
            if not origins: continue
            oi = origins[0]
            low, high = sorted((float(x.open.iloc[oi]), float(x.close.iloc[oi])))
            later = x.iloc[breakout_i+1:]
            if later.empty or float(later.high.max()) >= low: continue
        return {"direction":side,"origin_time":str(df.index[oi]),
                "origin_low":float(x.low.iloc[oi]),"origin_high":float(x.high.iloc[oi]),
                "origin_body_low":low,"origin_body_high":high,"atr":atr,
                "setup":"TWO_SIDED_SWEEP_ORIGIN_RETEST"}
    return None

def find_impulse_origin_retest(df: pd.DataFrame, side: str,
                              max_pattern_bars: int = 28) -> dict | None:
    """Find a directional impulse, deep pullback to its launch candle, then rejection.

    Pullback depth is measured from the complete launch-candle extreme to the
    impulse extreme, not from a shortened subset of impulse candles. The
    latest completed candle must retest the launch body and reject in direction.
    BUY/SELL are exact mirrors; 50–85% retracement is mandatory.
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
                pullback = x.iloc[impulse_end + 1:latest + 1]
                pullback_low = float(pullback.low.min()) if not pullback.empty else float("inf")
                retrace = impulse_retracement("BUY", float(origin.low), float(impulse.high.max()), pullback_low)
                latest_bar = x.iloc[latest]
                touched = float(latest_bar.low) <= zone_high and float(latest_bar.high) >= zone_low
                rejected = float(latest_bar.close) > float(latest_bar.open) and float(latest_bar.close) > zone_high
                if (impulse_size >= 1.5 * atr and made_progress
                        and valid_deep_pullback(retrace, (float(impulse.high.max()) - pullback_low) / atr, len(pullback))
                        and touched and rejected
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
                pullback = x.iloc[impulse_end + 1:latest + 1]
                pullback_high = float(pullback.high.max()) if not pullback.empty else float("-inf")
                retrace = impulse_retracement("SELL", float(origin.high), float(impulse.low.min()), pullback_high)
                latest_bar = x.iloc[latest]
                touched = float(latest_bar.high) >= zone_low and float(latest_bar.low) <= zone_high
                rejected = float(latest_bar.close) < float(latest_bar.open) and float(latest_bar.close) < zone_low
                if (impulse_size >= 1.5 * atr and made_progress
                        and valid_deep_pullback(retrace, (pullback_high - float(impulse.low.min())) / atr, len(pullback))
                        and touched and rejected
                        and pullback_high <= float(origin.high) + 0.35 * atr):
                    return {"entry": float(latest_bar.close), "origin_low": float(origin.low),
                            "origin_high": float(origin.high),
                            "sweep_extreme": float(x.high.iloc[origin_i:latest + 1].max()),
                            "atr": atr, "setup": "IMPULSE_ORIGIN_RETEST"}
    return None



def find_impulse_origin_approach(df: pd.DataFrame, side: str,
                                max_pattern_bars: int = 48) -> dict | None:
    """Find the exact opposing launch candle before a strong impulse, before its retest.

    Returns only an approaching (not-yet-touched) origin. Completed candles
    define the pattern; the live quote is used by the caller for proximity.
    """
    if side not in ("BUY", "SELL") or len(df) < 30:
        return None
    x = indicators(df).dropna()
    latest = len(x) - 1
    first_end = max(12, latest - max_pattern_bars)
    for impulse_end in range(latest - 2, first_end - 1, -1):
        pullback = x.iloc[impulse_end + 1:latest + 1]
        if len(pullback) < 2 or len(pullback) > 18:
            continue
        for leg_len in range(3, 8):
            start = impulse_end - leg_len + 1
            origin_i = start - 1
            if origin_i < 1:
                continue
            leg = x.iloc[start:impulse_end + 1]
            origin = x.iloc[origin_i]
            atr = float(x.atr.iloc[impulse_end])
            if not pd.notna(atr) or atr <= 0:
                continue
            impulse_high, impulse_low = float(leg.high.max()), float(leg.low.min())
            if side == "BUY":
                if float(origin.close) >= float(origin.open):
                    continue
                body_low, body_high = sorted((float(origin.open), float(origin.close)))
                strong = (
                    int((leg.close > leg.open).sum()) >= 3
                    and float((leg.close - leg.open).clip(lower=0).sum()) >= 1.25 * atr
                    and float(leg.high.max() - leg.low.min()) >= 1.8 * atr
                    and float(leg.close.iloc[-1]) >= impulse_high - 0.30 * atr
                )
                pullback_low = float(pullback.low.min())
                retrace = (impulse_high - pullback_low) / max(impulse_high - impulse_low, 1e-12)
                untouched = float(pullback.low.min()) > body_high
                still_valid = pullback_low > float(origin.low) - 0.20 * atr
                if (strong and untouched and still_valid and len(pullback) >= 2
                        and 0.20 <= retrace < 0.85):
                    return {
                        "direction": side, "origin_time": str(x.index[origin_i]),
                        "origin_low": float(origin.low), "origin_high": float(origin.high),
                        "origin_body_low": body_low, "origin_body_high": body_high,
                        "atr": atr, "impulse_high": impulse_high, "impulse_low": impulse_low,
                        "setup": "IMPULSE_PULLBACK_CONTINUATION",
                    }
            else:
                if float(origin.close) <= float(origin.open):
                    continue
                body_low, body_high = sorted((float(origin.open), float(origin.close)))
                strong = (
                    int((leg.close < leg.open).sum()) >= 3
                    and float((leg.open - leg.close).clip(lower=0).sum()) >= 1.25 * atr
                    and float(leg.high.max() - leg.low.min()) >= 1.8 * atr
                    and float(leg.close.iloc[-1]) <= impulse_low + 0.30 * atr
                )
                pullback_high = float(pullback.high.max())
                retrace = (pullback_high - impulse_low) / max(impulse_high - impulse_low, 1e-12)
                untouched = float(pullback.high.max()) < body_low
                still_valid = pullback_high < float(origin.high) + 0.20 * atr
                if (strong and untouched and still_valid and len(pullback) >= 2
                        and 0.20 <= retrace < 0.85):
                    return {
                        "direction": side, "origin_time": str(x.index[origin_i]),
                        "origin_low": float(origin.low), "origin_high": float(origin.high),
                        "origin_body_low": body_low, "origin_body_high": body_high,
                        "atr": atr, "impulse_high": impulse_high, "impulse_low": impulse_low,
                        "setup": "IMPULSE_PULLBACK_CONTINUATION",
                    }
    return None


def find_impulse_pullback_continuation(df: pd.DataFrame, side: str,
                                      max_pattern_bars: int = 48) -> dict | None:
    """Strong impulse followed by a confirmed retest of its exact launch candle.

    The origin is the last opposing candle immediately before the 3–7 candle
    displacement leg. Entry reference is the near edge of that candle's body.
    """
    if side not in ("BUY", "SELL") or len(df) < 30:
        return None
    x = indicators(df).dropna()
    latest = len(x) - 1
    first_end = max(12, latest - max_pattern_bars)
    for impulse_end in range(latest - 2, first_end - 1, -1):
        pullback = x.iloc[impulse_end + 1:latest + 1]
        if len(pullback) < 2 or len(pullback) > 18:
            continue
        for leg_len in range(3, 8):
            start = impulse_end - leg_len + 1
            origin_i = start - 1
            if origin_i < 1:
                continue
            leg = x.iloc[start:impulse_end + 1]
            origin = x.iloc[origin_i]
            atr = float(x.atr.iloc[impulse_end])
            if not pd.notna(atr) or atr <= 0:
                continue
            impulse_high, impulse_low = float(leg.high.max()), float(leg.low.min())
            last = x.iloc[latest]
            if side == "BUY":
                if float(origin.close) >= float(origin.open):
                    continue
                body_low, body_high = sorted((float(origin.open), float(origin.close)))
                strong = (
                    int((leg.close > leg.open).sum()) >= 3
                    and float((leg.close - leg.open).clip(lower=0).sum()) >= 1.25 * atr
                    and float(leg.high.max() - leg.low.min()) >= 1.8 * atr
                    and float(leg.close.iloc[-1]) >= impulse_high - 0.30 * atr
                )
                pb_low = float(pullback.low.min())
                retrace = (impulse_high - pb_low) / max(impulse_high - impulse_low, 1e-12)
                touched = bool(((pullback.low <= body_high) & (pullback.high >= body_low)).any())
                confirm = (float(last.close) > float(last.open)
                           and float(last.close) > body_high
                           and float(last.close) > float(x.close.iloc[latest - 1]))
                if (strong and valid_deep_pullback(retrace, (impulse_high - pb_low) / atr, len(pullback))
                        and touched and confirm
                        and pb_low >= float(origin.low) - 0.20 * atr):
                    return {
                        "entry": body_high, "origin_low": float(origin.low),
                        "origin_high": float(origin.high), "origin_body_low": body_low,
                        "origin_body_high": body_high, "origin_time": str(x.index[origin_i]),
                        "sweep_extreme": pb_low, "atr": atr,
                        "setup": "IMPULSE_PULLBACK_CONTINUATION",
                    }
            else:
                if float(origin.close) <= float(origin.open):
                    continue
                body_low, body_high = sorted((float(origin.open), float(origin.close)))
                strong = (
                    int((leg.close < leg.open).sum()) >= 3
                    and float((leg.open - leg.close).clip(lower=0).sum()) >= 1.25 * atr
                    and float(leg.high.max() - leg.low.min()) >= 1.8 * atr
                    and float(leg.close.iloc[-1]) <= impulse_low + 0.30 * atr
                )
                pb_high = float(pullback.high.max())
                retrace = (pb_high - impulse_low) / max(impulse_high - impulse_low, 1e-12)
                touched = bool(((pullback.high >= body_low) & (pullback.low <= body_high)).any())
                confirm = (float(last.close) < float(last.open)
                           and float(last.close) < body_low
                           and float(last.close) < float(x.close.iloc[latest - 1]))
                if (strong and valid_deep_pullback(retrace, (pb_high - impulse_low) / atr, len(pullback))
                        and touched and confirm
                        and pb_high <= float(origin.high) + 0.20 * atr):
                    return {
                        "entry": body_low, "origin_low": float(origin.low),
                        "origin_high": float(origin.high), "origin_body_low": body_low,
                        "origin_body_high": body_high, "origin_time": str(x.index[origin_i]),
                        "sweep_extreme": pb_high, "atr": atr,
                        "setup": "IMPULSE_PULLBACK_CONTINUATION",
                    }
    return None

def find_liquidity_sweep_retest(df: pd.DataFrame, side: str,
                                range_lookback: int = 36,
                                max_pattern_bars: int = 48) -> dict | None:
    """Detect sweep reversals with either same-edge or opposite-edge retests.

    BUY: sweep below range low, then either displace/retest the low from above,
    or break above the opposite range high and retest that breakout level.
    SELL is the exact inverse. The latest completed M5 candle must confirm.
    """
    if side not in ("BUY", "SELL") or len(df) < range_lookback + 15:
        return None
    x = df.reset_index(drop=True)
    latest = len(x) - 1
    first = max(range_lookback + 2, latest - max_pattern_bars)

    for sweep_i in range(latest - 3, first - 1, -1):
        base = x.iloc[sweep_i - range_lookback:sweep_i]
        if len(base) != range_lookback:
            continue
        rh, rl = float(base.high.max()), float(base.low.min())
        atr = float(indicators(x.iloc[:sweep_i + 1])["atr"].iloc[-1])
        if not pd.notna(atr) or atr <= 0 or rh <= rl or rh - rl > 4.0 * atr:
            continue

        sweep = x.iloc[sweep_i]
        if side == "BUY":
            swept = float(sweep.low) < rl
            sweep_extreme = float(sweep.low)
            if not swept:
                continue
            # Variation A: reversal displaces away from the swept low, then retests it.
            for j in range(sweep_i + 1, min(latest, sweep_i + 4) + 1):
                bar = x.iloc[j]
                body = abs(float(bar.close) - float(bar.open))
                if (float(bar.close) > float(bar.open)
                        and float(bar.close) > rl + 0.20 * atr
                        and body >= 0.60 * atr):
                    latest_bar = x.iloc[latest]
                    touched = float(latest_bar.low) <= rl + 0.15 * atr and float(latest_bar.high) > rl
                    confirmed = float(latest_bar.close) > float(latest_bar.open) and float(latest_bar.close) > rl
                    if latest > j and touched and confirmed:
                        origins = [k for k in range(sweep_i, j) if float(x.close.iloc[k]) < float(x.open.iloc[k])]
                        origin = x.iloc[origins[-1]] if origins else sweep
                        return {"entry":float(latest_bar.close),"origin_low":float(origin.low),
                                "origin_high":float(origin.high),"sweep_extreme":sweep_extreme,
                                "atr":atr,"range_high":rh,"range_low":rl,
                                "setup":"LIQUIDITY_SWEEP_RANGE_RETEST"}
            # Variation B: sweep low, break the opposite range high, retest that high.
            breakout_i = None
            for j in range(sweep_i + 1, min(latest, sweep_i + 9) + 1):
                bar = x.iloc[j]
                body = abs(float(bar.close) - float(bar.open))
                if (float(bar.close) > rh and float(bar.close) > float(bar.open)
                        and body >= 0.60 * atr):
                    breakout_i = j
                    break
            if breakout_i is not None and latest > breakout_i:
                bar = x.iloc[latest]
                touched = float(bar.low) <= rh + 0.15 * atr and float(bar.high) >= rh - 0.10 * atr
                confirmed = float(bar.close) > float(bar.open) and float(bar.close) > rh
                if touched and confirmed:
                    origins = [k for k in range(sweep_i + 1, breakout_i)
                               if float(x.close.iloc[k]) < float(x.open.iloc[k])]
                    origin = x.iloc[origins[-1]] if origins else sweep
                    return {"entry":float(bar.close),"origin_low":float(origin.low),
                            "origin_high":float(origin.high),"sweep_extreme":sweep_extreme,
                            "atr":atr,"range_high":rh,"range_low":rl,
                            "setup":"LIQUIDITY_SWEEP_OPPOSITE_RANGE_BREAK_RETEST"}
        else:
            swept = float(sweep.high) > rh
            sweep_extreme = float(sweep.high)
            if not swept:
                continue
            # Variation A: reversal displaces away from swept high, then retests it.
            for j in range(sweep_i + 1, min(latest, sweep_i + 4) + 1):
                bar = x.iloc[j]
                body = abs(float(bar.close) - float(bar.open))
                if (float(bar.close) < float(bar.open)
                        and float(bar.close) < rh - 0.20 * atr
                        and body >= 0.60 * atr):
                    latest_bar = x.iloc[latest]
                    touched = float(latest_bar.high) >= rh - 0.15 * atr and float(latest_bar.low) < rh
                    confirmed = float(latest_bar.close) < float(latest_bar.open) and float(latest_bar.close) < rh
                    if latest > j and touched and confirmed:
                        origins = [k for k in range(sweep_i, j) if float(x.close.iloc[k]) > float(x.open.iloc[k])]
                        origin = x.iloc[origins[-1]] if origins else sweep
                        return {"entry":float(latest_bar.close),"origin_low":float(origin.low),
                                "origin_high":float(origin.high),"sweep_extreme":sweep_extreme,
                                "atr":atr,"range_high":rh,"range_low":rl,
                                "setup":"LIQUIDITY_SWEEP_RANGE_RETEST"}
            # Variation B: sweep high, break the opposite range low, retest that low.
            breakout_i = None
            for j in range(sweep_i + 1, min(latest, sweep_i + 9) + 1):
                bar = x.iloc[j]
                body = abs(float(bar.close) - float(bar.open))
                if (float(bar.close) < rl and float(bar.close) < float(bar.open)
                        and body >= 0.60 * atr):
                    breakout_i = j
                    break
            if breakout_i is not None and latest > breakout_i:
                bar = x.iloc[latest]
                touched = float(bar.high) >= rl - 0.15 * atr and float(bar.low) <= rl + 0.10 * atr
                confirmed = float(bar.close) < float(bar.open) and float(bar.close) < rl
                if touched and confirmed:
                    origins = [k for k in range(sweep_i + 1, breakout_i)
                               if float(x.close.iloc[k]) > float(x.open.iloc[k])]
                    origin = x.iloc[origins[-1]] if origins else sweep
                    return {"entry":float(bar.close),"origin_low":float(origin.low),
                            "origin_high":float(origin.high),"sweep_extreme":sweep_extreme,
                            "atr":atr,"range_high":rh,"range_low":rl,
                            "setup":"LIQUIDITY_SWEEP_OPPOSITE_RANGE_BREAK_RETEST"}
    return None



def _double_break_origin_zone(x: pd.DataFrame, sweep_i: int, break_i: int,
                               side: str, atr: float) -> dict | None:
    """Use one meaningful opposing candle, or combine adjacent tiny indecision candles.

    Tiny bars are grouped only when consecutive immediately before displacement.
    Their combined OHLC defines one cluster zone; they are never treated as
    several separate origin candles.
    """
    start = sweep_i + 1
    end = break_i
    if end <= start:
        return None
    tail_start = end
    while tail_start > start:
        row = x.iloc[tail_start - 1]
        body = abs(float(row.close) - float(row.open))
        full_range = float(row.high) - float(row.low)
        if body <= 0.20 * atr and full_range <= 0.65 * atr:
            tail_start -= 1
        else:
            break
    if end - tail_start >= 2:
        cluster = x.iloc[tail_start:end]
        cluster_open = float(cluster.open.iloc[0])
        cluster_close = float(cluster.close.iloc[-1])
        directional = (cluster_close < cluster_open if side == "BUY"
                       else cluster_close > cluster_open)
        if directional:
            return {
                "low": float(cluster.low.min()),
                "high": float(cluster.high.max()),
                "body_low": min(cluster_open, cluster_close),
                "body_high": max(cluster_open, cluster_close),
                "time": str(x.index[tail_start]),
            }

    if side == "BUY":
        candidates = [i for i in range(start, end)
                      if float(x.close.iloc[i]) < float(x.open.iloc[i])]
    else:
        candidates = [i for i in range(start, end)
                      if float(x.close.iloc[i]) > float(x.open.iloc[i])]
    if not candidates:
        return None
    i = candidates[-1]
    row = x.iloc[i]
    body_low, body_high = sorted((float(row.open), float(row.close)))
    return {"low": float(row.low), "high": float(row.high),
            "body_low": body_low, "body_high": body_high,
            "time": str(x.index[i])}


def find_double_break_origin_retest(df: pd.DataFrame, side: str,
                                    range_bars: int = 12,
                                    max_pattern_bars: int = 36,
                                    max_sweep_to_break_bars: int = 10) -> dict | None:
    """Detect the approved two-sided range sweep, opposite-edge break and exact
    origin-body retest. The sweep may occur several completed candles before
    the breakout; the consolidation range is measured before the sweep.

    BUY: sweep below range low and close back inside; later break above range
    high; use the last bearish candle between sweep and bullish displacement as
    the origin; alert only when the latest completed candle retests that body
    and closes bullish above it. SELL is the exact inverse.
    """
    if side not in ("BUY", "SELL") or len(df) < range_bars + 12:
        return None
    x = df.reset_index(drop=True)
    latest = len(x) - 1
    first_break = max(range_bars + 2, latest - max_pattern_bars)

    for break_i in range(latest - 1, first_break - 1, -1):
        bar = x.iloc[break_i]
        atr = float(indicators(x.iloc[:break_i + 1])["atr"].iloc[-1])
        if not pd.notna(atr) or atr <= 0:
            continue

        # Search a multi-candle window for the sweep. For each candidate sweep,
        # build the range only from candles that existed before that sweep.
        sweep_first = max(range_bars, break_i - max_sweep_to_break_bars)
        for sweep_i in range(break_i - 1, sweep_first - 1, -1):
            base = x.iloc[sweep_i - range_bars:sweep_i]
            if len(base) != range_bars:
                continue
            rh, rl = float(base.high.max()), float(base.low.min())
            if rh <= rl or rh - rl > 4.0 * atr:
                continue

            sweep = x.iloc[sweep_i]
            if side == "BUY":
                swept = float(sweep.low) < rl
                broke = (float(bar.close) > rh and float(bar.close) > float(bar.open)
                         and abs(float(bar.close) - float(bar.open)) >= 0.50 * atr)
                if not (swept and broke):
                    continue

                origin = _double_break_origin_zone(x, sweep_i, break_i, side, atr)
                if origin is None:
                    continue
                body_low, body_high = origin["body_low"], origin["body_high"]
                last = x.iloc[latest]
                touched = float(last.low) <= body_high and float(last.high) >= body_low
                # A retest candle need not close above the previous breakout
                # candle; it must reclaim the origin body with bullish intent.
                confirmed = (float(last.close) > float(last.open)
                             and float(last.close) > body_high)
                if not (latest > break_i and touched and confirmed):
                    continue
                return {"entry":body_high,"origin_low":origin["low"],
                        "origin_high":origin["high"],"origin_body_low":body_low,
                        "origin_body_high":body_high,"origin_time":origin["time"],
                        "sweep_extreme":float(x.low.iloc[sweep_i:break_i+1].min()),
                        "atr":atr,"range_high":rh,"range_low":rl,
                        "setup":"DOUBLE_BREAK_ORIGIN_RETEST"}

            else:
                swept = float(sweep.high) > rh
                broke = (float(bar.close) < rl and float(bar.close) < float(bar.open)
                         and abs(float(bar.close) - float(bar.open)) >= 0.50 * atr)
                if not (swept and broke):
                    continue

                origin = _double_break_origin_zone(x, sweep_i, break_i, side, atr)
                if origin is None:
                    continue
                body_low, body_high = origin["body_low"], origin["body_high"]
                last = x.iloc[latest]
                touched = float(last.high) >= body_low and float(last.low) <= body_high
                confirmed = (float(last.close) < float(last.open)
                             and float(last.close) < body_low)
                if not (latest > break_i and touched and confirmed):
                    continue
                return {"entry":body_low,"origin_low":origin["low"],
                        "origin_high":origin["high"],"origin_body_low":body_low,
                        "origin_body_high":body_high,"origin_time":origin["time"],
                        "sweep_extreme":float(x.high.iloc[sweep_i:break_i+1].max()),
                        "atr":atr,"range_high":rh,"range_low":rl,
                        "setup":"DOUBLE_BREAK_ORIGIN_RETEST"}
    return None


def find_consolidation_break_origin_retest(df: pd.DataFrame, side: str,
                                               range_bars: int = 18,
                                               max_pattern_bars: int = 48) -> dict | None:
    """Single setup only: range-edge break, reversal/displacement, exact origin retest.

    BUY: price sweeps below a clear consolidation low and closes back above it;
    bullish displacement follows; the last bearish candle immediately before
    that push is the origin. Alert only on a later bullish retest of its body.
    SELL is the exact inverse at the consolidation high.
    """
    if side not in ("BUY", "SELL") or len(df) < range_bars + 10:
        return None
    x = df.reset_index(drop=True)
    latest = len(x) - 1
    first = max(range_bars + 2, latest - max_pattern_bars)

    for push_i in range(latest - 1, first, -1):
        push = x.iloc[push_i]
        atr = float(indicators(x.iloc[:push_i + 1])["atr"].iloc[-1])
        if not pd.notna(atr) or atr <= 0:
            continue
        body = abs(float(push.close) - float(push.open))
        if body < 0.60 * atr:
            continue

        sweep_first = max(range_bars, push_i - 12)
        for sweep_i in range(push_i - 2, sweep_first - 1, -1):
            base = x.iloc[sweep_i - range_bars:sweep_i]
            if len(base) != range_bars:
                continue
            range_high, range_low = float(base.high.max()), float(base.low.min())
            if range_high <= range_low or range_high - range_low > 4.0 * atr:
                continue
            sweep = x.iloc[sweep_i]

            if side == "BUY":
                swept = float(sweep.low) < range_low and float(sweep.close) > range_low
                reversed = (float(push.close) > float(push.open)
                            and float(push.close) > float(x.high.iloc[sweep_i + 1:push_i].max()))
                origins = [j for j in range(sweep_i + 1, push_i)
                           if float(x.close.iloc[j]) < float(x.open.iloc[j])]
            else:
                swept = float(sweep.high) > range_high and float(sweep.close) < range_high
                reversed = (float(push.close) < float(push.open)
                            and float(push.close) < float(x.low.iloc[sweep_i + 1:push_i].min()))
                origins = [j for j in range(sweep_i + 1, push_i)
                           if float(x.close.iloc[j]) > float(x.open.iloc[j])]
            if not swept or not reversed or not origins:
                continue

            origin_i = origins[-1]
            origin = x.iloc[origin_i]
            body_low, body_high = sorted((float(origin.open), float(origin.close)))
            last = x.iloc[latest]
            touched = float(last.low) <= body_high and float(last.high) >= body_low
            confirmed = ((float(last.close) > float(last.open) and float(last.close) > body_high)
                         if side == "BUY" else
                         (float(last.close) < float(last.open) and float(last.close) < body_low))
            if latest <= push_i or not touched or not confirmed:
                continue

            return {
                "entry": body_high if side == "BUY" else body_low,
                "origin_low": float(origin.low), "origin_high": float(origin.high),
                "origin_body_low": body_low, "origin_body_high": body_high,
                "origin_time": str(df.index[origin_i]),
                "sweep_extreme": float(x.low.iloc[sweep_i:push_i + 1].min()) if side == "BUY"
                                 else float(x.high.iloc[sweep_i:push_i + 1].max()),
                "atr": atr, "range_high": range_high, "range_low": range_low,
                "setup": "CONSOLIDATION_BREAK_ORIGIN_RETEST",
            }
    return None


def timeframe_direction(df: pd.DataFrame) -> str | None:
    """Require a clear EMA21/EMA50 trend on a completed higher-timeframe chart."""
    if len(df) < 55:
        return None
    close = pd.to_numeric(df.close, errors="coerce")
    ema21 = close.ewm(span=21, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    last = float(close.iloc[-1])
    fast, slow = float(ema21.iloc[-1]), float(ema50.iloc[-1])
    if fast > slow and last > fast:
        return "BUY"
    if fast < slow and last < fast:
        return "SELL"
    return None


def make_signal(m5: pd.DataFrame, m15: pd.DataFrame | None = None,
                h1: pd.DataFrame | None = None, pip: float = 0.0001) -> dict | None:
    """Trade only the user's M5 two-sided sweep -> opposite break -> origin retest."""
    if len(m5) < 210 or pip <= 0:
        return None

    # Direction comes from the actual M5 sequence, not a higher-timeframe filter.
    pattern = None
    direction = None
    for candidate in ("SELL", "BUY"):
        for range_bars in (18, 24, 12, 30):
            pattern = find_double_break_origin_retest(
                m5, candidate, range_bars=range_bars,
                max_pattern_bars=48, max_sweep_to_break_bars=10
            )
            if pattern is not None:
                direction = candidate
                break
        if pattern is not None:
            break
    if pattern is None or direction is None:
        return None

    atr = float(pattern["atr"])
    entry = float(pattern["entry"])
    buffer = max(0.25 * atr, 1.0 * pip)
    if direction == "BUY":
        stop = confirmed_swing(m5, "LOW", lookback=36) - buffer
        risk, target = entry - stop, entry + MIN_RR * (entry - stop)
    else:
        stop = confirmed_swing(m5, "HIGH", lookback=36) + buffer
        risk, target = stop - entry, entry - MIN_RR * (stop - entry)
    # Keep a valid structural stop and positive risk; do not reject the setup
    # using arbitrary minimum/maximum ATR or pip-distance filters.
    if risk <= 0:
        return None

    return {
        "direction": direction, "signal_close": entry, "stop": float(stop),
        "target": float(target), "risk_pips": round(risk / pip, 1),
        "rr": MIN_RR, "atr": atr, "setup": pattern["setup"],
        "origin_low": float(pattern["origin_low"]),
        "origin_high": float(pattern["origin_high"]),
        "origin_body_low": float(pattern["origin_body_low"]),
        "origin_body_high": float(pattern["origin_body_high"]),
        "origin_time": pattern.get("origin_time", ""),
        "range_high": float(pattern["range_high"]),
        "range_low": float(pattern["range_low"]),
    }
