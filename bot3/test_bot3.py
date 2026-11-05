import unittest
from datetime import datetime, timezone
from unittest.mock import patch, Mock
import pandas as pd
from bot3.bot3 import in_session, trend_direction, confirmed_pivots, fetch_live_quote, evaluate_trade_outcome
from bot3.backtest import midpoint_stop_to_executable

class Bot3Tests(unittest.TestCase):
    def test_midpoint_stop_converts_to_correct_executable_side(self):
        half_spread = 0.000075
        self.assertAlmostEqual(midpoint_stop_to_executable(1.25000, "BUY", half_spread), 1.249925)
        self.assertAlmostEqual(midpoint_stop_to_executable(1.25000, "SELL", half_spread), 1.250075)

    def test_midpoint_stop_rejects_unknown_direction(self):
        with self.assertRaises(ValueError):
            midpoint_stop_to_executable(1.25000, "HOLD", 0.000075)

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

if __name__=="__main__": unittest.main()
