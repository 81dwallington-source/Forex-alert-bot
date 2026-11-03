"""Continuous Render worker for the Forex Alert Bot.

Runs one complete scan, then waits until five minutes after that scan started.
The single loop prevents overlapping scans in this process.
"""
import time
from datetime import datetime, timezone
from pathlib import Path

import Forex_alert as bot

DATA_DIR = Path("/var/data")
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Keep deduplication state and the signal/outcome ledger across restarts/deploys.
bot.STATE_FILE = DATA_DIR / "signals_state.json"
bot.LOG_FILE = DATA_DIR / "signal_log.csv"

SCAN_INTERVAL_SECONDS = 300
HEARTBEAT_FILE = DATA_DIR / "worker_heartbeat.txt"


def write_heartbeat(status):
    stamp = datetime.now(timezone.utc).isoformat()
    HEARTBEAT_FILE.write_text(
        f"Last worker scan (UTC): {stamp}\nStatus: {status}\n",
        encoding="utf-8",
    )


def run_forever():
    print("Forex Alert Background Worker started; scan interval: 300 seconds.", flush=True)
    while True:
        started = time.monotonic()
        try:
            print(
                f"[{datetime.now(timezone.utc).isoformat()}] Starting scheduled scan.",
                flush=True,
            )
            bot.main()
            write_heartbeat("completed")
        except Exception as exc:
            message = f"FAILED: {type(exc).__name__}: {exc}"
            print(
                f"[{datetime.now(timezone.utc).isoformat()}] Worker scan {message}",
                flush=True,
            )
            try:
                write_heartbeat(message)
            except Exception as heartbeat_exc:
                print(f"Could not write worker heartbeat: {heartbeat_exc}", flush=True)

        elapsed = time.monotonic() - started
        sleep_for = max(0, SCAN_INTERVAL_SECONDS - elapsed)
        print(
            f"Scan cycle took {elapsed:.1f}s; next scan in {sleep_for:.1f}s.",
            flush=True,
        )
        time.sleep(sleep_for)


if __name__ == "__main__":
    run_forever()
