import unittest
import pandas as pd
from execution_backtest import simulate, report


class ExecutionBacktestTests(unittest.TestCase):
    def setUp(self):
        self.signals = pd.DataFrame([{
            "time": "2026-01-01T00:00:00Z", "pair": "EURUSD",
            "direction": "BUY", "entry": 1.1000, "stop": 1.0990, "target": 1.1010
        }])

    def candles(self, rows):
        return pd.DataFrame([
            {"time": f"2026-01-01T00:{minute:02d}:00Z", "pair": "EURUSD",
             "open": o, "high": h, "low": l, "close": c}
            for minute, o, h, l, c in rows
        ])

    def test_enters_on_next_candle_not_signal_close(self):
        bars = self.candles([(5, 1.1002, 1.1004, 1.1001, 1.1003),
                             (10, 1.1003, 1.1012, 1.1002, 1.1011)])
        result = simulate(self.signals, bars)
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0].entry_fill, 1.1002)
        self.assertEqual(result[0].outcome, "WIN")

    def test_same_candle_stop_and_target_is_stop_first(self):
        bars = self.candles([(5, 1.1000, 1.1012, 1.0988, 1.1005)])
        result = simulate(self.signals, bars)
        self.assertEqual(result[0].outcome, "LOSS")
        self.assertEqual(result[0].exit_reason, "STOP")

    def test_spread_and_slippage_reduce_result(self):
        bars = self.candles([(5, 1.1000, 1.1012, 1.0998, 1.1011)])
        clean = simulate(self.signals, bars)[0]
        costly = simulate(self.signals, bars, spread=0.0002, slippage=0.00005)[0]
        self.assertLess(costly.r_multiple, clean.r_multiple)

    def test_signal_without_later_candle_is_not_filled(self):
        bars = self.candles([(0, 1.1000, 1.1002, 1.0998, 1.1000)])
        self.assertEqual(simulate(self.signals, bars), [])

    def test_report_metrics(self):
        bars = self.candles([(5, 1.1000, 1.1012, 1.0998, 1.1011)])
        summary = report(simulate(self.signals, bars))
        self.assertEqual(summary["trades"], 1)
        self.assertEqual(summary["wins"], 1)
        self.assertEqual(summary["net_r"], 1.0)


if __name__ == "__main__":
    unittest.main()
