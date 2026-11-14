import unittest
from datetime import datetime, timezone
from unittest.mock import patch, Mock
import pandas as pd
import tempfile
from pathlib import Path
from bot3.bot3 import in_session, trend_direction, confirmed_pivots, fetch_live_quote, evaluate_trade_outcome, detect_approved_pattern, validate_m15_structure
from bot3.backtest import bid_stop_to_executable, load_m5

class Bot3Tests(unittest.TestCase):
    def test_backtest_loader_preserves_zero_range_candle_timestamps(self):
        prices = [1.1] * 3 + [1.1001 + i * 0.00001 for i in range(802)]
        frame = pd.DataFrame({
            "time": pd.date_range("2026-01-01", periods=805, freq="5min", tz="UTC").astype(str),
            "Open": prices,
            "High": [1.1] * 3 + [p + 0.0002 for p in prices[3:]],
            "Low": [1.1] * 3 + [p - 0.0002 for p in prices[3:]],
            "Close": [1.1] * 3 + [p + 0.0001 for p in prices[3:]],
        })
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bars.csv"
            frame.to_csv(path, index=False)
            loaded = load_m5(path)
        self.assertEqual(len(loaded), 805)
        self.assertEqual(loaded.attrs["raw_rows"], 805)
        self.assertEqual(loaded.attrs["flat_rows"], 3)
        self.assertEqual(loaded.attrs["flat_rows_removed"], 0)

    def test_bid_stop_converts_to_correct_executable_side(self):
        full_spread = 0.00015
        self.assertAlmostEqual(bid_stop_to_executable(1.25000, "BUY", full_spread), 1.25000)
        self.assertAlmostEqual(bid_stop_to_executable(1.25000, "SELL", full_spread), 1.25015)

    def test_bid_stop_rejects_unknown_direction(self):
        with self.assertRaises(ValueError):
            bid_stop_to_executable(1.25000, "HOLD", 0.00015)

    def test_session_uses_uk_local_time(self):
        self.assertTrue(in_session(datetime(2026,11,5,6,0,tzinfo=timezone.utc)))
        self.assertFalse(in_session(datetime(2026,11,5,23,0,tzinfo=timezone.utc)))

    def test_trend_requires_ordered_emas(self):
        close=list(range(1,81))
        frame=pd.DataFrame({"Open":close,"High":[x+1 for x in close],
            "Low":[x-1 for x in close],"Close":close},
            index=pd.date_range("2026-01-01",periods=80,freq="h",tz="UTC"))
        self.assertEqual(trend_direction(frame),"BUY")

    def test_unconfirmed_latest_pivot_is_ignored(self):
        lows=[5,4,5,6,5,4,5,6,3,4]
        frame=pd.DataFrame({"Low":lows,"High":[x+2 for x in lows]})
        pivots=confirmed_pivots(frame,"low",lookback=10,left=2,right=2)
        self.assertTrue(all(i<=len(frame)-3 for i,_ in pivots))
        self.assertNotIn((8,3.0),pivots)

    @patch("bot3.bot3.SESSION.get")
    def test_live_quote_accepts_fresh_valid_bid_ask(self, get):
        now=datetime(2026,11,5,12,0,tzinfo=timezone.utc)
        response=Mock()
        response.json.return_value={
            "marketState":"open","stale":False,"bid":1.1000,"ask":1.1002,
            "mid":1.1001,"timestamp":"2026-11-05T11:59:50Z","quoteAgeSeconds":10
        }
        get.return_value=response
        mid,stamp,bid,ask=fetch_live_quote("EURUSD",now)
        self.assertEqual((mid,bid,ask),(1.1001,1.1,1.1002))
        self.assertEqual(stamp, pd.Timestamp("2026-11-05T11:59:50Z"))

    @patch("bot3.bot3.SESSION.get")
    def test_live_quote_rejects_stale_or_closed_market(self, get):
        response=Mock()
        response.json.return_value={
            "marketState":"closed","stale":True,"bid":1.1,"ask":1.1002,
            "mid":1.1001,"timestamp":"2026-11-05T11:59:50Z","quoteAgeSeconds":10
        }
        get.return_value=response
        with self.assertRaisesRegex(ValueError,"market is closed or quote is marked stale"):
            fetch_live_quote("EURUSD",datetime(2026,11,5,12,0,tzinfo=timezone.utc))

    @patch("bot3.bot3.SESSION.get")
    def test_live_quote_rejects_old_timestamp(self, get):
        response=Mock()
        response.json.return_value={
            "marketState":"open","stale":False,"bid":1.1,"ask":1.1002,
            "mid":1.1001,"timestamp":"2026-11-05T11:55:00Z","quoteAgeSeconds":300
        }
        get.return_value=response
        with self.assertRaisesRegex(ValueError,"quote is too old"):
            fetch_live_quote("EURUSD",datetime(2026,11,5,12,0,tzinfo=timezone.utc))


    def test_outcome_tracker_records_first_target_hit(self):
        signal={"direction":"BUY","entry":1.1000,"stop":1.0990,"target":1.1010,
                "candle_time":"2026-11-05T10:00:00Z"}
        candles=pd.DataFrame(
            {"High":[1.1005,1.1012],"Low":[1.0995,1.1002],"Close":[1.1002,1.1011]},
            index=pd.to_datetime(["2026-11-05T10:05:00Z","2026-11-05T10:10:00Z"]))
        outcome,stamp,detail=evaluate_trade_outcome(signal,candles)
        self.assertEqual(outcome,"WIN")
        self.assertEqual(stamp,"2026-11-05T10:10:00+00:00")

    def test_outcome_tracker_marks_same_candle_sl_tp_ambiguous(self):
        signal={"direction":"SELL","entry":1.1000,"stop":1.1010,"target":1.0990,
                "candle_time":"2026-11-05T10:00:00Z"}
        candles=pd.DataFrame(
            {"High":[1.1012],"Low":[1.0988],"Close":[1.1001]},
            index=pd.to_datetime(["2026-11-05T10:05:00Z"]))
        outcome,stamp,detail=evaluate_trade_outcome(signal,candles)
        self.assertEqual(outcome,"AMBIGUOUS")
        self.assertIn("Both stop and target",detail)

    def test_outcome_tracker_does_not_reuse_signal_candle(self):
        signal={"direction":"BUY","entry":1.1000,"stop":1.0990,"target":1.1010,
                "candle_time":"2026-11-05T10:00:00Z"}
        candles=pd.DataFrame(
            {"High":[1.1012],"Low":[1.0995],"Close":[1.1011]},
            index=pd.to_datetime(["2026-11-05T10:00:00Z"]))
        outcome,stamp,detail=evaluate_trade_outcome(signal,candles)
        self.assertEqual(outcome,"")

    def test_liquidity_sweep_requires_sweep_rejection_and_close_break(self):
        n=60
        frame=pd.DataFrame({
            "Open":[1.005]*n,"High":[1.006]*n,"Low":[1.004]*n,
            "Close":[1.0055]*n,"ATR":[0.001]*n,
            "EMA21":[1.005]*n,"EMA20":[1.005]*n,"EMA50":[1.004]*n,
        },index=pd.date_range("2026-11-05",periods=n,freq="5min",tz="UTC"))
        frame.loc[frame.index[56],"Low"]=1.000
        frame.loc[frame.index[59],["Open","High","Low","Close"]]=[1.001,1.007,0.999,1.0068]
        result=detect_approved_pattern(frame,"BUY")
        self.assertIsNotNone(result)
        self.assertEqual(result["setup_type"],"LIQUIDITY SWEEP")

    @patch("bot3.bot3.detect_approved_pattern")
    def test_m15_must_confirm_same_approved_setup(self, detect):
        detect.return_value = {"setup_type":"LIQUIDITY SWEEP","evidence":"M15 sweep"}
        frame = pd.DataFrame({"Open":[1.0]*60,"High":[1.1]*60,
            "Low":[0.9]*60,"Close":[1.0]*60},
            index=pd.date_range("2026-11-05",periods=60,freq="15min",tz="UTC"))
        self.assertIsNone(validate_m15_structure(frame,"BUY","IMPULSE + ORIGIN RETEST"))

    @patch("bot3.bot3.detect_approved_pattern")
    def test_m15_matching_setup_is_accepted(self, detect):
        expected = {"setup_type":"LIQUIDITY SWEEP","evidence":"M15 sweep"}
        detect.return_value = expected
        frame = pd.DataFrame({"Open":[1.0]*60,"High":[1.1]*60,
            "Low":[0.9]*60,"Close":[1.0]*60},
            index=pd.date_range("2026-11-05",periods=60,freq="15min",tz="UTC"))
        self.assertEqual(validate_m15_structure(frame,"BUY","LIQUIDITY SWEEP"),expected)

    def test_unrelated_m5_candle_is_not_an_approved_setup(self):
        n=60
        frame=pd.DataFrame({
            "Open":[1.005]*n,"High":[1.006]*n,"Low":[1.004]*n,
            "Close":[1.0055]*n,"ATR":[0.001]*n,
            "EMA21":[1.005]*n,"EMA20":[1.005]*n,"EMA50":[1.004]*n,
        },index=pd.date_range("2026-11-05",periods=n,freq="5min",tz="UTC"))
        self.assertIsNone(detect_approved_pattern(frame,"BUY"))


    def _two_sided_breakout_frame(self, include_both_sides=True):
        n = 70
        frame = pd.DataFrame({
            "Open":[1.000]*n,"High":[1.001]*n,"Low":[0.999]*n,
            "Close":[1.0005]*n,"ATR":[0.002]*n,
            "EMA21":[1.000]*n,"EMA20":[1.000]*n,"EMA50":[0.999]*n,
        },index=pd.date_range("2026-11-05",periods=n,freq="5min",tz="UTC"))
        # Same compact eight-candle range.
        frame.loc[frame.index[47:55],"High"] = 1.002
        frame.loc[frame.index[47:55],"Low"] = 0.998
        if include_both_sides:
            # First break below the range, then decisive reclaim above it.
            frame.loc[frame.index[57],["Open","High","Low","Close"]] = [1.000,1.001,0.995,0.996]
            frame.loc[frame.index[59],["Open","High","Low","Close"]] = [1.003,1.004,0.998,0.999]
            frame.loc[frame.index[60],["Open","High","Low","Close"]] = [0.999,1.005,0.998,1.004]
            # Return to the exact origin candle (index 59) and confirm BUY.
            frame.loc[frame.index[68],["Open","High","Low","Close"]] = [1.000,1.002,0.999,1.001]
            frame.loc[frame.index[69],["Open","High","Low","Close"]] = [1.001,1.006,1.000,1.005]
        else:
            # Only one side breaks: this must never qualify as the pattern.
            frame.loc[frame.index[60],["Open","High","Low","Close"]] = [1.000,1.005,0.999,1.004]
            frame.loc[frame.index[68],["Open","High","Low","Close"]] = [1.000,1.002,0.999,1.001]
            frame.loc[frame.index[69],["Open","High","Low","Close"]] = [1.001,1.006,1.000,1.005]
        return frame

    def test_two_sided_consolidation_requires_both_edges_and_origin_retest(self):
        frame = self._two_sided_breakout_frame(True)
        result = detect_approved_pattern(frame, "BUY")
        self.assertIsNotNone(result)
        self.assertEqual(result["setup_type"], "TWO-SIDED CONSOLIDATION BREAKOUT + ORIGIN RETEST")
        self.assertIn("both ways", result["evidence"])
        self.assertIn(str(frame.index[59]), result["evidence"])

    def test_single_sided_consolidation_breakout_is_rejected(self):
        frame = self._two_sided_breakout_frame(False)
        result = detect_approved_pattern(frame, "BUY")
        self.assertIsNone(result)

    def test_two_sided_breakout_requires_aggressive_breaks_on_both_edges(self):
        frame = self._two_sided_breakout_frame(True)
        # Weaken the first (SELL-side) boundary break so it no longer meets
        # the minimum real-body / displacement requirement.
        frame.loc[frame.index[57], ["Open","High","Low","Close"]] = [
            1.000, 1.001, 0.995, 0.9975
        ]
        result = detect_approved_pattern(frame, "BUY")
        self.assertIsNone(result)

if __name__=="__main__": unittest.main()
