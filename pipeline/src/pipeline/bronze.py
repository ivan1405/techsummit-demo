"""Bronze layer: land raw source files untouched, partitioned by ingestion time."""

import io
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .lake import Lake

SOURCES = {
    "orders": "orders.csv",
    "customers": "customers.json",
    "reviews": "reviews.jsonl",
}


def count_records(filename: str, data: bytes) -> int:
    if filename.endswith(".csv"):
        return max(len(data.decode().splitlines()) - 1, 0)
    if filename.endswith(".jsonl"):
        return sum(1 for line in data.decode().splitlines() if line.strip())
    return len(json.loads(data))


def read_frame(filename: str, data: bytes) -> pd.DataFrame:
    """Parse a raw source file as text columns, exactly as delivered."""
    if filename.endswith(".csv"):
        return pd.read_csv(io.BytesIO(data), dtype=str)
    if filename.endswith(".jsonl"):
        return pd.read_json(io.BytesIO(data), lines=True, dtype=str)
    return pd.DataFrame(json.loads(data)).astype("string")


def ingest_one(lake: Lake, sources_dir: str, source: str, run_ts: datetime | None = None) -> tuple[str, int]:
    run_ts = run_ts or datetime.now(timezone.utc)
    filename = SOURCES[source]
    data = (Path(sources_dir) / filename).read_bytes()
    key = f"{source}/ingested_at={run_ts:%Y%m%dT%H%M%SZ}/{filename}"
    lake.put_bytes(lake.settings.bronze_bucket, key, data)
    return key, count_records(filename, data)
