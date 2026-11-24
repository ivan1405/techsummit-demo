"""Accepted (onboarded) sources: a registry of specs in the lake, built through bronze, silver and gold
by the same pipeline run as the core sources."""

import io
import json
import re
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal

import pandas as pd
from botocore.exceptions import ClientError

from .gold import GOLD_INPUTS
from .lake import Lake
from .lineage import ColumnLineage

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
FEED_RE = re.compile(r"^gold\.[a-z][a-z0-9_]*$")
TABLE_RE = re.compile(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)?$")
KINDS = ("file", "json", "database")
MAX_ROWS = 50_000
PREFIX = "sources/"
CORE_GOLD = set(GOLD_INPUTS)


class OnboardError(ValueError):
    pass


# ---- registry -----------------------------------------------------------------------------

def label(spec: dict) -> str:
    """Source dataset id suffix: source.<label> (matches the MCP staging labels)."""
    return {"file": spec.get("filename", ""), "json": f"{spec['name']}.json", "database": f"{spec['name']}.db"}[spec["kind"]]


def check_spec(spec: dict) -> dict:
    name, kind = spec.get("name", ""), spec.get("kind")
    if not NAME_RE.match(name):
        raise OnboardError("name must be snake_case, e.g. support_tickets.")
    if kind not in KINDS:
        raise OnboardError(f"kind must be one of {', '.join(KINDS)}.")
    if kind == "file" and not re.match(r"^[\w.-]+\.(csv|json|jsonl)$", spec.get("filename", "")):
        raise OnboardError("filename must be a .csv, .json or .jsonl file name.")
    if kind == "database" and not spec.get("url"):
        raise OnboardError("url is required for database sources.")
    feed = spec.get("feed") or f"gold.{name}"
    if not FEED_RE.match(feed):
        raise OnboardError("feed must look like gold.<name>.")
    links = [lk for lk in spec.get("links", []) if lk.get("source_column")]
    for lk in links:
        if lk.get("relation") not in ("join_key", "maps_to", "new"):
            raise OnboardError(f"Invalid relation for {lk['source_column']}.")
        if lk["relation"] != "new" and not re.match(r"^(silver|gold)\.\w+\.\w+$", lk.get("target") or ""):
            raise OnboardError(f"{lk['source_column']}: target must be a silver/gold column.")
    keep = ("name", "kind", "filename", "url", "table", "query")
    return {**{k: spec.get(k, "") for k in keep}, "feed": feed, "links": links,
            "accepted_at": datetime.now(timezone.utc).isoformat()}


def register(lake: Lake, spec: dict, content: str = "") -> dict:
    spec = check_spec(spec)
    bucket = lake.settings.registry_bucket
    if spec["kind"] != "database":
        if not content:
            raise OnboardError("content is required for file and JSON sources.")
        lake.put_bytes(bucket, f"{PREFIX}{spec['name']}/{label(spec)}", content.encode())
    lake.put_bytes(bucket, f"{PREFIX}{spec['name']}.json", json.dumps(spec, indent=2).encode())
    return spec


def unregister(lake: Lake, name: str) -> None:
    bucket = lake.settings.registry_bucket
    if not any(s["name"] == name for s in load(lake)):
        raise OnboardError(f"Unknown onboarded source '{name}'.")
    for obj in lake.s3.list_objects_v2(Bucket=bucket, Prefix=f"{PREFIX}{name}").get("Contents", []):
        if obj["Key"] in (f"{PREFIX}{name}.json",) or obj["Key"].startswith(f"{PREFIX}{name}/"):
            lake.s3.delete_object(Bucket=bucket, Key=obj["Key"])


def load(lake: Lake) -> list[dict]:
    bucket = lake.settings.registry_bucket
    try:
        objs = lake.s3.list_objects_v2(Bucket=bucket, Prefix=PREFIX).get("Contents", [])
    except ClientError:
        return []
    keys = sorted(o["Key"] for o in objs if o["Key"].endswith(".json") and o["Key"].count("/") == 1)
    return [json.loads(lake.get_bytes(bucket, k)) for k in keys]


def public(spec: dict) -> dict:
    """Spec safe to show: database passwords masked."""
    return {**spec, "url": re.sub(r"(://[^:/@]+):[^@]*@", r"\1:***@", spec.get("url", ""))}


# ---- extract ------------------------------------------------------------------------------

def _scalar(v):
    if isinstance(v, Decimal):
        return float(v)
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def extract(lake: Lake, spec: dict) -> tuple[str, bytes]:
    """Raw bytes of the source as delivered (database sources are snapshotted to JSONL)."""
    if spec["kind"] != "database":
        return label(spec), lake.get_bytes(lake.settings.registry_bucket, f"{PREFIX}{spec['name']}/{label(spec)}")
    if spec.get("table"):
        if not TABLE_RE.match(spec["table"]):
            raise OnboardError("Invalid table name.")
        sql = "SELECT * FROM " + ".".join(f'"{p}"' for p in spec["table"].split("."))
    else:
        sql = spec.get("query", "").strip().rstrip(";")
        if not re.match(r"(?is)^(select|with)\b", sql) or ";" in sql:
            raise OnboardError("Query must be a single SELECT statement.")
    sql = f"SELECT * FROM ({sql}) AS src LIMIT {MAX_ROWS}"
    url = spec["url"]
    if url.startswith(("postgresql://", "postgres://")):
        import psycopg

        with psycopg.connect(url, connect_timeout=5,
                             options="-c default_transaction_read_only=on -c statement_timeout=30000") as conn:
            cur = conn.execute(sql)
            cols, rows = [d.name for d in cur.description], cur.fetchall()
    elif url.startswith("sqlite:///"):
        conn = sqlite3.connect(f"file:{url.removeprefix('sqlite:///')}?mode=ro", uri=True)
        try:
            cur = conn.execute(sql)
            cols, rows = [d[0] for d in cur.description], cur.fetchall()
        finally:
            conn.close()
    else:
        raise OnboardError("Unsupported database URL.")
    lines = (json.dumps({c: _scalar(v) for c, v in zip(cols, r)}, default=str) for r in rows)
    return f"{spec['name']}.jsonl", "\n".join(lines).encode()


def _clean_name(name: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", str(name).strip().lower()).strip("_") or "col"


def _flatten(record: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in record.items():
        if isinstance(v, dict):
            out.update(_flatten(v, f"{prefix}{k}_"))
        else:
            out[_clean_name(f"{prefix}{k}")] = json.dumps(v) if isinstance(v, list) else v
    return out


def raw_frame(filename: str, data: bytes) -> pd.DataFrame:
    """Records as delivered (text), flattened and snake_cased like the staging profile."""
    text = data.decode()
    if filename.endswith(".csv"):
        df = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
        records = df.to_dict("records")
    elif filename.endswith(".jsonl"):
        records = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        records = json.loads(text)
        if isinstance(records, dict):
            lists = [v for v in records.values() if isinstance(v, list) and v and isinstance(v[0], dict)]
            records = lists[0] if len(lists) == 1 else [records]
    flat = [_flatten(r) for r in records if isinstance(r, dict)]
    if not flat:
        raise OnboardError(f"{filename} has no rows.")
    df = pd.DataFrame(flat)
    return df.map(lambda v: None if v is None or (isinstance(v, float) and pd.isna(v)) else str(v)).astype("string")


# ---- silver / gold ------------------------------------------------------------------------

def joins(spec: dict) -> list[tuple[str, str, str]]:
    """(source_column, target_dataset, target_column) for every join key link."""
    return [(lk["source_column"], *lk["target"].rsplit(".", 1)) for lk in spec["links"] if lk["relation"] == "join_key"]


def clean(raw: pd.DataFrame, spec: dict) -> tuple[pd.DataFrame, dict[str, int], ColumnLineage]:
    """Trim text, blank -> null, numeric typing, drop duplicate rows and rows missing every join key."""
    df = raw.apply(lambda s: s.str.strip()).replace({"": None})
    for col in df.columns:
        values = df[col].dropna()
        if len(values) and pd.to_numeric(values, errors="coerce").notna().all():
            df[col] = pd.to_numeric(df[col])
    dropped = {"duplicate_row": int(df.duplicated().sum())}
    df = df.drop_duplicates()
    keys = [col for col, _, _ in joins(spec) if col in df.columns]
    if keys:
        mask = df[keys].isna().all(axis=1)
        dropped["null_join_keys"] = int(mask.sum())
        df = df[~mask]
    lineage = {c: [(f"bronze.{spec['name']}", c, "trimmed, typed, deduplicated")] for c in df.columns}
    return df.reset_index(drop=True), dropped, lineage


def _target_frame(available: dict[str, pd.DataFrame], dataset: str) -> pd.DataFrame:
    if dataset not in available:
        raise OnboardError(f"Join target {dataset} is not built by the pipeline.")
    return available[dataset]


def build_new_feed(silver_df: pd.DataFrame, spec: dict, available: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, ColumnLineage, list[str]]:
    """New gold dataset: the source rows LEFT JOINed to every linked dataset (target columns prefixed on clash)."""
    silver_ds = f"silver.{spec['name']}"
    out = silver_df.copy()
    lineage: ColumnLineage = {c: [(silver_ds, c, "direct copy")] for c in out.columns}
    inputs = [silver_ds]
    for col, ds, key in joins(spec):
        target = _target_frame(available, ds)
        extra = [c for c in target.columns if c != key and not c.startswith("_")]
        names = {c: (c if c not in out.columns else f"{ds.split('.', 1)[1]}_{c}") for c in extra}
        right = target[[key, *extra]].drop_duplicates(subset=key).rename(columns={key: "__key", **names})
        right["__key"] = right["__key"].astype(str)
        out = out.assign(__key=out[col].astype(str)).merge(right, on="__key", how="left").drop(columns="__key")
        lineage.update({names[c]: [(ds, c, f"LEFT JOIN {ds} ON {col} = {key}")] for c in extra})
        inputs.append(ds)
    return out, lineage, list(dict.fromkeys(inputs))


def enrich_feed(feed_df: pd.DataFrame, silver_df: pd.DataFrame, spec: dict) -> tuple[pd.DataFrame, ColumnLineage]:
    """Existing dataset: add the source's attributes via the join key whose target column the feed has."""
    match = [(col, key) for col, _, key in joins(spec) if key in feed_df.columns]
    if not match:
        raise OnboardError(f"{spec['feed']} has none of the join-key target columns; link a key it contains.")
    col, key = match[0]
    if silver_df[col].duplicated().any():
        raise OnboardError(f"{col} is not unique in {spec['name']}: adding it to {spec['feed']} would duplicate "
                           f"rows. Feed a new gold dataset instead.")
    keys = {c for c, _, _ in joins(spec)}
    add = [c for c in silver_df.columns if c not in keys and c not in feed_df.columns]
    right = silver_df[[col, *add]].rename(columns={col: key})
    right[key] = right[key].astype(str)
    out = feed_df.assign(**{key: feed_df[key].astype(str)}).merge(right, on=key, how="left")
    out[key] = feed_df[key].values
    how = f"LEFT JOIN silver.{spec['name']} ON {key} = {col}"
    return out, {c: [(f"silver.{spec['name']}", c, how)] for c in add}


def descriptions(spec: dict, columns: list[str]) -> dict[str, str]:
    by_col = {lk["source_column"]: lk for lk in spec["links"]}
    out = {}
    for c in columns:
        lk = by_col.get(c, {"relation": "new"})
        what = {"join_key": f"join key to {lk.get('target')}", "maps_to": f"same meaning as {lk.get('target')}",
                "new": "new attribute"}[lk["relation"]]
        out[c] = f"From onboarded source {label(spec)}: {what}."
    return out


def preview(lake: Lake, spec: dict, content: str = "") -> dict:
    """Dry run before registering: parse and clean the source and check it can feed its target."""
    spec = check_spec(spec)
    if spec["kind"] == "database":
        filename, data = extract(lake, spec)
    else:
        filename, data = label(spec), content.encode()
    silver_df, dropped, _ = clean(raw_frame(filename, data), spec)
    missing = [c for c, _, _ in joins(spec) if c not in silver_df.columns]
    if missing:
        raise OnboardError(f"Join columns not in the source: {', '.join(missing)}.")
    feed = spec["feed"].split(".", 1)[1]
    if feed in CORE_GOLD:
        feed_df = lake.read_parquet(lake.settings.gold_bucket, feed)
        enrich_feed(feed_df[[c for c in feed_df.columns if not c.startswith("_")]], silver_df, spec)
    elif owners := [o["name"] for o in load(lake) if o["feed"] == spec["feed"] and o["name"] != spec["name"]]:
        raise OnboardError(f"{spec['feed']} is already built from onboarded source {owners[0]}.")
    return {"spec": spec, "rows": len(silver_df), "dropped": dropped}
