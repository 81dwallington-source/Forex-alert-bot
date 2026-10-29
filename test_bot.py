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
        self.assertEqual(bot.STRUCTURE_LOOKBACK_CANDLES, 6)
        self.assertEqual(bot.STRUCTURE_STOP_BUFFER_ATR, 0.15)
        self.assertEqual(bot.MAX_STOP_DISTANCE_ATR, 3.0)

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
