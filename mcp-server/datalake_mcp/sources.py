"""Stage a candidate data source (file, JSON object or database table), profile it, suggest how its
columns link to the lake and validate those links. Nothing is written to the lake."""

import csv
import io
import json
import re
import sqlite3
import threading
from datetime import date, datetime
from decimal import Decimal
from difflib import SequenceMatcher

import pyarrow as pa

MAX_BYTES = 5_000_000
MAX_ROWS = 5000
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
TABLE_RE = re.compile(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)?$")
RELATIONS = ("join_key", "maps_to", "new")
KEY_RATIO = 0.95


class SourceError(ValueError):
    pass


# ---- reading ------------------------------------------------------------------------------

def _scalar(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, (dict, list)):
        return json.dumps(v, default=str)
    return v


def _flatten(record: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in record.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, f"{key}_"))
        else:
            out[key] = _scalar(v)
    return out


def _clean_name(name: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", str(name).strip().lower()).strip("_") or "col"


def _infer(text: str):
    if text is None or text == "":
        return None
    for cast in (int, float):
        try:
            return cast(text)
        except ValueError:
            pass
    return text


def to_table(records: list[dict]) -> pa.Table:
    """Records -> Arrow table with snake_case columns; mixed-type columns become strings."""
    records = [{_clean_name(k): v for k, v in _flatten(r).items()} for r in records[:MAX_ROWS] if isinstance(r, dict)]
    if not records:
        raise SourceError("The source has no rows.")
    names = list(dict.fromkeys(k for r in records for k in r))
    columns = {}
    for n in names:
        values = [r.get(n) for r in records]
        kinds = {type(v) for v in values if v is not None}
        if kinds <= {int}:
            columns[n] = pa.array(values, pa.int64())
        elif kinds <= {int, float}:
            columns[n] = pa.array([None if v is None else float(v) for v in values], pa.float64())
        elif kinds <= {bool}:
            columns[n] = pa.array(values, pa.bool_())
        else:
            columns[n] = pa.array([None if v is None else str(v) for v in values], pa.string())
    return pa.table(columns)


def read_file(filename: str, content: str) -> pa.Table:
    if len(content.encode()) > MAX_BYTES:
        raise SourceError(f"Files are limited to {MAX_BYTES // 1_000_000} MB.")
    ext = filename.rsplit(".", 1)[-1].lower()
    try:
        if ext == "csv":
            rows = list(csv.DictReader(io.StringIO(content)))
            return to_table([{k: _infer(v) for k, v in r.items()} for r in rows])
        if ext == "jsonl":
            return to_table([json.loads(line) for line in content.splitlines() if line.strip()])
        if ext == "json":
            return read_json(content)
    except (json.JSONDecodeError, csv.Error) as exc:
        raise SourceError(f"Could not parse {filename}: {exc}") from exc
    raise SourceError("Supported files: .csv, .json, .jsonl")


def read_json(content: str) -> pa.Table:
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise SourceError(f"Invalid JSON: {exc}") from exc
    if isinstance(data, dict):
        lists = [v for v in data.values() if isinstance(v, list) and v and isinstance(v[0], dict)]
        data = lists[0] if len(lists) == 1 else [data]
    if not isinstance(data, list):
        raise SourceError("JSON must be an object or an array of objects.")
    return to_table(data)


def read_db(url: str, table: str = "", query: str = "") -> pa.Table:
    """Read up to MAX_ROWS rows from a table or single SELECT, over a read-only connection."""
    if table:
        if not TABLE_RE.match(table):
            raise SourceError("Table must be a plain name, optionally schema-qualified.")
        sql = "SELECT * FROM " + ".".join(f'"{p}"' for p in table.split("."))
    else:
        sql = query.strip().rstrip(";")
        if not re.match(r"(?is)^(select|with)\b", sql) or ";" in sql:
            raise SourceError("Query must be a single SELECT statement.")
    sql = f"SELECT * FROM ({sql}) AS src LIMIT {MAX_ROWS}"
    try:
        if url.startswith(("postgresql://", "postgres://")):
            import psycopg

            with psycopg.connect(url, connect_timeout=5, autocommit=False,
                                 options="-c default_transaction_read_only=on -c statement_timeout=10000") as conn:
                conn.read_only = True
                cur = conn.execute(sql)
                cols = [d.name for d in cur.description]
                rows = cur.fetchall()
        elif url.startswith("sqlite:///"):
            conn = sqlite3.connect(f"file:{url.removeprefix('sqlite:///')}?mode=ro", uri=True)
            try:
                cur = conn.execute(sql)
                cols = [d[0] for d in cur.description]
                rows = cur.fetchall()
            finally:
                conn.close()
        else:
            raise SourceError("Supported databases: postgresql://user:pass@host:port/db or sqlite:///path.db")
    except SourceError:
        raise
    except Exception as exc:  # noqa: BLE001 - surface any driver error to the user, without the URL
        raise SourceError(f"Database error: {type(exc).__name__}: {str(exc).splitlines()[0]}") from exc
    return to_table([dict(zip(cols, r)) for r in rows])


# ---- profiling ----------------------------------------------------------------------------

def _type(t: pa.DataType) -> str:
    if pa.types.is_integer(t):
        return "int64"
    if pa.types.is_floating(t):
        return "float64"
    if pa.types.is_boolean(t):
        return "bool"
    return "string"


def _family(data_type: str) -> str:
    return "number" if data_type.startswith(("int", "float")) else data_type


def distinct(values: list) -> set[str]:
    return {str(v) for v in values if v is not None and v != ""}


def profile(table: pa.Table) -> list[dict]:
    out = []
    for name in table.column_names:
        values = table.column(name).to_pylist()
        present = [v for v in values if v is not None and v != ""]
        out.append({
            "column_name": name,
            "data_type": _type(table.schema.field(name).type),
            "null_pct": round(100 * (1 - len(present) / len(values)), 1) if values else 0.0,
            "distinct_count": len(distinct(present)),
            "sample_values": ", ".join(list(dict.fromkeys(str(v)[:40] for v in present))[:3]),
        })
    return out


class SourceStore:
    """In-memory staging area for candidate sources, keyed by name."""

    def __init__(self):
        self._lock = threading.Lock()
        self._sources: dict[str, dict] = {}

    def stage(self, name: str, kind: str, label: str, table: pa.Table) -> dict:
        if not NAME_RE.match(name):
            raise SourceError("Name must be snake_case (a-z, 0-9, _), e.g. support_tickets.")
        entry = {"name": name, "kind": kind, "dataset": f"source.{label}", "rows": table.num_rows,
                 "columns": profile(table), "sample": table.slice(0, 5).to_pylist()}
        with self._lock:
            self._sources[name] = {**entry, "table": table}
        return entry

    def get(self, name: str) -> dict:
        with self._lock:
            if name not in self._sources:
                raise SourceError(f"Unknown source '{name}'. Profile it first.")
            return self._sources[name]


# ---- linking ------------------------------------------------------------------------------

def _tokens(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def name_score(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if ta == tb:
        return 1.0
    words_a, words_b = set(a.lower().split("_")), set(b.lower().split("_"))
    jaccard = len(words_a & words_b) / len(words_a | words_b)
    return round(max(SequenceMatcher(None, ta, tb).ratio(), jaccard), 2)


def overlap(src: set[str], target: set[str]) -> float:
    return round(len(src & target) / len(src), 3) if src else 0.0


def suggest(source: dict, targets: list[dict], values_of) -> list[dict]:
    """Heuristic links: best existing column per source column by name similarity and value overlap.

    targets: [{ref, dataset, column, data_type, is_key}]; values_of(ref) -> set of distinct strings.
    """
    table: pa.Table = source["table"]
    links = []
    for col in source["columns"]:
        name = col["column_name"]
        src_vals = distinct(table.column(name).to_pylist())
        ranked = []
        for t in targets:
            if _family(t["data_type"]) != _family(col["data_type"]):
                continue
            ns = name_score(name, t["column"])
            ov = overlap(src_vals, values_of(t["ref"])) if src_vals else 0.0
            if ns < 0.6 and ov < 0.3:
                continue
            score = round(0.4 * ns + 0.6 * ov, 3)
            ranked.append((score, t["is_key"], t["dataset"].startswith("silver."), ns, ov, t))
        ranked.sort(key=lambda r: r[:3], reverse=True)
        link = {"source_column": name, "relation": "new", "target": None, "confidence": 0.0,
                "reason": "No similar column in the lake: new attribute.",
                "candidates": [r[5]["ref"] for r in ranked[:3]]}
        if ranked:
            score, is_key, _, ns, ov, t = ranked[0]
            keyish = col["distinct_count"] > 1 and (ov >= 0.5 and (is_key or _tokens(name).endswith("id")))
            if keyish:
                link.update(relation="join_key", target=t["ref"], confidence=score,
                            reason=f"{ov:.0%} of values found in {t['ref']}"
                                   + (" (a unique key)" if is_key else "") + f"; name similarity {ns:.2f}.")
            elif score >= 0.4:
                link.update(relation="maps_to", target=t["ref"], confidence=score,
                            reason=f"Same meaning as {t['ref']}: name similarity {ns:.2f}, value overlap {ov:.0%}.")
        links.append(link)
    return links


def validate(source: dict, links: list[dict], targets: dict[str, dict], values_of) -> list[dict]:
    """Check each confirmed link against real data."""
    table: pa.Table = source["table"]
    types = {c["column_name"]: c["data_type"] for c in source["columns"]}
    checks = []
    for link in links:
        col, rel, ref = link.get("source_column"), link.get("relation"), link.get("target") or None
        check = {"source_column": col, "relation": rel, "target": ref, "status": "ok", "overlap": None,
                 "message": ""}
        checks.append(check)
        if col not in types:
            check.update(status="fail", message=f"Column {col} is not in the source.")
            continue
        if rel not in RELATIONS:
            check.update(status="fail", message=f"Relation must be one of {', '.join(RELATIONS)}.")
            continue
        if rel == "new":
            check["message"] = "Lands as a new attribute."
            continue
        if ref not in targets:
            check.update(status="fail", message=f"{ref} is not a column in the lake.")
            continue
        t = targets[ref]
        src_vals = distinct(table.column(col).to_pylist())
        tgt_vals = values_of(ref)
        check["overlap"] = ov = overlap(src_vals, tgt_vals)
        orphans = sorted(src_vals - tgt_vals)
        if _family(t["data_type"]) != _family(types[col]):
            check.update(status="warn", message=f"Type mismatch: {types[col]} vs {t['data_type']}.")
        elif rel == "join_key":
            if ov < 0.5:
                check.update(status="fail", message=f"Only {ov:.0%} of values match {ref}; this is not a valid join.")
            elif orphans:
                check.update(status="warn", message=f"{len(orphans)} value{'s have' if len(orphans) > 1 else ' has'} no match in {ref}, "
                                                    f"e.g. {', '.join(orphans[:3])}.")
            else:
                check["message"] = f"All {len(src_vals)} values match {ref}."
            if check["status"] != "fail" and not t["is_key"]:
                check.update(status="warn", message=(check["message"] + f" {ref} is not unique: the join fans out.").strip())
        else:
            check["message"] = f"Types match; {ov:.0%} of values also appear in {ref}."
    return checks


def to_changes(source: dict, links: list[dict], feed: str, existing: dict[str, list[str]]) -> list[dict]:
    """Translate a staged source and its links into impact-analysis changes."""
    name, dataset = source["name"], source["dataset"]
    bronze, silver = f"bronze.{name}", f"silver.{name}"
    cols = [c["column_name"] for c in source["columns"]]
    changes = [{"type": "add_dataset", "dataset": bronze, "inputs": [dataset]}]
    changes += [{"type": "add_column", "dataset": bronze, "column": c, "if_missing": True, "transformation": f"landed as-is from {dataset}"}
                for c in cols]
    changes.append({"type": "add_dataset", "dataset": silver, "inputs": [bronze]})
    changes += [{"type": "add_column", "dataset": silver, "column": c, "if_missing": True, "sources": [f"{bronze}.{c}"],
                 "transformation": "typed and cleaned"} for c in cols]
    joins = [lk for lk in links if lk.get("relation") == "join_key" and lk.get("target")]
    inputs = [silver] + list(dict.fromkeys(lk["target"].rsplit(".", 1)[0] for lk in joins))
    changes.append({"type": "add_dataset", "dataset": feed, "inputs": [i for i in inputs if i != feed]})
    feed_cols = set(existing.get(feed, []))
    for lk in links:
        col = lk.get("source_column")
        if col not in cols or lk.get("relation") == "join_key" or col in feed_cols:
            continue
        how = f"same meaning as {lk['target']}" if lk.get("relation") == "maps_to" else f"new attribute from {name}"
        changes.append({"type": "add_column", "dataset": feed, "column": col, "if_missing": True, "sources": [f"{silver}.{col}"],
                        "transformation": how})
    return changes
