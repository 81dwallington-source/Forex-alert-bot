import pandas as pd
from live_bot import find_m5_bos, pip_size

def frame(rows):
    idx=pd.date_range("2026-01-01T00:00:00Z",periods=len(rows),freq="5min")
    return pd.DataFrame(rows,index=idx,columns=["open","high","low","close"],dtype=float)

def make_rows(last_high):
    highs=[1.099]*12+[1.105,1.103,1.101,1.102,1.104,1.103,1.104,last_high]
    return [[h-0.002,h,h-0.004,h-0.001] for h in highs]

def test_pip_size():
    assert pip_size("EURUSD")==0.0001
    assert pip_size("USDJPY")==0.01

def test_m5_bos_requires_close_through_confirmed_swing():
    f=frame(make_rows(1.108))
    result=find_m5_bos(f,"BUY")
    assert result is not None and result["close"]>result["level"]

def test_no_bos_if_latest_candle_does_not_close_above_swing():
    f=frame(make_rows(1.104))
    assert find_m5_bos(f,"BUY") is None
