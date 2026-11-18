"""Continuous five-minute scanner loop for Best Bot.

GitHub Actions may delay scheduled starts. Keep one runner alive and scan every
five minutes; the workflow concurrency group keeps at most one newer run queued
to take over when this loop finishes.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from live_bot import scan

SCAN_INTERVAL_SECONDS = 5 * 60
RUN_FOR_SECONDS = 55 * 60


def main() -> None:
    deadline = time.monotonic() + RUN_FOR_SECONDS
    scan_number = 0
    while True:
        scan_number += 1
        started_monotonic = time.monotonic()
        started = datetime.now(timezone.utc)
        print(
            f"Best Bot scan {scan_number} starting: {started.isoformat()}",
            flush=True,
        )
        try:
            sent = scan()
            print(
                f"Best Bot scan {scan_number} finished; Telegram signals sent: {sent}",
                flush=True,
            )
        except Exception as exc:
            print(
                f"Best Bot scan {scan_number} failed: {type(exc).__name__}: {exc}",
                flush=True,
            )

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        elapsed = time.monotonic() - started_monotonic
        sleep_for = min(max(0, SCAN_INTERVAL_SECONDS - elapsed), remaining)
        if sleep_for <= 0:
            continue
        print(f"Next Best Bot scan in {sleep_for:.0f} seconds", flush=True)
        time.sleep(sleep_for)

    print("Best Bot continuous scan window complete.", flush=True)


if __name__ == "__main__":
    main()
