#!/usr/bin/env python3
"""C++ computes exact results; Arrow only encodes the final columns as Parquet."""
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time


def run_core(data, intermediate):
    with open(data / "manifest.json") as f:
        manifest = json.load(f)
    def epoch(value):
        return int(dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
                   .replace(tzinfo=dt.timezone.utc).timestamp()) * 1000
    executable = Path(os.environ.get("CORE_BIN", Path(__file__).parent / "core"))
    subprocess.run([str(executable.resolve()), str(data), str(intermediate),
                    str(epoch(manifest["window_start"])),
                    str(epoch(manifest["window_end"])),
                    os.environ.get("NCPU", "8"), "64"], check=True)


def write_parquet(intermediate, output):
    import numpy as np
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    pa.set_cpu_count(int(os.environ.get("NCPU", "8")))
    def dictionary(name):
        offsets = pa.memory_map(str(intermediate / (name + ".offsets")), "r").read_buffer()
        path = intermediate / (name + ".bytes")
        values = pa.memory_map(str(path), "r").read_buffer() if path.stat().st_size else pa.py_buffer(b"")
        return pa.Array.from_buffers(pa.large_string(), offsets.size // 8 - 1,
                                     [None, offsets, values])
    people, categories = dictionary("people"), dictionary("categories")
    campaigns, channels = dictionary("campaigns"), dictionary("channels")
    user_type = np.dtype({"names": ["person", "day", "sessions", "events", "revenue", "top"],
                          "formats": ["<u4", "<i4", "<u8", "<u8", "<u8", "<u4"],
                          "offsets": [0, 4, 8, 16, 24, 32], "itemsize": 40})
    campaign_type = np.dtype([("campaign", "<u4"), ("day", "<i4"),
                              ("sessions", "<u8"), ("purchasing", "<u8"),
                              ("revenue", "<u8"), ("assists", "<u8")])
    schemas = {
        "user_daily": pa.schema([("user_id", pa.string()), ("day", pa.date32()),
                                 ("sessions", pa.uint64()), ("events", pa.uint64()),
                                 ("revenue_cents", pa.uint64()), ("top_category", pa.string())]),
        "campaign_daily": pa.schema([("campaign_id", pa.string()), ("channel", pa.string()),
                                     ("day", pa.date32()), ("sessions", pa.uint64()),
                                     ("purchasing_sessions", pa.uint64()),
                                     ("revenue_cents", pa.uint64()), ("assisted_sessions", pa.uint64())]),
    }
    def strings(values, indices, nullable=False):
        mask = indices == np.uint32(0xffffffff) if nullable else None
        indices = pa.array(indices, mask=mask)
        return pc.cast(pc.take(values, indices), pa.string())
    for name, paths, dtype in [
        ("user_daily", sorted(intermediate.glob("user-*.bin")), user_type),
        ("campaign_daily", [intermediate / "campaign.bin"], campaign_type),
    ]:
        folder = output / name
        folder.mkdir(parents=True, exist_ok=True)
        # A rerun must not leave stale extra rows from an earlier output.
        for stale in folder.glob("*.parquet"):
            stale.unlink()
        with pq.ParquetWriter(folder / "part-0.parquet", schemas[name], compression="snappy") as writer:
            for path in paths:
                with open(path, "rb") as f:
                    while True:
                        a = np.fromfile(f, dtype=dtype, count=262144)
                        if not len(a):
                            break
                        dates = pa.array(a["day"], type=pa.int32()).cast(pa.date32())
                        if name == "user_daily":
                            columns = [strings(people, a["person"]), dates,
                                       pa.array(a["sessions"]), pa.array(a["events"]),
                                       pa.array(a["revenue"]), strings(categories, a["top"], True)]
                        else:
                            columns = [strings(campaigns, a["campaign"]),
                                       strings(channels, a["campaign"]), dates,
                                       pa.array(a["sessions"]), pa.array(a["purchasing"]),
                                       pa.array(a["revenue"]), pa.array(a["assists"])]
                        writer.write_table(pa.Table.from_arrays(columns, schema=schemas[name]))


def main():
    started = time.perf_counter()
    data = Path(os.environ.get("DATA_DIR", "/data")).resolve()
    output = Path(os.environ.get("OUTPUT_DIR", "/output")).resolve()
    scratch = Path(os.environ.get("SCRATCH_DIR", "/scratch")).resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="shopping-", dir=scratch) as temporary:
        intermediate = Path(temporary)
        run_core(data, intermediate)
        write_parquet(intermediate, output)
    import sys
    print(f"complete: {time.perf_counter() - started:.3f} s including Parquet", file=sys.stderr)


if __name__ == "__main__":
    main()
