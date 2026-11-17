"""One scheduled Best Bot scan plus one Telegram screenshot-inbox check.

GitHub Actions schedules this script every five minutes. Each invocation exits
after saving the scan state; it does not occupy a runner for hours.
"""
from __future__ import annotations

from datetime import datetime, timezone

from live_bot import scan
from photo_review import poll_telegram_photos


def main() -> None:
    started = datetime.now(timezone.utc)
    print(f"Best Bot scheduled scan starting: {started.isoformat()}", flush=True)

    # A scan failure is allowed to fail the Actions job so it is visible.
    sent = scan()
    print(f"Scheduled market scan finished; signals sent: {sent}", flush=True)

    # Telegram screenshots are checked on every five-minute invocation.
    received = poll_telegram_photos()
    print(f"Telegram screenshot reviews completed: {received}", flush=True)

    finished = datetime.now(timezone.utc)
    print(f"Best Bot run finished: {finished.isoformat()}", flush=True)


if __name__ == "__main__":
    main()
