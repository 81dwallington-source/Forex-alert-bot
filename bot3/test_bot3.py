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

if __name__=="__main__": unittest.main()
