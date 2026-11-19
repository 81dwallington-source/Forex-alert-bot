import pandas as pd
from strategy import confirmed_swing, valid_stop_distance, find_double_break_origin_retest, make_signal, indicators

def sample_frame(n=260, slope=0.0002, start=1.0):
    close = [start + i*slope for i in range(n)]
    return pd.DataFrame({"open":close,"high":[x+0.0001 for x in close],"low":[x-0.0001 for x in close],"close":close})

def _pattern(side):
    rows = [{"open":1.1000,"high":1.1005,"low":1.0995,"close":1.1000} for _ in range(30)]
    if side == "BUY":
        rows.extend([
            {"open":1.1000,"high":1.1002,"low":1.0990,"close":1.0998},
            {"open":1.0998,"high":1.1001,"low":1.0997,"close":1.1000},
            {"open":1.1002,"high":1.1004,"low":1.0998,"close":1.1000},
            {"open":1.1000,"high":1.1013,"low":1.0999,"close":1.1012},
            {"open":1.1012,"high":1.1014,"low":1.1010,"close":1.1013},
            {"open":1.1000,"high":1.1004,"low":1.0999,"close":1.10025},
        ])
    else:
        rows.extend([
            {"open":1.1000,"high":1.1010,"low":1.0998,"close":1.1002},
            {"open":1.1002,"high":1.1003,"low":1.0999,"close":1.1000},
            {"open":1.0998,"high":1.1002,"low":1.0996,"close":1.1000},
            {"open":1.1000,"high":1.1001,"low":1.0987,"close":1.0988},
            {"open":1.0988,"high":1.0990,"low":1.0986,"close":1.0987},
            {"open":1.1000,"high":1.1001,"low":1.0996,"close":1.09975},
        ])
    return pd.DataFrame(rows)

def test_indicators_are_atr_only_not_ema_based():
    out = indicators(sample_frame())
    assert "atr" in out.columns
    assert not any(c.startswith("ema") for c in out.columns)

def test_confirmed_swing_uses_two_right_bars():
    frame = pd.DataFrame({"high":[1.02,1.03,1.04,1.05,1.06,1.07,1.08,1.09,1.10],"low":[1.00,0.99,0.98,0.97,0.90,0.96,0.97,0.98,0.99]})
    assert confirmed_swing(frame,"LOW",lookback=9) == 0.90

def test_stop_filter_rejects_tight_and_wide_stops():
    assert not valid_stop_distance(0.044,0.01,0.10)
    assert valid_stop_distance(0.080,0.01,0.10)
    assert not valid_stop_distance(0.40,0.01,0.10)

def test_double_break_buy_uses_origin_body_retest():
    result = find_double_break_origin_retest(_pattern("BUY"),"BUY")
    assert result is not None and result["setup"] == "DOUBLE_BREAK_ORIGIN_RETEST"
    assert result["origin_body_low"] == 1.1000 and result["origin_body_high"] == 1.1002
    assert result["entry"] == result["origin_body_high"]

def test_double_break_sell_uses_inverse_origin_body_retest():
    result = find_double_break_origin_retest(_pattern("SELL"),"SELL")
    assert result is not None and result["setup"] == "DOUBLE_BREAK_ORIGIN_RETEST"
    assert result["origin_body_low"] == 1.0998 and result["origin_body_high"] == 1.1000
    assert result["entry"] == result["origin_body_low"]

def test_ema_or_trend_alignment_alone_never_creates_signal():
    frame = sample_frame()
    assert make_signal(frame,frame,frame,0.0001) is None

def test_missing_higher_timeframes_reject_signal():
    frame = sample_frame()
    assert make_signal(frame, pip=0.0001) is None

def test_stop_distance_enforces_eight_pip_floor_and_three_atr_ceiling():
    pip, atr = 0.0001, 0.001
    assert not valid_stop_distance(7.9 * pip, pip, atr)
    assert valid_stop_distance(8.0 * pip, pip, atr)
    assert not valid_stop_distance(3.01 * atr, pip, atr)
