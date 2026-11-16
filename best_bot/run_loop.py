"""Keep Best Bot scanning and receiving Telegram chart screenshots.

The runner scans every five minutes and checks Telegram for incoming chart photos
between scans. GitHub Actions remains a best-effort hosted runner, not a 24/7 service.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from live_bot import scan
from photo_review import poll_telegram_photos

INTERVAL_SECONDS = 5 * 60
PHOTO_POLL_SECONDS = 5
MAX_RUNTIME_SECONDS = 340 * 60  # leave room inside GitHub's six-hour job limit


def main() -> None:
    deadline = time.monotonic() + MAX_RUNTIME_SECONDS
    next_scan = time.monotonic()
    scan_number = 0

    while time.monotonic() < deadline:
        scan_number += 1
        print(
            f"Loop scan {scan_number} starting at "
            f"{datetime.now(timezone.utc).isoformat()}",
            flush=True,
        )
        try:
            scan()
        except Exception as exc:
            print(f"Scan loop caught {type(exc).__name__}: {exc}", flush=True)

        # Poll photos throughout the wait instead of only once per five-minute scan.
        next_scan += INTERVAL_SECONDS
        while time.monotonic() < deadline:
            try:
                received = poll_telegram_photos()
                if received:
                    print(f"Reviewed {received} Telegram screenshot(s)", flush=True)
            except Exception as exc:
                print(f"Telegram photo polling caught {type(exc).__name__}: {exc}", flush=True)

            now = time.monotonic()
            if now >= next_scan:
                break
            time.sleep(min(PHOTO_POLL_SECONDS, next_scan - now, deadline - now))
        if next_scan < time.monotonic():
            next_scan = time.monotonic()

    print(f"Scan loop finished after {scan_number} scans", flush=True)


if __name__ == "__main__":
    main()
