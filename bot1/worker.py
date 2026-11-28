"""Keep the Bot 1 signal scanner alive and scan on a five-minute cadence.

GitHub-hosted jobs have a six-hour maximum runtime, so this worker exits
before that limit. The workflow restarts it on a six-hour schedule.
"""
from __future__ import annotations

import time
import traceback

from live_bot import scan

INTERVAL_SECONDS = 5 * 60
MAX_RUNTIME_SECONDS = 340 * 60  # leave time for validation and state commit


def main() -> None:
    started = time.monotonic()
    next_scan = started
    scan_number = 0

    while time.monotonic() - started < MAX_RUNTIME_SECONDS:
        scan_number += 1
        print(f"Starting five-minute scan #{scan_number}", flush=True)
        try:
            scan()
        except Exception:
            print("Unexpected scan-level error; worker will continue.", flush=True)
            traceback.print_exc()

        next_scan += INTERVAL_SECONDS
        remaining_runtime = MAX_RUNTIME_SECONDS - (time.monotonic() - started)
        sleep_for = min(next_scan - time.monotonic(), remaining_runtime)
        if sleep_for > 0:
            time.sleep(sleep_for)
        else:
            # If a scan took longer than five minutes, start the next one now.
            next_scan = time.monotonic()


if __name__ == "__main__":
    main()
