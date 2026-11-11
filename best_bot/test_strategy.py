import pandas as pd
from strategy import confirmed_swing, indicators, trend


def sample_frame(n=260, slope=0.0002):
    close = [1.0 + i*slope for i in range(n)]
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
