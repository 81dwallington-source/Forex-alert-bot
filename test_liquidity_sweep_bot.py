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
