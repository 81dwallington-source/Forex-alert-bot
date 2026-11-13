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


    def test_trade_log_updates_pending_row_instead_of_duplicating(self):
        import csv
        import tempfile
        from pathlib import Path

        original = live_bot.LOG_PATH
        try:
            with tempfile.TemporaryDirectory() as folder:
                live_bot.LOG_PATH = Path(folder) / "signals.csv"
                trade = {
                    "signal_time_utc":"2026-11-11T17:20:00+00:00",
                    "pair":"EURJPY","direction":"BUY","entry_reference":177.3365,
                    "stop_loss":177.29221,"take_profit":177.40294,
                    "risk_pips":4.4,"planned_rr":1.5,"outcome":"PENDING",
                    "data_source":"test"
                }
                live_bot.append_log(trade)
                resolved = dict(trade, outcome="WIN", outcome_time_utc="2026-11-11T18:00:00+00:00",
                                outcome_note="Target touched")
                live_bot.append_log(resolved)
                with live_bot.LOG_PATH.open(newline="", encoding="utf-8") as f:
                    rows = list(csv.DictReader(f))
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["outcome"], "WIN")
                self.assertEqual(rows[0]["stop_loss"], "177.29221")
                self.assertEqual(rows[0]["take_profit"], "177.40294")
        finally:
            live_bot.LOG_PATH = original

    def test_live_bot_requires_telegram_secrets(self):
        with patch.object(live_bot, "TOKEN", ""), patch.object(live_bot, "CHAT_ID", ""):
            with self.assertRaises(RuntimeError):
                live_bot.scan()


    def test_every_alert_displays_the_recognised_pattern():
        message = live_bot.format_signal(
            "AUDNZD", "SELL", 1.22591, 1.22679, 1.22457, 8.8,
            pd.Timestamp("2026-11-13T07:30:00Z"), 1.5, 0.001,
            "IMPULSE_ORIGIN_RETEST", 1.2250, 1.2258,
        )
        self.assertIn("PATTERN CONFIRMED: IMPULSE_ORIGIN_RETEST", message)
        self.assertIn("Origin candle body:", message)


if __name__ == "__main__":
    unittest.main(verbosity=2)

