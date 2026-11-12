"""Keep the Best Bot scanning on one GitHub Actions runner.

GitHub scheduled events can be delayed. This loop performs an immediate scan,
then starts another scan on five-minute boundaries for up to 5h40m. The
workflow saves the signal/outcome ledger when the loop exits.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from live_bot import scan

INTERVAL_SECONDS = 5 * 60
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
            # A temporary feed/API failure must not kill the whole scan loop.
            print(f"Scan loop caught {type(exc).__name__}: {exc}", flush=True)

        next_scan += INTERVAL_SECONDS
        now = time.monotonic()
        if next_scan < now:
            # If a scan took longer than five minutes, start the next one now.
            next_scan = now
        sleep_for = min(next_scan - now, max(0.0, deadline - now))
        if sleep_for > 0:
            print(f"Next scan in {sleep_for:.0f} seconds", flush=True)
            time.sleep(sleep_for)

    print(f"Scan loop finished after {scan_number} scans", flush=True)


if __name__ == "__main__":
    main()
