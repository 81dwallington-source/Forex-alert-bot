import unittest
from datetime import datetime, timezone
from unittest.mock import patch, Mock
import pandas as pd
from bot3.bot3 import in_session, trend_direction, confirmed_pivots, fetch_live_quote

class Bot3Tests(unittest.TestCase):
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

if __name__=="__main__": unittest.main()
