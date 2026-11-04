import unittest
import pandas as pd
from liquidity_sweep_bot import detect_sweep

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

if __name__=="__main__":
    unittest.main()
