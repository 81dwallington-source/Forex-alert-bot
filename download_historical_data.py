#!/usr/bin/env python3
"""Download Dukascopy bid candles and build validated M15 candles from M5.

Dukascopy data is an independent historical reference, NOT MT5 broker data.
Requires Node.js 18+ and dukascopy-node CLI via npx.
"""
from __future__ import annotations

import argparse
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

PAIRS = ["eurusd", "gbpusd", "usdjpy", "usdchf", "usdcad", "audusd", "nzdusd"]


def run_download(instrument: str, timeframe: str, start: str, end: str, out: Path) -> None:
    stem = f"{instrument.upper()}_{timeframe.upper()}"
    command = [
        "npx", "--yes", "dukascopy-node",
        "-i", instrument, "-from", start, "-to", end,
        "-t", timeframe.lower(), "-f", "csv",
        "-dir", str(out), "-fn", stem,
        "--cache", "--retries", "3", "--batch-size", "10", "--batch-pause", "1500",
    ]
    print("Running:", " ".join(command), flush=True)
    subprocess.run(command, check=True, timeout=4 * 60 * 60)
    path = out / f"{stem}.csv"
    if not path.exists() or path.stat().st_size < 100:
        raise RuntimeError(f"Expected download missing or empty: {path}")
    df = pd.read_csv(path)
    required = {"timestamp", "open", "high", "low", "close"}
    if not required.issubset({c.lower() for c in df.columns}):
        raise RuntimeError(f"Unexpected Dukascopy CSV columns in {path}: {list(df.columns)}")
    print(f"Validated {path}: {len(df):,} rows", flush=True)


def build_m15(m5_path: Path) -> None:
    df = pd.read_csv(m5_path)
    df.columns = [c.lower() for c in df.columns]
    idx = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    frame = df.assign(_time=idx).set_index("_time")[["open", "high", "low", "close"]]

    # Only complete 15-minute buckets: three M5 candles are required.
    grouped = frame.resample("15min", label="left", closed="left").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        count=("close", "count"),
    )
    grouped = grouped[grouped["count"] == 3].drop(columns="count").dropna()

    # Pandas 3 preserves the datetime index's native unit in .asi8. Explicitly
    # convert to nanoseconds first, otherwise second-resolution values can be
    # written as if they were milliseconds and collapse the timeline near 1970.
    naive_utc = grouped.index.tz_convert("UTC").tz_localize(None)
    timestamp_ms = naive_utc.as_unit("ns").asi8 // 1_000_000

    if len(timestamp_ms) < 100:
        raise RuntimeError(f"Too few complete M15 candles in {m5_path}: {len(timestamp_ms)}")
    if int(timestamp_ms.min()) < 1_000_000_000_000:
        raise RuntimeError(
            f"M15 timestamp conversion failed for {m5_path}: expected Unix milliseconds, "
            f"got first value {int(timestamp_ms[0])}"
        )
    if (pd.Series(timestamp_ms).diff().dropna() < 900_000).any():
        raise RuntimeError(f"Generated M15 candles overlap or are less than 15 minutes apart: {m5_path}")

    grouped.insert(0, "timestamp", timestamp_ms)
    out = m5_path.with_name(m5_path.stem.replace("_M5", "_M15") + ".csv")
    grouped.reset_index(drop=True).to_csv(out, index=False)
    print(
        f"Built and validated {out}: {len(grouped):,} rows; "
        f"{pd.to_datetime(timestamp_ms[0], unit='ms', utc=True)} to "
        f"{pd.to_datetime(timestamp_ms[-1], unit='ms', utc=True)}",
        flush=True,
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--years", type=int, default=1, choices=[1, 2])
    p.add_argument("--data-dir", default="historical_data")
    args = p.parse_args()

    end_dt = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start_dt = end_dt - timedelta(days=365 * args.years)
    start, end = start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d")
    out = Path(args.data_dir)
    out.mkdir(parents=True, exist_ok=True)

    print(
        f"Historical source: Dukascopy public feed (not MT5 broker data); UTC {start} to {end}",
        flush=True,
    )
    for pair in PAIRS:
        run_download(pair, "m5", start, end, out)
        run_download(pair, "h1", start, end, out)
        build_m15(out / f"{pair.upper()}_M5.csv")

    print("All 7 pairs downloaded and validated.", flush=True)


if __name__ == "__main__":
    main()
