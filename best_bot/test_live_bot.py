import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import live_bot


class LiveBotTests(unittest.TestCase):
    def test_pip_size(self):
        self.assertEqual(live_bot.pip_size("GBPJPY"), 0.01)
        self.assertEqual(live_bot.pip_size("EURUSD"), 0.0001)

    def test_all_27_pairs_unique(self):
        self.assertEqual(len(live_bot.PAIRS), 27)
        self.assertEqual(len(set(live_bot.PAIRS)), 27)

    def test_stale_candle_rejected(self):
        old = pd.DataFrame(
            {"open":[1.0],"high":[1.1],"low":[0.9],"close":[1.0]},
            index=pd.DatetimeIndex([pd.Timestamp.now(tz="UTC")-pd.Timedelta(hours=1)])
        )
        with self.assertRaises(ValueError):
            live_bot.validate_freshness("EURUSD", old, "5m", 10)

    def test_live_bot_requires_telegram_secrets(self):
        with patch.object(live_bot, "TOKEN", ""), patch.object(live_bot, "CHAT_ID", ""):
            with self.assertRaises(RuntimeError):
                live_bot.scan()


if __name__ == "__main__":
    unittest.main(verbosity=2)
