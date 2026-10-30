"""Offline regression tests for the Forex alert bot. These tests never call market APIs or Telegram."""
import unittest
import csv
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch

import pandas as pd

import Forex_alert as bot


class ForexBotRegressionTests(unittest.TestCase):
    def test_pair_universe_has_no_duplicates(self):
        self.assertEqual(len(bot.PAIRS), len(set(bot.PAIRS)))
        self.assertGreaterEqual(len(bot.PAIRS), 20)

    def test_signal_log_preserves_setup_type(self):
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "signal_log.csv"
            with patch.object(bot, "LOG_FILE", log_path):
                bot.log_signal({
                    "timestamp_utc": "2026-10-30T12:00:00+00:00",
                    "pair": "GBP/USD",
                    "direction": "BUY",
                    "setup_type": "PULLBACK CONTINUATION",
                    "outcome": "UNVERIFIED_PENDING",
                })
            with log_path.open("r", newline="", encoding="utf-8") as file:
                row = next(csv.DictReader(file))
            self.assertEqual(row["setup_type"], "PULLBACK CONTINUATION")

    def test_entry_timeframe_is_five_minutes(self):
        self.assertEqual(bot.TIMEFRAME, "5m")

    def test_conservative_daily_alert_limits_are_enabled(self):
        self.assertEqual(bot.MAX_ALERTS_PER_UTC_DAY, 5)
        self.assertTrue(bot.MAX_ONE_ALERT_PER_PAIR_PER_UTC_DAY)

    def test_stop_loss_has_more_volatility_room_and_structure_buffer(self):
        self.assertEqual(bot.ATR_STOP_MULTIPLIER, 1.75)
        self.assertEqual(bot.STRUCTURE_LOOKBACK_CANDLES, 18)
        self.assertEqual(bot.STRUCTURE_STOP_BUFFER_ATR, 0.35)
        self.assertEqual(bot.MAX_STOP_DISTANCE_ATR, 3.0)

    def test_m5_ema_separation_requires_directional_gap_and_expansion(self):
        frame = pd.DataFrame([
            {"EMA9": 1.1000, "EMA21": 1.0990, "ATR": 0.0100},
            {"EMA9": 1.1010, "EMA21": 1.0990, "ATR": 0.0100},
        ])
        ok, reason = bot.m5_ema_separation_check(frame, "BUY")
        self.assertTrue(ok, reason)

    def test_m5_ema_separation_rejects_flat_or_touching_emas(self):
        frame = pd.DataFrame([
            {"EMA9": 1.1000, "EMA21": 1.1000, "ATR": 0.0100},
            {"EMA9": 1.1001, "EMA21": 1.1000, "ATR": 0.0100},
        ])
        ok, reason = bot.m5_ema_separation_check(frame, "BUY")
        self.assertFalse(ok)
        self.assertIn("separation too small", reason)

    def test_m5_ema_separation_rejects_gap_that_is_shrinking(self):
        frame = pd.DataFrame([
            {"EMA9": 1.1020, "EMA21": 1.1000, "ATR": 0.0100},
            {"EMA9": 1.1015, "EMA21": 1.1000, "ATR": 0.0100},
        ])
        ok, reason = bot.m5_ema_separation_check(frame, "BUY")
        self.assertFalse(ok)
        self.assertIn("not expanding", reason)

    def test_m5_ema_separation_supports_bearish_direction(self):
        frame = pd.DataFrame([
            {"EMA9": 1.1000, "EMA21": 1.1010, "ATR": 0.0100},
            {"EMA9": 1.0990, "EMA21": 1.1010, "ATR": 0.0100},
        ])
        ok, reason = bot.m5_ema_separation_check(frame, "SELL")
        self.assertTrue(ok, reason)



    def test_detects_bullish_ema21_pullback_and_continuation_break(self):
        rows = []
        for i in range(30):
            rows.append({"Open":1.1000,"High":1.1020,"Low":1.0990,"Close":1.1010,
                         "EMA21":1.1000,"EMA9":1.1010,"ATR":0.0020})
        rows[-3].update({"Low":1.0998,"Close":1.1002})
        rows[-2].update({"Low":1.0999,"Close":1.1003,"High":1.1010})
        rows[-1].update({"Open":1.1005,"Low":1.1004,"High":1.1020,"Close":1.1015,
                         "EMA9":1.1012,"EMA21":1.1000,"ATR":0.0030})
        ok, reason = bot.detect_pullback_continuation(pd.DataFrame(rows), "BUY")
        self.assertTrue(ok, reason)

    def test_detects_bearish_ema21_pullback_and_continuation_break(self):
        rows = []
        for i in range(30):
            rows.append({"Open":1.1000,"High":1.1010,"Low":1.0980,"Close":1.0990,
                         "EMA21":1.1000,"EMA9":1.0990,"ATR":0.0020})
        rows[-3].update({"High":1.1002,"Close":1.0998})
        rows[-2].update({"High":1.1001,"Close":1.0997,"Low":1.0990})
        rows[-1].update({"Open":1.0995,"High":1.0996,"Low":1.0980,"Close":1.0985,
                         "EMA9":1.0988,"EMA21":1.1000,"ATR":0.0030})
        ok, reason = bot.detect_pullback_continuation(pd.DataFrame(rows), "SELL")
        self.assertTrue(ok, reason)

    def test_pullback_alert_rejects_without_breakout_confirmation(self):
        rows = []
        for i in range(30):
            rows.append({"Open":1.1000,"High":1.1020,"Low":1.0990,"Close":1.1010,
                         "EMA21":1.1000,"EMA9":1.1010,"ATR":0.0020})
        rows[-3].update({"Low":1.0998,"Close":1.1002})
        rows[-2].update({"Low":1.0999,"Close":1.1003,"High":1.1010})
        rows[-1].update({"Open":1.1005,"Low":1.1004,"High":1.1010,"Close":1.1008,
                         "EMA9":1.1012,"EMA21":1.1000})
        ok, reason = bot.detect_pullback_continuation(pd.DataFrame(rows), "BUY")
        self.assertFalse(ok)
        self.assertIn("confirmed continuation candle", reason)

    def test_minimum_quality_score_is_six_of_seven(self):
        self.assertEqual(bot.MIN_SCORE, 6)

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
