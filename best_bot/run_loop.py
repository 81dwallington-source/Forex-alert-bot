"""Single scheduled market scan for Best Bot.

GitHub Actions starts this script every five minutes. The scan sends qualifying
signals through Telegram, saves its state, and exits. It does not hold a runner
open or load the optional screenshot-vision model.
"""
from __future__ import annotations

from datetime import datetime, timezone

from live_bot import scan


def main() -> None:
    started = datetime.now(timezone.utc)
    print(f"Best Bot scan starting: {started.isoformat()}", flush=True)
    sent = scan()
    finished = datetime.now(timezone.utc)
    print(
        f"Best Bot scan finished: {finished.isoformat()}; Telegram signals sent: {sent}",
        flush=True,
    )


if __name__ == "__main__":
    main()
