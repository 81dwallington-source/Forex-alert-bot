import pandas as pd
from strategy import confirmed_swing, indicators, trend, m5_trend_aligned, pullback_structure_extreme, make_signal


def sample_frame(n=260, slope=0.0002, start=1.0):
    close = [start + i*slope for i in range(n)]
    return pd.DataFrame({"open":close,"high":[x+0.0001 for x in close],
                         "low":[x-0.0001 for x in close],"close":close})


def test_indicators_produce_expected_columns():
    out=indicators(sample_frame())
    assert {"ema21","ema50","ema200","atr"}.issubset(out.columns)
    assert out.iloc[-1].ema21 > out.iloc[-1].ema50 > out.iloc[-1].ema200


def test_trend_returns_direction_for_aligned_uptrend():
    assert trend(sample_frame()) == "BUY"


def test_trend_rejects_insufficient_history():
    assert trend(sample_frame(40)) is None


def test_m5_filter_requires_full_bullish_ema_alignment():
    assert m5_trend_aligned(sample_frame(), "BUY")
    assert not m5_trend_aligned(sample_frame(), "SELL")


def test_m5_filter_requires_full_bearish_ema_alignment():
    falling = sample_frame(slope=-0.0002, start=2.0)
    assert m5_trend_aligned(falling, "SELL")
    assert not m5_trend_aligned(falling, "BUY")


def test_confirmed_swing_uses_pivot_with_two_right_bars():
    frame = pd.DataFrame({
        "high": [1.02,1.03,1.04,1.05,1.06,1.07,1.08,1.09,1.10],
        "low":  [1.00,0.99,0.98,0.97,0.90,0.96,0.97,0.98,0.99],
    })
    assert confirmed_swing(frame, "LOW", lookback=9) == 0.90


def test_confirmed_swing_falls_back_to_recent_structure():
    frame = pd.DataFrame({
        "high": [1.02,1.03,1.04,1.05,1.06,1.07,1.08,1.09,1.10],
        "low":  [1.00,0.99,0.98,0.97,0.96,0.95,0.94,0.93,0.92],
    })
    assert confirmed_swing(frame, "LOW", lookback=9) == 0.93


def test_stop_filter_rejects_tight_eurjpy_style_stop():
    from strategy import valid_stop_distance
    assert not valid_stop_distance(0.044, 0.01, 0.10)
    assert valid_stop_distance(0.080, 0.01, 0.10)


def test_stop_filter_rejects_excessively_wide_stop():
    from strategy import valid_stop_distance
    assert not valid_stop_distance(0.40, 0.01, 0.10)


def test_stop_filter_rejects_invalid_inputs():
    from strategy import valid_stop_distance
    assert not valid_stop_distance(0.08, 0.0, 0.10)
    assert not valid_stop_distance(0.08, 0.01, 0.0)
    assert not valid_stop_distance(-0.08, 0.01, 0.10)


def test_staircase_confirmed_recognises_bullish_structure():
    from strategy import staircase_confirmed
    highs = [1,2,3,5,3,2,4,3,2,6,4,3,5,4,3]
    lows  = [5,4,3,2,3,4,3,4,5,3,4,5,4,2,3]
    frame = pd.DataFrame({"high": highs, "low": lows})
    assert staircase_confirmed(frame, "BUY", lookback=15)
    assert not staircase_confirmed(frame, "SELL", lookback=15)


def test_staircase_confirmed_rejects_missing_structure():
    from strategy import staircase_confirmed
    frame = pd.DataFrame({"high": range(15), "low": range(15)})
    assert not staircase_confirmed(frame, "BUY", lookback=15)
    assert not staircase_confirmed(frame, "SELL", lookback=15)


def test_sell_stop_uses_recent_pullback_high_not_small_local_pivot():
    frame = pd.DataFrame({
        "high": [1.10,1.11,1.12,1.13,1.15,1.14,1.13,1.12,1.11,1.12,1.11,1.10],
        "low":  [1.08,1.09,1.10,1.11,1.12,1.11,1.10,1.09,1.08,1.09,1.08,1.07],
    })
    assert pullback_structure_extreme(frame, "HIGH") == 1.15


def test_buy_stop_uses_recent_pullback_low_not_small_local_pivot():
    frame = pd.DataFrame({
        "high": [1.12,1.13,1.14,1.15,1.16,1.15,1.14,1.13,1.12,1.13,1.12,1.11],
        "low":  [1.10,1.09,1.08,1.07,1.05,1.06,1.07,1.08,1.09,1.08,1.09,1.10],
    })
    assert pullback_structure_extreme(frame, "LOW") == 1.05



def test_indicator_alignment_alone_never_creates_a_signal():
    # A clean EMA trend without one of the three requested chart patterns
    # must produce no trade.
    m5 = sample_frame()
    m15 = sample_frame()
    h1 = sample_frame()
    assert trend(m15) == "BUY"
    assert trend(h1) == "BUY"
    assert make_signal(m5, m15, h1, 0.0001) is None


def test_impulse_retracement_uses_full_origin_to_extreme_move():
    from strategy import impulse_retracement
    # 20% retracement of the complete move is rejected; 60% qualifies.
    assert impulse_retracement("BUY", 1.00, 1.10, 1.08) == 0.20
    assert impulse_retracement("BUY", 1.00, 1.10, 1.04) == 0.60
    assert impulse_retracement("SELL", 1.10, 1.00, 1.02) == 0.20
    assert impulse_retracement("SELL", 1.10, 1.00, 1.06) == 0.60


def test_invalid_impulse_retracement_returns_zero():
    from strategy import impulse_retracement
    assert impulse_retracement("BUY", 1.10, 1.00, 1.05) == 0.0
    assert impulse_retracement("SELL", 1.00, 1.10, 1.05) == 0.0


def test_deep_pullback_gate_rejects_shallow_tiny_or_short_retracements():
    from strategy import valid_deep_pullback
    assert not valid_deep_pullback(0.20, 0.90, 5)
    assert not valid_deep_pullback(0.50, 0.30, 5)
    assert not valid_deep_pullback(0.60, 0.90, 2)
    assert not valid_deep_pullback(0.90, 1.00, 5)


def test_deep_pullback_gate_accepts_only_full_quality_retracement():
    from strategy import valid_deep_pullback
    assert valid_deep_pullback(0.60, 0.90, 4)


def _multi_bar_sweep_pattern(side):
    # Twelve-bar compact range, then sweep, a pause, the opposing origin
    # candle, an opposite-edge displacement, and a later origin-body retest.
    rows = [{"open":1.1000,"high":1.1005,"low":1.0995,"close":1.1000}
            for _ in range(30)]
    if side == "BUY":
        rows.extend([
            {"open":1.1000,"high":1.1002,"low":1.0990,"close":1.0998},  # sweep low
            {"open":1.0998,"high":1.1001,"low":1.0997,"close":1.1000},  # pause
            {"open":1.1002,"high":1.1004,"low":1.0998,"close":1.1000},  # last bearish origin
            {"open":1.1000,"high":1.1013,"low":1.0999,"close":1.1012},  # break range high
            {"open":1.1012,"high":1.1014,"low":1.1010,"close":1.1013},
            {"open":1.1000,"high":1.1004,"low":1.0999,"close":1.10025}, # retest + bullish reject
        ])
    else:
        rows.extend([
            {"open":1.1000,"high":1.1010,"low":1.0998,"close":1.1002},  # sweep high
            {"open":1.1002,"high":1.1003,"low":1.0999,"close":1.1000},  # pause
            {"open":1.0998,"high":1.1002,"low":1.0996,"close":1.1000},  # last bullish origin
            {"open":1.1000,"high":1.1001,"low":1.0987,"close":1.0988},  # break range low
            {"open":1.0988,"high":1.0990,"low":1.0986,"close":1.0987},
            {"open":1.1000,"high":1.1001,"low":1.0996,"close":1.09975}, # retest + bearish reject
        ])
    return pd.DataFrame(rows)


def test_double_break_buy_detects_sweep_several_candles_before_breakout():
    from strategy import find_double_break_origin_retest
    result = find_double_break_origin_retest(_multi_bar_sweep_pattern("BUY"), "BUY")
    assert result is not None
    assert result["setup"] == "DOUBLE_BREAK_ORIGIN_RETEST"
    assert result["direction"] if "direction" in result else True
    assert result["origin_body_low"] == 1.1000
    assert result["origin_body_high"] == 1.1002


def test_double_break_sell_detects_sweep_several_candles_before_breakout():
    from strategy import find_double_break_origin_retest
    result = find_double_break_origin_retest(_multi_bar_sweep_pattern("SELL"), "SELL")
    assert result is not None
    assert result["setup"] == "DOUBLE_BREAK_ORIGIN_RETEST"
    assert result["origin_body_low"] == 1.0998
    assert result["origin_body_high"] == 1.1000


def test_double_break_retest_does_not_need_to_close_beyond_previous_breakout_close():
    from strategy import find_double_break_origin_retest
    frame = _multi_bar_sweep_pattern("BUY")
    assert frame.iloc[-1].close < frame.iloc[-2].close
    assert find_double_break_origin_retest(frame, "BUY") is not None
