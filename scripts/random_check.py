#!/usr/bin/env python3
"""Independent, deliberately slow oracle: BFS identities + brute-force assists.

The oracle does not use the C++ DSU, dedup hash buckets, or interval algorithm.
No third-party Python packages are needed for this computation-layer check.
"""
from collections import defaultdict
import datetime as dt
import json
from pathlib import Path
import random
import re
import subprocess
import sys
import tempfile

from check_binary import ROOT, runner, read_results, sorted_rows

DAY = 86400000
WEEK = 7 * DAY
UTC = dt.timezone.utc
EPOCH = dt.datetime(1970, 1, 1, tzinfo=UTC)


def iso(ms):
    return (EPOCH + dt.timedelta(milliseconds=ms)).strftime("%Y-%m-%dT%H:%M:%SZ")


def strict_rows(folder, columns):
    pattern = re.compile(rb'(?:[^"\n,]*|"[^"\n,]*")')
    for file in sorted(folder.glob("*.csv")):
        # splitlines() would incorrectly treat CR and several Unicode bytes as
        # row terminators; the challenge has LF-only records.
        lines = file.read_bytes().split(b"\n")[1:]
        for line in lines:
            fields = line.split(b",")
            if len(fields) != columns or not all(pattern.fullmatch(x) for x in fields):
                continue
            yield tuple((x[1:-1] if x.startswith(b'"') else x).decode() for x in fields)


def stamp(s, start, end):
    try:
        if re.fullmatch(r"[0-9]+", s):
            value = int(s)
        else:
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(Z|[+-][0-9]{2}:[0-9]{2})", s):
                return None
            # strptime validates calendar values. Offset limits are explicit.
            value = dt.datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)
            value = (value - EPOCH) // dt.timedelta(milliseconds=1)
            if s[19] != "Z":
                h, m = int(s[20:22]), int(s[23:25])
                if h > 23 or m > 59:
                    return None
                value -= (h * 60 + m) * 60000 * (1 if s[19] == "+" else -1)
        return value if start <= value < end else None
    except (ValueError, OverflowError):
        return None


def reference(data):
    manifest = json.loads((data / "manifest.json").read_text())
    start = stamp(manifest["window_start"], 0, 10**18)
    end = stamp(manifest["window_end"], 0, 10**18)
    graph = defaultdict(set)
    for a, b in strict_rows(data / "links", 2):
        if a and b:
            graph[a].add(b)
            graph[b].add(a)
    names = {}
    for first in graph:
        if first in names:
            continue
        todo, component = [first], {first}
        while todo:
            for other in graph[todo.pop()]:
                if other not in component:
                    component.add(other)
                    todo.append(other)
        name = min(component, key=lambda x: x.encode())
        names.update((x, name) for x in component)
    def dimension(file):
        # Reuse the independent dialect reader with a temporary glob view.
        rows = file.read_bytes().split(b"\n")[1:]
        def decode(x):
            return (x[1:-1] if x.startswith(b'"') else x).decode()
        return {decode(a): decode(b) for row in rows if row for a, b in [row.split(b",")]}
    products, campaigns = dimension(data / "products.csv"), dimension(data / "campaigns.csv")
    kept = {}
    for fields in strict_rows(data / "events", 7):
        eid = fields[0]
        if eid and (eid not in kept or fields[3] < kept[eid][3]):
            kept[eid] = fields
    by_person = defaultdict(list)
    for eid, uid, text, ingest, kind, product, price in kept.values():
        ts = stamp(text, start, end)
        if not uid or not product or ts is None or kind not in {"view", "cart", "purchase"}:
            continue
        cents = 0
        if kind == "purchase":
            if not re.fullmatch(r"[0-9]+", price) or not 1 <= int(price) <= 10000000:
                continue
            cents = int(price)
        if stamp(ingest, 0, 10**18) - ts > DAY:
            continue
        by_person[names.get(uid, uid)].append((ts, cents, products.get(product, "unknown")))
    clicks = defaultdict(list)
    seen = set()
    for cid, uid, campaign, text in strict_rows(data / "clicks", 4):
        if not cid or cid in seen:
            continue
        seen.add(cid)
        ts = stamp(text, start - WEEK, end)
        if uid and campaign in campaigns and ts is not None:
            clicks[names.get(uid, uid)].append((ts, cid, campaign))
    user_agg = {}
    campaign_agg = defaultdict(lambda: [0, 0, 0, 0])
    for person, events in by_person.items():
        events.sort()
        sessions = []
        previous = None
        for ts, cents, category in events:
            if previous is None or ts - previous > 1800000:
                sessions.append([ts, []])
            sessions[-1][1].append((cents, category))
            previous = ts
        for beginning, events in sessions:
            day = str((EPOCH + dt.timedelta(milliseconds=beginning)).date())
            a = user_agg.setdefault((person, day), [0, 0, 0, defaultdict(int)])
            a[0] += 1
            a[1] += len(events)
            revenue = sum(x[0] for x in events)
            a[2] += revenue
            for cents, category in events:
                if cents:
                    a[3][category] += cents
            eligible = [c for c in clicks[person] if beginning - WEEK <= c[0] <= beginning]
            winner = min(eligible, key=lambda c: (-c[0], c[1].encode()))[2] if eligible else "organic"
            a = campaign_agg[winner, day]
            a[0] += 1
            a[1] += revenue > 0
            a[2] += revenue
            for campaign in {c[2] for c in eligible}:
                campaign_agg[campaign, day][3] += 1
    users = []
    for (p, day), (ns, ne, revenue, cats) in user_agg.items():
        top = min(cats, key=lambda c: (-cats[c], c.encode())) if cats else None
        users.append((p, day, ns, ne, revenue, top))
    return {"user_daily": users,
            "campaign_daily": [(c, "organic" if c == "organic" else campaigns[c], day, *a)
                               for (c, day), a in campaign_agg.items()]}


def generate(data, seed, count=1500):
    rng = random.Random(seed)
    beginning = dt.datetime(2028, 2, 23, tzinfo=UTC) if seed % 2 else dt.datetime(2026, 10, 1, tzinfo=UTC)
    start = (beginning - EPOCH) // dt.timedelta(milliseconds=1)
    end = start + 14 * DAY
    (data / "manifest.json").write_text(json.dumps({"window_start": iso(start), "window_end": iso(end)}))
    (data / "products.csv").write_text("product_id,category\np1,shoes\np2,books\np3,鞋\n")
    (data / "campaigns.csv").write_text("campaign_id,channel\nc1,search\nc2,social\nc3,email\n")
    users = [f"u{i}" for i in range(40)] + [" u1", "u1 ", "U1", "é", "中", "\tfirst"]
    def text(ts):
        choice = rng.randrange(4)
        if choice == 0 or ts % 1000:
            return "0" * rng.randrange(5) + str(ts)
        if choice == 1:
            return iso(ts)
        minutes = rng.choice([-1439, -600, -240, 0, 330, 720, 1439])
        local = EPOCH + dt.timedelta(milliseconds=ts, minutes=minutes)
        return local.strftime("%Y-%m-%dT%H:%M:%S") + ("+" if minutes >= 0 else "-") + f"{abs(minutes)//60:02}:{abs(minutes)%60:02}"
    def row(fields):
        return ",".join('"' + str(x) + '"' if rng.random() < .3 else str(x) for x in fields)
    def write_parts(name, header, rows, n):
        folder = data / name
        folder.mkdir()
        parts = [[] for _ in range(n)]
        for r in rows:
            parts[rng.randrange(n-1)].append(r)  # last file is header-only
        for i, part in enumerate(parts):
            rng.shuffle(part)
            (folder / f"{i}.csv").write_bytes((header + "\n" + "\n".join(part) + ("\n" if part else "")).encode())
    links = [row([f"u{i}", f"u{i+1}"]) for i in range(15)]
    links += [row(["u20", "u23"]), row(["u23", " A-unused"]), row(["u31", "u31"])]
    links += ['u1,', ',u9', 'u1,u2,u3', '"u1,u2', 'u5"x,u1', '']
    write_parts("links", "user_id_a,user_id_b", links, 3)
    event_rows, click_rows = [], []
    bad_ts = ["", "2026-02-30T10:00:00Z", "2028-02-30T10:00:00Z", "2028-02-29T24:00:00Z",
              "2028-02-29T10:00:00+24:00", "2028-02-29t10:00:00Z", "١٧٩١٠٧١٨٩٠٠٠٠",
              "9" * 99, iso(start) + "\r", "-5", "1.79e12"]
    anchors = [start, start + DAY - 600000, start + 6*DAY, end-1]
    for i in range(count):
        ts = rng.choice(anchors) + rng.choice([0, 1, 1800000, 1800001, 3600000, 3600001, DAY, -1])
        if rng.random() < .25:
            ts = rng.randrange(start-DAY, end+DAY)
        ingest = iso(ts + rng.choice([-3600000, 0, DAY, DAY+1000, 2*DAY]))
        kind = rng.choice(["view", "cart", "purchase", "purchase", "Purchase"])
        cents = rng.choice(["100", "000250", "10000000", "0", "-5", "1.5", "10000001", "２５０", "100\r"])
        fields = [f"e{i}", rng.choice(users + [""]), text(ts), ingest, kind, rng.choice(["p1", "p2", "p3", "p9", ""]), cents]
        if rng.random() < .07:
            fields[2] = rng.choice(bad_ts)
        event_rows.append(row(fields))
        if rng.random() < .2:
            later = fields.copy()
            later[1] = rng.choice(users)
            later[2] = text(ts)
            later[3] = iso(stamp(ingest,0,10**18) + 1000)
            event_rows.append(row(later))
    for i in range(count//2):
        ts = rng.choice(anchors) + rng.choice([0, 1, -1, -WEEK, -WEEK-1, -3600000, 3600000])
        fields = [f"k{i}", rng.choice(users + [""]), rng.choice(["c1", "c2", "c3", "c9"]), text(ts)]
        if rng.random() < .05:
            fields[3] = rng.choice(bad_ts)
        click_rows.append(row(fields))
        if rng.random() < .2:
            click_rows.append(row(fields))
    event_rows += ['bad,row', '', 'x,u1,"x"oops,2026-10-01T00:00:00Z,view,p1,0']
    click_rows += ['bad,row', '', '"bad"x,u1,c1,0']
    write_parts("events", "event_id,user_id,ts,ingest_ts,event_type,product_id,price_cents", event_rows, 5)
    write_parts("clicks", "click_id,user_id,campaign_id,ts", click_rows, 4)


def main():
    seeds = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    for seed in range(seeds):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            data, out = tmp / "data", tmp / "out"
            data.mkdir()
            generate(data, seed)
            exp = reference(data)
            runner.run_core(data, out)
            actual = read_results(out)
            for table in exp:
                if sorted_rows(exp[table]) != sorted_rows(actual[table]):
                    print("FAIL", seed, table)
                    print("expected", sorted_rows(exp[table]))
                    print("actual", sorted_rows(actual[table]))
                    return 1
            print("PASS seed", seed)
    print(f"{seeds}/{seeds} random datasets match the independent oracle")
    return 0


if __name__ == "__main__":
    sys.exit(main())
