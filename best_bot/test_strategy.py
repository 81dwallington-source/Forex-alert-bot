import pandas as pd
from strategy import indicators, trend


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
    assert trend(sample_frame(100)) is None
