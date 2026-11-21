#!/usr/bin/env python3
"""Build-time integration check: linked IDs, midnight, assists, DATE and null."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import pyarrow as pa
import pyarrow.parquet as pq

with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp);data=root/"data";out=root/"out"
    for group in ["events","clicks","links"]:(data/group).mkdir(parents=True)
    (data/"manifest.json").write_text(json.dumps({"window_start":"2026-10-01T00:00:00Z","window_end":"2026-10-15T00:00:00Z"}))
    (data/"products.csv").write_text("product_id,category\np1,books\n")
    (data/"campaigns.csv").write_text("campaign_id,channel\nc1,search\nc2,social\n")
    (data/"links/a.csv").write_text("user_id_a,user_id_b\nu2,u1\n")
    (data/"events/a.csv").write_text(
        "event_id,user_id,ts,ingest_ts,event_type,product_id,price_cents\n"
        "e1,u1,2026-10-03T23:50:00Z,2026-10-03T23:50:00Z,view,p1,\n"
        "e2,u2,2026-10-04T00:10:00Z,2026-10-04T00:10:00Z,purchase,p1,1000\n"
        "e3, u1,2026-10-04T10:00:00Z,2026-10-04T10:00:00Z,view,p1,\n")
    (data/"clicks/a.csv").write_text(
        "click_id,user_id,campaign_id,ts\n"
        "k1,u1,c1,2026-10-03T22:00:00Z\n"
        "k2,u2,c2,2026-10-03T23:00:00Z\n")
    env=dict(os.environ,DATA_DIR=str(data),OUTPUT_DIR=str(out),NCPU="2")
    subprocess.run([os.environ.get("NATIVE_BIN","/app/core-native")],env=env,check=True)
    def table(name):
        return pa.concat_tables([pq.read_table(p) for p in sorted((out/name).glob("*.parquet"))])
    users=table("user_daily");campaigns=table("campaign_daily")
    assert users.schema.field("day").type==pa.date32()
    rows={r["user_id"]:r for r in users.to_pylist()}
    assert rows["u1"]["events"]==2 and rows["u1"]["revenue_cents"]==1000
    assert str(rows["u1"]["day"])=="2026-10-03" and rows["u1"]["top_category"]=="books"
    assert rows[" u1"]["top_category"] is None
    ad={r["campaign_id"]:r for r in campaigns.to_pylist()}
    assert ad["c2"]["revenue_cents"]==1000 and ad["c1"]["sessions"]==0
    assert ad["c1"]["assisted_sessions"]==1 and ad["organic"]["sessions"]==1
    # Empty data must still emit readable empty Parquet tables.
    (data/"events/a.csv").write_text("event_id,user_id,ts,ingest_ts,event_type,product_id,price_cents\n")
    subprocess.run([os.environ.get("NATIVE_BIN","/app/core-native")],env=env,check=True)
    assert table("user_daily").num_rows==0 and table("campaign_daily").num_rows==0
    print("Native Parquet integration smoke check PASS")
