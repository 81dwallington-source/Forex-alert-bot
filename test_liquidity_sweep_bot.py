import unittest
from unittest.mock import patch
from datetime import datetime, timezone
import pandas as pd
from liquidity_sweep_bot import detect_sweep, build_signal

class LiquiditySweepTests(unittest.TestCase):
    def test_no_signal_without_enough_candles(self):
        df=pd.DataFrame({"Open":[1.0]*10,"High":[1.01]*10,"Low":[0.99]*10,"Close":[1.0]*10},
                        index=pd.date_range("2026-01-01",periods=10,freq="5min",tz="UTC"))
        self.assertIsNone(detect_sweep(df))

    def test_no_signal_when_no_sweep(self):
        n=40
        df=pd.DataFrame({"Open":[1.0]*n,"High":[1.001]*n,"Low":[0.999]*n,"Close":[1.0]*n},
                        index=pd.date_range("2026-01-01",periods=n,freq="5min",tz="UTC"))
        self.assertIsNone(detect_sweep(df))


    def test_rejects_rolling_high_without_confirmed_swing_pivot(self):
        n = 45
        highs = [1.1000 + i * 0.00002 for i in range(n)]
        lows = [h - 0.00030 for h in highs]
        opens = [h - 0.00012 for h in highs]
        closes = [h - 0.00008 for h in highs]
        # A bearish candle pokes above the rising rolling high but no prior
        # confirmed pivot exists for it to sweep.
        opens[-5] = highs[-6] + 0.00002
        highs[-5] = highs[-6] + 0.00020
        lows[-5] = opens[-5] - 0.00035
        closes[-5] = highs[-6] - 0.00003
        # Follow-through and a later pullback must not rescue a fake sweep.
        opens[-4], highs[-4], lows[-4], closes[-4] = 1.1000, 1.1001, 1.0994, 1.0995
        opens[-3], highs[-3], lows[-3], closes[-3] = 1.0995, 1.0998, 1.0993, 1.0996
        opens[-2], highs[-2], lows[-2], closes[-2] = 1.0996, 1.0999, 1.0994, 1.0997
        opens[-1], highs[-1], lows[-1], closes[-1] = 1.0997, 1.0999, 1.0995, 1.0996
        df = pd.DataFrame({"Open":opens,"High":highs,"Low":lows,"Close":closes},
                          index=pd.date_range("2026-01-01",periods=n,freq="5min",tz="UTC"))
        self.assertIsNone(detect_sweep(df))

    def test_h1_opposite_direction_rejects_setup(self):
        now = pd.Timestamp.now(tz="UTC")
        m5 = pd.DataFrame({"Open":[1.0], "High":[1.01], "Low":[0.99], "Close":[1.0]},
                          index=pd.DatetimeIndex([now - pd.Timedelta(minutes=5)]))
        m15 = pd.DataFrame({"Close":[1.0]})
        h1 = pd.DataFrame({"Close":[1.0]})
        setup = {"direction":"BUY", "entry":1.1000, "sweep_extreme":1.0990,
                 "atr":0.0010, "candle_time":(now - pd.Timedelta(minutes=5)).isoformat(),
                 "swept_level":1.0995, "pullback_level":1.1000}
        with (
            patch("liquidity_sweep_bot.detect_sweep", return_value=setup),
            patch("liquidity_sweep_bot.htf_direction", side_effect=["BUY", "SELL"]),
        ):
            signal, reason = build_signal(m5, m15, h1)
        self.assertIsNone(signal)
        self.assertIn("H1 trend (SELL)", reason)

    def test_both_m15_and_h1_aligned_accept_setup(self):
        now = pd.Timestamp.now(tz="UTC")
        m5 = pd.DataFrame({"Open":[1.0], "High":[1.01], "Low":[0.99], "Close":[1.0]},
                          index=pd.DatetimeIndex([now - pd.Timedelta(minutes=5)]))
        m15 = pd.DataFrame({"Close":[1.0]})
        h1 = pd.DataFrame({"Close":[1.0]})
        setup = {"direction":"BUY", "entry":1.1000, "sweep_extreme":1.0990,
                 "atr":0.0010, "candle_time":(now - pd.Timedelta(minutes=5)).isoformat(),
                 "swept_level":1.0995, "pullback_level":1.1000}
        with (
            patch("liquidity_sweep_bot.detect_sweep", return_value=setup),
            patch("liquidity_sweep_bot.htf_direction", side_effect=["BUY", "BUY"]),
        ):
            signal, reason = build_signal(m5, m15, h1)
        self.assertIsNotNone(signal, reason)
        self.assertTrue(signal["h1_agrees"])

    def test_m15_opposite_direction_still_rejects_setup(self):
        now = pd.Timestamp.now(tz="UTC")
        m5 = pd.DataFrame({"Open":[1.0], "High":[1.01], "Low":[0.99], "Close":[1.0]},
                          index=pd.DatetimeIndex([now - pd.Timedelta(minutes=5)]))
        m15 = pd.DataFrame({"Close":[1.0]})
        h1 = pd.DataFrame({"Close":[1.0]})
        setup = {"direction":"BUY", "entry":1.1000, "sweep_extreme":1.0990,
                 "atr":0.0010, "candle_time":(now - pd.Timedelta(minutes=5)).isoformat(),
                 "swept_level":1.0995, "pullback_level":1.1000}
        with (
            patch("liquidity_sweep_bot.detect_sweep", return_value=setup),
            patch("liquidity_sweep_bot.htf_direction", side_effect=["SELL", "BUY"]),
        ):
            signal, reason = build_signal(m5, m15, h1)
        self.assertIsNone(signal)
        self.assertIn("M15 trend", reason)

if __name__=="__main__":
    unittest.main()
