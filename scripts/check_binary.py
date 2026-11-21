#!/usr/bin/env python3
"""Dependency-free exact test of C++ results before Parquet serialization.

This is an additional check, not a replacement for the official Parquet checks.
"""
import csv
import datetime as dt
import importlib.util
from pathlib import Path
import struct
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("runner", ROOT / "run.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def read_dictionary(folder, name):
    offsets = (folder / (name + ".offsets")).read_bytes()
    data = (folder / (name + ".bytes")).read_bytes()
    offsets = [x[0] for x in struct.iter_unpack("<Q", offsets)]
    return [data[a:b].decode("utf-8") for a, b in zip(offsets, offsets[1:])]


def read_results(folder):
    users = read_dictionary(folder, "people")
    cats = read_dictionary(folder, "categories")
    campaigns = read_dictionary(folder, "campaigns")
    channels = read_dictionary(folder, "channels")
    epoch = dt.date(1970, 1, 1)
    user_rows, campaign_rows = [], []
    for path in folder.glob("user-*.bin"):
        for person, day, ns, ne, revenue, top in struct.iter_unpack("<IiQQQI4x", path.read_bytes()):
            user_rows.append((users[person], str(epoch + dt.timedelta(days=day)), ns, ne, revenue,
                              None if top == 0xffffffff else cats[top]))
    for campaign, day, ns, np, revenue, assists in struct.iter_unpack("<IiQQQQ", (folder / "campaign.bin").read_bytes()):
        campaign_rows.append((campaigns[campaign], channels[campaign], str(epoch + dt.timedelta(days=day)),
                              ns, np, revenue, assists))
    return {"user_daily": user_rows, "campaign_daily": campaign_rows}


def expected(folder):
    result = {}
    for table, integers in [("user_daily", {2,3,4}), ("campaign_daily", {3,4,5,6})]:
        with open(folder / (table + ".csv"), newline="") as f:
            reader = csv.reader(f)
            next(reader)
            result[table] = [tuple(int(x) if i in integers else (None if table=="user_daily" and i==5 and x=="" else x)
                                   for i, x in enumerate(row)) for row in reader]
    return result


def sorted_rows(rows):
    return sorted(rows, key=lambda r: tuple("" if x is None else str(x) for x in r))


def main():
    fixtures = Path(sys.argv[1] if len(sys.argv)>1 else ROOT / "fixtures")
    failed = []
    for case in sorted(fixtures.iterdir()):
        if not case.is_dir():
            continue
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            runner.run_core(case / "input", output)
            actual, exp = read_results(output), expected(case / "expected")
            ok = all(sorted_rows(actual[t]) == sorted_rows(exp[t]) for t in exp)
            if not ok:
                failed.append(case.name)
                print("FAIL", case.name, "expected", exp, "actual", actual)
            else:
                print("PASS", case.name)
    print("Failed:", failed)
    return bool(failed)


if __name__ == "__main__":
    sys.exit(main())
