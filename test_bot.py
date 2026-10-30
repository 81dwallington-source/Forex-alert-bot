"""Offline regression tests for the Forex alert bot. These tests never call market APIs or Telegram."""
import unittest
from datetime import datetime, timezone

import pandas as pd

import Forex_alert as bot


class ForexBotRegressionTests(unittest.TestCase):
    def test_pair_universe_has_no_duplicates(self):
        self.assertEqual(len(bot.PAIRS), len(set(bot.PAIRS)))
        self.assertGreaterEqual(len(bot.PAIRS), 20)

    def test_entry_timeframe_is_five_minutes(self):
        self.assertEqual(bot.TIMEFRAME, "5m")

    def test_conservative_daily_alert_limits_are_enabled(self):
        self.assertEqual(bot.MAX_ALERTS_PER_UTC_DAY, 5)
        self.assertTrue(bot.MAX_ONE_ALERT_PER_PAIR_PER_UTC_DAY)

    def test_stop_loss_has_more_volatility_room_and_structure_buffer(self):
        self.assertEqual(bot.ATR_STOP_MULTIPLIER, 1.5)
        self.assertEqual(bot.STRUCTURE_LOOKBACK_CANDLES, 12)
        self.assertEqual(bot.STRUCTURE_STOP_BUFFER_ATR, 0.25)
        self.assertEqual(bot.MAX_STOP_DISTANCE_ATR, 3.0)

    def test_detects_bullish_liquidity_sweep_and_reclaim(self):
        rows = [{"Open": 1.1000, "High": 1.1050, "Low": 1.0950, "Close": 1.1000} for _ in range(15)]
        rows.append({"Open": 1.0990, "High": 1.1030, "Low": 1.0940, "Close": 1.1010})
        self.assertTrue(bot.detect_liquidity_sweep(pd.DataFrame(rows), "BUY"))

    def test_detects_bearish_liquidity_sweep_and_rejection(self):
        rows = [{"Open": 1.1000, "High": 1.1050, "Low": 1.0950, "Close": 1.1000} for _ in range(15)]
        rows.append({"Open": 1.1010, "High": 1.1060, "Low": 1.0970, "Close": 1.0990})
        self.assertTrue(bot.detect_liquidity_sweep(pd.DataFrame(rows), "SELL"))

    def test_no_liquidity_sweep_when_price_does_not_reclaim(self):
        rows = [{"Open": 1.1000, "High": 1.1050, "Low": 1.0950, "Close": 1.1000} for _ in range(15)]
        rows.append({"Open": 1.0940, "High": 1.0970, "Low": 1.0930, "Close": 1.0945})
        self.assertFalse(bot.detect_liquidity_sweep(pd.DataFrame(rows), "BUY"))

    def test_fvg_requires_latest_bullish_retest_confirmation(self):
        rows = [
            {"Open": 1.1000, "High": 1.1010, "Low": 1.0990, "Close": 1.1005},
            {"Open": 1.1005, "High": 1.1020, "Low": 1.1000, "Close": 1.1015},
            {"Open": 1.1020, "High": 1.1040, "Low": 1.1030, "Close": 1.1035},
            {"Open": 1.1035, "High": 1.1040, "Low": 1.1020, "Close": 1.1030},
            {"Open": 1.1025, "High": 1.1035, "Low": 1.1015, "Close": 1.1032},
        ]
        self.assertTrue(bot.detect_fvg_retest(pd.DataFrame(rows), "BUY", atr=0.002))

    def test_fvg_requires_latest_bearish_retest_confirmation(self):
        rows = [
            {"Open": 1.1040, "High": 1.1050, "Low": 1.1030, "Close": 1.1035},
            {"Open": 1.1035, "High": 1.1040, "Low": 1.1020, "Close": 1.1025},
            {"Open": 1.1020, "High": 1.1025, "Low": 1.1000, "Close": 1.1005},
            {"Open": 1.1005, "High": 1.1020, "Low": 1.1000, "Close": 1.1010},
            {"Open": 1.1015, "High": 1.1025, "Low": 1.1005, "Close": 1.1008},
        ]
        self.assertTrue(bot.detect_fvg_retest(pd.DataFrame(rows), "SELL", atr=0.002))

    def test_fvg_rejects_old_reaction_without_latest_confirmation(self):
        rows = [
            {"Open": 1.1000, "High": 1.1010, "Low": 1.0990, "Close": 1.1005},
            {"Open": 1.1005, "High": 1.1020, "Low": 1.1000, "Close": 1.1015},
            {"Open": 1.1020, "High": 1.1040, "Low": 1.1030, "Close": 1.1035},
            {"Open": 1.1025, "High": 1.1035, "Low": 1.1015, "Close": 1.1032},
            {"Open": 1.1030, "High": 1.1040, "Low": 1.1025, "Close": 1.1035},
        ]
        self.assertFalse(bot.detect_fvg_retest(pd.DataFrame(rows), "BUY", atr=0.002))

    def test_trend_direction_buy_sell_and_unclear(self):
        self.assertEqual(
            bot.trend_direction(pd.DataFrame([{"EMA20": 1.2, "EMA50": 1.1}])),
            "BUY",
        )
        self.assertEqual(
            bot.trend_direction(pd.DataFrame([{"EMA20": 1.1, "EMA50": 1.2}])),
            "SELL",
        )
        self.assertEqual(
            bot.trend_direction(pd.DataFrame([{"EMA20": 1.1, "EMA50": 1.1}])),
            "NONE",
        )

    def test_freshness_accepts_recent_utc_candle(self):
        frame = pd.DataFrame(
            {"Close": [1.0]},
            index=pd.DatetimeIndex([pd.Timestamp.now(tz="UTC")]),
        )
        fresh, age = bot.candle_is_fresh(frame, 12)
        self.assertTrue(fresh)
        self.assertGreaterEqual(age, -1)
        self.assertLessEqual(age, 12)

    def test_freshness_rejects_old_candle(self):
        old_time = datetime.now(timezone.utc) - pd.Timedelta(minutes=30)
        frame = pd.DataFrame(
            {"Close": [1.0]},
            index=pd.DatetimeIndex([old_time]),
        )
        fresh, age = bot.candle_is_fresh(frame, 12)
        self.assertFalse(fresh)
        self.assertGreater(age, 12)

    def test_m5_freshness_measures_from_candle_close_not_open(self):
        # An M5 candle stamped 11 minutes ago closed 6 minutes ago.
        candle_open = datetime.now(timezone.utc) - pd.Timedelta(minutes=11)
        frame = pd.DataFrame(
            {"Close": [1.0]},
            index=pd.DatetimeIndex([candle_open]),
        )
        fresh, age = bot.candle_is_fresh(frame, 10, candle_duration_minutes=5)
        self.assertTrue(fresh)
        self.assertGreaterEqual(age, 5)
        self.assertLessEqual(age, 7)

    def test_m5_freshness_still_rejects_candle_closed_too_long_ago(self):
        # An M5 candle stamped 16 minutes ago closed 11 minutes ago.
        candle_open = datetime.now(timezone.utc) - pd.Timedelta(minutes=16)
        frame = pd.DataFrame(
            {"Close": [1.0]},
            index=pd.DatetimeIndex([candle_open]),
        )
        fresh, age = bot.candle_is_fresh(frame, 10, candle_duration_minutes=5)
        self.assertFalse(fresh)
        self.assertGreater(age, 10)

    def test_indicators_produce_finite_recent_values(self):
        index = pd.date_range(
            end=pd.Timestamp.now(tz="UTC"),
            periods=100,
            freq="5min",
        )
        close = pd.Series([1.10 + i * 0.0001 + (0.0003 if i % 2 else 0.0) for i in range(100)], index=index)
        frame = pd.DataFrame(
            {
                "Open": close - 0.00005,
                "High": close + 0.0002,
                "Low": close - 0.0002,
                "Close": close,
            },
            index=index,
        )
        result = bot.indicators(frame)
        self.assertFalse(result.empty)
        for column in ("EMA9", "EMA21", "EMA20", "EMA50", "RSI", "ATR"):
            self.assertIn(column, result.columns)
            self.assertTrue(pd.notna(result.iloc[-1][column]), column)
        self.assertGreater(float(result.iloc[-1]["ATR"]), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
