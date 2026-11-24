"""Read-only DuckDB catalog over the S3 data lake (gold + lineage layers)."""

import io
import logging
import os
import threading
from collections import deque

import boto3
import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

log = logging.getLogger(__name__)

TABLE_DOCS = {
    "sales": "Gold. One row per valid order joined with customer attributes (country, segment).",
    "customer_360": "Gold. One row per customer: orders, revenue, last order, reviews, avg rating, negatives.",
    "product_performance": "Gold. One row per product: units, revenue, return rate, ratings, sentiment, top complaint.",
    "daily_revenue": "Gold. Completed revenue, orders and units per day and country.",
    "review_insights": "Gold. AI-enriched reviews (sentiment, topic) with customer and product context.",
    "lineage_runs": "Lineage. One row per pipeline run.",
    "lineage_jobs": "Lineage. One row per job per run: layer, inputs, outputs, rows in/out/dropped, status.",
    "lineage_edges": "Lineage. Dataset-level edges (source_dataset -> target_dataset) per job and run.",
    "lineage_columns": "Lineage. Column-level lineage with the transformation applied, per run.",
    "data_quality": "Lineage. Rows dropped per data-quality rule, job and run.",
}

DEFAULT_MAX_ROWS = 200


class QueryError(ValueError):
    pass


class Catalog:
    """Loads lake tables into an isolated in-memory DuckDB with external access disabled."""

    def __init__(self, s3=None, buckets: tuple[str, ...] = ("gold", "lineage")):
        self.s3 = s3
        self.buckets = buckets
        self._lock = threading.RLock()
        self._fingerprint: tuple | None = None
        self._con: duckdb.DuckDBPyConnection | None = None
        self.tables: dict[str, str] = {}

    @classmethod
    def from_env(cls) -> "Catalog":
        s3 = boto3.client(
            "s3",
            endpoint_url=os.getenv("S3_ENDPOINT", "http://localhost:8333"),
            aws_access_key_id=os.getenv("S3_ACCESS_KEY", "lakereader"),
            aws_secret_access_key=os.getenv("S3_SECRET_KEY", "lakereader-secret"),
            region_name="us-east-1",
        )
        buckets = tuple(os.getenv("LAKE_BUCKETS", "gold,lineage").split(","))
        return cls(s3, buckets)

    @classmethod
    def from_tables(cls, tables: dict[str, tuple[str, pa.Table]]) -> "Catalog":
        """Build a catalog from in-memory Arrow tables ({name: (bucket, table)}); used in tests."""
        cat = cls()
        cat._build(tables)
        cat._fingerprint = ("static",)
        return cat

    def _objects(self) -> list[tuple[str, str, str]]:
        found = []
        for bucket in self.buckets:
            try:
                for obj in self.s3.list_objects_v2(Bucket=bucket).get("Contents", []):
                    if obj["Key"].endswith(".parquet"):
                        found.append((bucket, obj["Key"], obj["ETag"]))
            except self.s3.exceptions.NoSuchBucket:
                continue
        return sorted(found)

    def _build(self, tables: dict[str, tuple[str, pa.Table]]) -> None:
        con = duckdb.connect(":memory:")
        for name, (bucket, table) in tables.items():
            con.register("_incoming", table)
            con.execute(f'CREATE TABLE "{name}" AS SELECT * FROM _incoming')
            con.unregister("_incoming")
        con.execute("SET enable_external_access = false")
        con.execute("SET lock_configuration = true")
        old, self._con = self._con, con
        self.tables = {name: bucket for name, (bucket, _) in tables.items()}
        if old is not None:
            old.close()

    def refresh(self) -> None:
        """Reload tables from the lake when any parquet object changed (cheap ETag check)."""
        if self.s3 is None:
            return
        with self._lock:
            objects = self._objects()
            fingerprint = tuple(objects)
            if fingerprint == self._fingerprint:
                return
            tables = {}
            for bucket, key, _ in objects:
                body = self.s3.get_object(Bucket=bucket, Key=key)["Body"].read()
                tables[key.split("/")[0]] = (bucket, pq.read_table(io.BytesIO(body)))
            self._build(tables)
            self._fingerprint = fingerprint
            log.info("Loaded %d tables from the lake", len(tables))

    @property
    def con(self) -> duckdb.DuckDBPyConnection:
        self.refresh()
        if self._con is None:
            raise QueryError("The data lake is empty. Run the pipeline first.")
        return self._con

    # ---- generic SQL -------------------------------------------------------------------

    def query(self, sql: str, max_rows: int = DEFAULT_MAX_ROWS) -> dict:
        statements = duckdb.extract_statements(sql)
        if len(statements) != 1:
            raise QueryError("Exactly one SQL statement is allowed.")
        if statements[0].type != duckdb.StatementType.SELECT:
            raise QueryError("Only read-only SELECT queries are allowed.")
        max_rows = max(1, min(int(max_rows), 1000))
        with self._lock:
            try:
                cur = self.con.cursor().execute(statements[0].query)
                columns = [d[0] for d in cur.description]
                rows = cur.fetchmany(max_rows + 1)
            except duckdb.Error as exc:
                raise QueryError(str(exc)) from exc
        return {
            "columns": columns,
            "rows": [[_jsonable(v) for v in r] for r in rows[:max_rows]],
            "row_count": min(len(rows), max_rows),
            "truncated": len(rows) > max_rows,
        }

    def list_tables(self) -> list[dict]:
        con = self.con
        out = []
        for name, bucket in sorted(self.tables.items(), key=lambda t: (t[1], t[0])):
            rows = con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            out.append({"table": name, "layer": bucket, "rows": rows, "description": TABLE_DOCS.get(name, "")})
        return out

    def describe_table(self, table: str) -> dict:
        if table not in self.tables and (self.refresh() or table not in self.tables):
            raise QueryError(f"Unknown table '{table}'. Use list_tables to see available tables.")
        cols = self.con.execute(f'DESCRIBE "{table}"').fetchall()
        sample = self.query(f'SELECT * FROM "{table}" LIMIT 5')
        return {
            "table": table,
            "layer": self.tables[table],
            "description": TABLE_DOCS.get(table, ""),
            "columns": [{"name": c[0], "type": c[1]} for c in cols],
            "sample": sample,
        }

    # ---- lineage -----------------------------------------------------------------------

    def _latest_run_id(self) -> str:
        self.refresh()
        if "lineage_runs" not in self.tables:
            raise QueryError("No lineage recorded yet. Run the pipeline first.")
        return self.con.execute("SELECT run_id FROM lineage_runs ORDER BY started_at DESC LIMIT 1").fetchone()[0]

    def _rows(self, sql: str, params: list) -> list[dict]:
        with self._lock:
            cur = self.con.cursor().execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, map(_jsonable, r))) for r in cur.fetchall()]

    def lineage_graph(self, dataset: str | None = None, run_id: str | None = None) -> dict:
        """Dataset-level lineage graph for a run; optionally restricted to one dataset's up/downstream."""
        run_id = run_id or self._latest_run_id()
        edges = self._rows(
            "SELECT DISTINCT source_dataset AS source, target_dataset AS target, job_name AS job "
            "FROM lineage_edges WHERE run_id = ?", [run_id])
        jobs = self._rows(
            "SELECT job_name, layer, description, inputs, outputs, rows_in, rows_out, rows_dropped, status, "
            "started_at, ended_at FROM lineage_jobs WHERE run_id = ? ORDER BY started_at", [run_id])
        if dataset:
            keep = _reachable(edges, dataset, upstream=True) | _reachable(edges, dataset, upstream=False)
            edges = [e for e in edges if e["source"] in keep and e["target"] in keep]
            jobs = [j for j in jobs if any(e["job"] == j["job_name"] for e in edges)]
        rows_out = {}
        for j in jobs:
            for out in filter(None, j["outputs"].split(", ")):
                rows_out[out] = j["rows_out"]
        cols: dict[str, list[str]] = {}
        for r in self._rows("SELECT DISTINCT target_dataset, target_column FROM lineage_columns WHERE run_id = ? "
                            "ORDER BY target_dataset, target_column", [run_id]):
            cols.setdefault(r["target_dataset"], []).append(r["target_column"])
        names = sorted({e["source"] for e in edges} | {e["target"] for e in edges} | ({dataset} if dataset else set()))
        nodes = [{"id": n, "layer": n.split(".", 1)[0], "name": n.split(".", 1)[1], "rows": rows_out.get(n),
                  "columns": cols.get(n, [])}
                 for n in names]
        return {"run_id": run_id, "nodes": nodes, "edges": edges, "jobs": jobs}

    def column_lineage(self, dataset: str, column: str, run_id: str | None = None) -> dict:
        """Trace a column back to its origin, hop by hop, with the transformation at each step."""
        run_id = run_id or self._latest_run_id()
        rows = self._rows(
            "SELECT target_dataset, target_column, source_dataset, source_column, transformation, job_name "
            "FROM lineage_columns WHERE run_id = ?", [run_id])
        index: dict[tuple[str, str], list[dict]] = {}
        for r in rows:
            index.setdefault((r["target_dataset"], r["target_column"]), []).append(r)
        if (dataset, column) not in index:
            raise QueryError(f"No column lineage for {dataset}.{column} in run {run_id}.")
        steps, seen, queue = [], set(), deque([(dataset, column)])
        while queue:
            key = queue.popleft()
            if key in seen:
                continue
            seen.add(key)
            for r in index.get(key, []):
                steps.append(r)
                queue.append((r["source_dataset"], r["source_column"]))
        origins = sorted({f'{s["source_dataset"]}.{s["source_column"]}' for s in steps
                          if (s["source_dataset"], s["source_column"]) not in index})
        return {"run_id": run_id, "dataset": dataset, "column": column, "steps": steps, "origins": origins}

    def data_quality(self, run_id: str | None = None) -> dict:
        run_id = run_id or self._latest_run_id()
        rules = self._rows(
            "SELECT job_name, dataset, rule, rows_affected FROM data_quality WHERE run_id = ? "
            "ORDER BY rows_affected DESC", [run_id])
        return {"run_id": run_id, "rules": rules, "total_rows_dropped": sum(r["rows_affected"] for r in rules)}


def _reachable(edges: list[dict], start: str, upstream: bool) -> set[str]:
    src, dst = ("target", "source") if upstream else ("source", "target")
    seen, queue = {start}, deque([start])
    while queue:
        node = queue.popleft()
        for e in edges:
            if e[src] == node and e[dst] not in seen:
                seen.add(e[dst])
                queue.append(e[dst])
    return seen


def _jsonable(v):
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    return str(v)
