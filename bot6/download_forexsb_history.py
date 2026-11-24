"""Download public forexsb.com DukasCopy-mirror bars for Bot 6 research."""
from __future__ import annotations
import argparse
import gzip
import json
import struct
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

import pandas as pd

BASE = "https://data.forexsb.com/datafeed/data/dukascopy"
INFO = "https://data.forexsb.com/datafeed/info/premium.json.gz"
PAIRS = [
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD",
    "EURGBP", "EURJPY", "EURCHF", "EURAUD", "EURCAD", "EURNZD",
    "GBPJPY", "GBPCHF", "GBPAUD", "GBPCAD", "GBPNZD",
    "AUDJPY", "AUDCHF", "AUDCAD", "AUDNZD",
    "NZDJPY", "NZDCHF", "NZDCAD", "CADJPY", "CHFJPY",
]


def get_bytes(url: str) -> bytes:
    last = None
    for attempt in range(4):
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0 Bot6Research/1.0"})
            with urlopen(req, timeout=45) as response:
                return response.read()
        except Exception as exc:
            last = exc
            if attempt < 3:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"Download failed after retries: {url}: {last}")


def read_metadata():
    raw = gzip.decompress(get_bytes(INFO))
    data = json.loads(raw)
    missing = [p for p in PAIRS if p not in data]
    if missing:
        raise RuntimeError(f"Source metadata missing symbols: {missing}")
    return data


def parse_binary(raw: bytes, meta: dict) -> pd.DataFrame:
    data = gzip.decompress(raw)
    if len(data) % 28 == 0:
        record_size = 28
    elif len(data) % 24 == 0:
        record_size = 24
    else:
        raise ValueError(f"Unexpected binary record length: {len(data)}")
    scale = int(meta["priceScale"])
    volume_scale = int(meta.get("volumeScale", 1))
    epoch = int(datetime(2000, 1, 1, tzinfo=timezone.utc).timestamp())
    rows = []
    for offset in range(0, len(data), record_size):
        if record_size == 28:
            minute, o, h, low, c, volume, _spread = struct.unpack_from("<iiiiiii", data, offset)
        else:
            minute, o, h, low, c, volume = struct.unpack_from("<iiiiii", data, offset)
        rows.append((epoch + minute * 60, o / scale, h / scale, low / scale,
                     c / scale, max(1, volume // max(1, volume_scale))))
    frame = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    frame["time"] = pd.to_datetime(frame["timestamp"], unit="s", utc=True)
    frame = frame.set_index("time").sort_index()
    frame = frame[~frame.index.duplicated(keep="last")]
    if (frame.high < frame[["open", "close", "low"]].max(axis=1)).any() or (frame.low > frame[["open", "close", "high"]].min(axis=1)).any():
        raise ValueError("Source returned invalid OHLC bars")
    return frame[["open", "high", "low", "close"]]


def write_frame(frame: pd.DataFrame, path: Path):
    out = frame.copy()
    out.insert(0, "timestamp", out.index.tz_convert("UTC").asi8 // 1_000_000)
    out.to_csv(path, index=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, choices=(1, 2, 3, 5, 7), default=7)
    parser.add_argument("--data-dir", default="historical_data")
    args = parser.parse_args()
    end = pd.Timestamp.now(tz="UTC").floor("D")
    start = end - pd.Timedelta(days=365 * args.years)
    folder = Path(args.data_dir)
    folder.mkdir(parents=True, exist_ok=True)
    metadata = read_metadata()
    print(f"Source: forexsb.com public DukasCopy mirror; {start} to {end}", flush=True)

    for pair in PAIRS:
        meta = metadata[pair]
        frames = {}
        for period, label in ((5, "M5"), (15, "M15"), (30, "M30")):
            url = f"{BASE}/{pair}{period}.lb.gz"
            frame = parse_binary(get_bytes(url), meta)
            frame = frame.loc[(frame.index >= start) & (frame.index < end)]
            if len(frame) < 500:
                raise RuntimeError(f"{pair} {label}: insufficient history ({len(frame)} bars)")
            frames[label] = frame
            if label in ("M5", "M15"):
                write_frame(frame, folder / f"{pair}_{label}.csv")
            print(f"{pair} {label}: {len(frame):,} bars", flush=True)

        # Build H1 from two complete M30 candles; do not forward-fill missing bars.
        m30 = frames["M30"]
        h1 = m30.resample("1h", label="left", closed="left").agg(
            open=("open", "first"), high=("high", "max"),
            low=("low", "min"), close=("close", "last"), count=("close", "count"))
        h1 = h1[h1["count"] == 2].drop(columns="count").dropna()
        if len(h1) < 500:
            raise RuntimeError(f"{pair} H1: insufficient derived history ({len(h1)} bars)")
        write_frame(h1, folder / f"{pair}_H1.csv")
        print(f"{pair} H1: {len(h1):,} complete bars", flush=True)

    print("All Bot 6 history files downloaded and validated.", flush=True)


if __name__ == "__main__":
    main()
