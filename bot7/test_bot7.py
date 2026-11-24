"""Offline tests for Bot 7 risk and signal mechanics."""
import importlib.util
from pathlib import Path
import pandas as pd

spec=importlib.util.spec_from_file_location("bot7",Path(__file__).with_name("live_bot.py"))
bot=importlib.util.module_from_spec(spec); spec.loader.exec_module(bot)

def test_pip_sizes():
    assert bot.pip("EURUSD")==0.0001
    assert bot.pip("USDJPY")==0.01

def test_stop_is_structural_and_correct_side():
    idx=pd.date_range("2026-01-01",periods=50,freq="5min",tz="UTC")
    f=pd.DataFrame({"open":[1.1]*50,"high":[1.101]*50,"low":[1.099]*50,"close":[1.1]*50},index=idx)
    f.iloc[25,f.columns.get_loc("low")]=1.095
    out=bot.swing_stop(f,"BUY",1.1,0.0002)
    assert out is not None and out[0]<1.095 and out[1]<1.1

def test_no_stop_if_no_confirmed_pivot():
    idx=pd.date_range("2026-01-01",periods=50,freq="5min",tz="UTC")
    f=pd.DataFrame({"open":[1.1]*50,"high":[1.101]*50,"low":[1.099]*50,"close":[1.1]*50},index=idx)
    assert bot.swing_stop(f,"BUY",1.1,0.0002) is None


def test_higher_timeframe_trend_direction():
    buy = pd.Series({"close":1.12,"ema21":1.11,"ema50":1.10,"ema200":1.08})
    sell = pd.Series({"close":1.07,"ema21":1.08,"ema50":1.09,"ema200":1.11})
    mixed = pd.Series({"close":1.12,"ema21":1.11,"ema50":1.12,"ema200":1.08})
    assert bot.trend_direction(buy) == "BUY"
    assert bot.trend_direction(sell) == "SELL"
    assert bot.trend_direction(mixed) is None


def test_higher_timeframes_must_agree():
    buy = pd.Series({"close":1.12,"ema21":1.11,"ema50":1.10,"ema200":1.08})
    sell = pd.Series({"close":1.07,"ema21":1.08,"ema50":1.09,"ema200":1.11})
    assert bot.trend_direction(buy) != bot.trend_direction(sell)
