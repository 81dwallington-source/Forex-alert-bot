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
    def test_performance_report_separates_public_estimates_from_verified_trades(self):
        rows = [
            {"outcome": "TP_HIT_PUBLIC_CANDLE_ESTIMATE", "planned_rr": "1.5", "outcome_candle_time": "2026-10-30T10:00:00+00:00"},
            {"outcome": "SL_HIT_PUBLIC_CANDLE_ESTIMATE", "planned_rr": "1.5", "outcome_candle_time": "2026-10-30T10:05:00+00:00"},
            {"outcome": "UNVERIFIED_PENDING"},
            {"outcome": "AMBIGUOUS_BOTH_HIT_PUBLIC_CANDLES"},
            {"outcome": "VERIFIED_WIN"},
        ]
        report = bot.build_performance_report(rows)
        self.assertIn("1 TP / 1 SL", report)
        self.assertIn("estimated hit rate=50.0%", report)
        self.assertIn("avg/expectancy=+0.25R", report)
        self.assertIn("max drawdown=1.00R", report)
        self.assertIn("pending=1; ambiguous=1", report)
        self.assertIn("Broker-verified trade records: 1", report)
        self.assertIn("Not broker fills", report)

    def test_performance_report_does_not_invent_results_when_empty(self):
        report = bot.build_performance_report([])
        self.assertIn("no resolved estimates yet", report)
        self.assertIn("Broker-verified trade records: 0", report)

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

    def test_public_feed_is_explicitly_not_broker_verified(self):
        self.assertIn("biquote", bot.DATA_SOURCE.lower())
        self.assertIn("not user-broker verified", bot.DATA_SOURCE.lower())
        self.assertNotIn("yahoo bootstrap", bot.DATA_SOURCE.lower())

    def test_public_feed_mode_uses_biquote_without_oanda_dependency(self):
        import inspect
        source = inspect.getsource(bot.main)
        self.assertNotIn("OANDA_API_TOKEN", source)
        self.assertIn("analyse_pair", source)
        self.assertIn("biquote.io/api/", inspect.getsource(bot.download_data))
        self.assertNotIn("download_yahoo_data", inspect.getsource(bot.download_data))

    def test_detects_bullish_breakout_retest_and_confirmation(self):
        rows = [{"Open":1.1000,"High":1.1050,"Low":1.0950,"Close":1.1020,
                 "ATR":0.0020} for _ in range(30)]
        rows[-6].update({"Close":1.1060})
        rows[-2].update({"Low":1.1048,"Close":1.1052})
        rows[-1].update({"Open":1.1055,"High":1.1080,"Low":1.1052,"Close":1.1075})
        ok, reason = bot.detect_breakout_retest(pd.DataFrame(rows), "BUY")
        self.assertTrue(ok, reason)

    def test_breakout_retest_rejects_without_retest(self):
        rows = [{"Open":1.1000,"High":1.1050,"Low":1.0950,"Close":1.1020,
                 "ATR":0.0020} for _ in range(30)]
        rows[-6].update({"Close":1.1060})
        rows[-2].update({"Low":1.1070,"Close":1.1080})
        rows[-1].update({"Open":1.1080,"High":1.1090,"Low":1.1075,"Close":1.1088})
        ok, reason = bot.detect_breakout_retest(pd.DataFrame(rows), "BUY")
        self.assertFalse(ok)
        self.assertIn("retested", reason)

    def test_detects_bullish_support_rejection(self):
        rows = [{"Open":1.1000,"High":1.1050,"Low":1.0950,"Close":1.1020,
                 "ATR":0.0020} for _ in range(25)]
        rows[-1].update({"Open":1.0950,"High":1.0972,"Low":1.0948,"Close":1.0968})
        ok, reason = bot.detect_support_resistance_rejection(pd.DataFrame(rows), "BUY")
        self.assertTrue(ok, reason)

    def test_support_rejection_rejects_weak_bounce(self):
        rows = [{"Open":1.1000,"High":1.1050,"Low":1.0950,"Close":1.1020,
                 "ATR":0.0020} for _ in range(25)]
        rows[-1].update({"Open":1.0950,"High":1.0958,"Low":1.0948,"Close":1.0955})
        ok, reason = bot.detect_support_resistance_rejection(pd.DataFrame(rows), "BUY")
        self.assertFalse(ok)
        self.assertIn("decisive rejection", reason)

    def test_signal_candle_quality_requires_strong_bullish_close(self):
        candle = {"Open": 1.1000, "High": 1.1020, "Low": 1.0990, "Close": 1.1018}
        ok, reason = bot.signal_candle_quality_check(candle, "BUY")
        self.assertTrue(ok, reason)

    def test_signal_candle_quality_rejects_weak_body(self):
        candle = {"Open": 1.1000, "High": 1.1020, "Low": 1.0990, "Close": 1.1002}
        ok, reason = bot.signal_candle_quality_check(candle, "BUY")
        self.assertFalse(ok)
        self.assertIn("body too weak", reason)

    def test_signal_candle_quality_rejects_close_away_from_extreme(self):
        candle = {"Open": 1.0995, "High": 1.1020, "Low": 1.0990, "Close": 1.1010}
        ok, reason = bot.signal_candle_quality_check(candle, "BUY")
        self.assertFalse(ok)
        self.assertIn("directional extreme", reason)

    def test_signal_candle_quality_supports_bearish_close(self):
        candle = {"Open": 1.1020, "High": 1.1030, "Low": 1.0990, "Close": 1.0992}
        ok, reason = bot.signal_candle_quality_check(candle, "SELL")
        self.assertTrue(ok, reason)

    def test_entry_timeframe_is_five_minutes(self):
        self.assertEqual(bot.TIMEFRAME, "5m")

    def test_conservative_daily_alert_limits_are_enabled(self):
        self.assertEqual(bot.MAX_ALERTS_PER_UTC_DAY, 5)
        self.assertTrue(bot.MAX_ONE_ALERT_PER_PAIR_PER_UTC_DAY)

    def test_stop_loss_has_more_volatility_room_and_structure_buffer(self):
        self.assertEqual(bot.ATR_STOP_MULTIPLIER, 1.75)
        self.assertEqual(bot.STRUCTURE_LOOKBACK_CANDLES, 36)
        self.assertEqual(bot.STRUCTURE_PIVOT_LEFT_BARS, 2)
        self.assertEqual(bot.STRUCTURE_PIVOT_RIGHT_BARS, 2)
        self.assertEqual(bot.STRUCTURE_STOP_BUFFER_ATR, 0.25)
        self.assertEqual(bot.MAX_STOP_DISTANCE_ATR, 4.5)

    def test_m5_swing_levels_only_use_confirmed_pivots(self):
        rows = [{"High": 1.1010, "Low": 1.0990} for _ in range(12)]
        rows[5] = {"High": 1.1050, "Low": 1.0980}
        rows[8] = {"High": 1.1010, "Low": 1.0960}
        swings = bot.detect_m5_swing_levels(pd.DataFrame(rows), lookback=12)
        self.assertEqual(swings["high"], 1.1050)
        self.assertEqual(swings["low"], 1.0960)

    def test_m5_swing_levels_do_not_treat_unconfirmed_latest_bars_as_pivots(self):
        rows = [{"High": 1.1010, "Low": 1.0990} for _ in range(8)]
        rows[-1] = {"High": 1.1100, "Low": 1.0900}
        swings = bot.detect_m5_swing_levels(pd.DataFrame(rows), lookback=8)
        self.assertIsNone(swings["high"])
        self.assertIsNone(swings["low"])

    def test_m5_ema_separation_requires_directional_gap_and_expansion(self):
        frame = pd.DataFrame([
            {"EMA9": 1.1000, "EMA21": 1.0990, "ATR": 0.0100},
            {"EMA9": 1.1010, "EMA21": 1.0991, "ATR": 0.0100},
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
            {"EMA9": 1.0990, "EMA21": 1.1009, "ATR": 0.0100},
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

    def test_only_pullback_and_breakout_retest_setups_are_approved(self):
        self.assertEqual(bot.ALLOWED_SETUP_TYPES, ("PULLBACK CONTINUATION", "BREAKOUT RETEST"))
        self.assertEqual(bot.MIN_HTF_EMA_GAP_ATR, 0.08)
        self.assertEqual((bot.BUY_RSI_MIN, bot.BUY_RSI_MAX), (52, 65))
        self.assertEqual((bot.SELL_RSI_MIN, bot.SELL_RSI_MAX), (35, 48))

    def test_minimum_quality_score_requires_all_six_quality_checks(self):
        self.assertEqual(bot.MIN_SCORE, 6)

    def test_ema50_is_context_not_a_hard_entry_veto(self):
        buy = pd.DataFrame([{"Close": 1.1020, "EMA21": 1.1010, "EMA50": 1.1030, "ATR": 0.0020}])
        sell = pd.DataFrame([{"Close": 1.0980, "EMA21": 1.0990, "EMA50": 1.0970, "ATR": 0.0020}])
        self.assertEqual(bot.m5_entry_side_check(buy, "BUY")[0], True)
        self.assertEqual(bot.m5_entry_side_check(sell, "SELL")[0], True)

    def test_m5_entry_allows_a_nearby_ema21_pullback_from_either_side(self):
        buy_pullback = pd.DataFrame([{"Close": 1.1000, "EMA21": 1.1010, "ATR": 0.0020}])
        sell_pullback = pd.DataFrame([{"Close": 1.1000, "EMA21": 1.0990, "ATR": 0.0020}])
        self.assertEqual(bot.m5_entry_side_check(buy_pullback, "BUY")[0], True)
        self.assertEqual(bot.m5_entry_side_check(sell_pullback, "SELL")[0], True)

    def test_m5_entry_rejects_price_too_far_from_ema21(self):
        buy = pd.DataFrame([{"Close": 1.0980, "EMA21": 1.1010, "ATR": 0.0020}])
        sell = pd.DataFrame([{"Close": 1.1020, "EMA21": 1.0990, "ATR": 0.0020}])
        self.assertEqual(bot.m5_entry_side_check(buy, "BUY")[0], False)
        self.assertEqual(bot.m5_entry_side_check(sell, "SELL")[0], False)

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


    def test_higher_timeframes_must_both_align_with_signal(self):
        self.assertEqual(
            bot.higher_timeframe_alignment("BUY", "BUY", "BUY"),
            (True, "M15 and H1 aligned"),
        )
        ok, reason = bot.higher_timeframe_alignment("BUY", "SELL", "BUY")
        self.assertFalse(ok)
        self.assertIn("H1 trend (SELL)", reason)

    def test_neutral_higher_timeframe_rejects_signal(self):
        ok, reason = bot.higher_timeframe_alignment("BUY", "NONE", "BUY")
        self.assertFalse(ok)
        self.assertIn("H1 trend (NONE)", reason)

    def test_m15_must_match_proposed_direction(self):
        ok, reason = bot.higher_timeframe_alignment("SELL", "BUY", "BUY")
        self.assertFalse(ok)
        self.assertIn("M15 trend (SELL)", reason)



if __name__ == "__main__":
    unittest.main(verbosity=2)
