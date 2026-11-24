"""Read-only DuckDB catalog over the S3 data lake (gold + lineage layers)."""

import io
import logging
import os
import re
import threading
from collections import deque
from datetime import datetime, timezone

import boto3
import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from . import impact, sources

log = logging.getLogger(__name__)

TABLE_DOCS = {
    "sales": "Gold. One row per valid order joined with customer attributes (country, segment).",
    "customer_360": "Gold. One row per customer: orders, revenue, last order, reviews, avg rating, negatives.",
    "product_performance": "Gold. One row per product: units, revenue, return rate, ratings, sentiment, top complaint.",
    "daily_revenue": "Gold. Completed revenue, orders and units per day and country.",
    "review_insights": "Gold. AI-enriched reviews (sentiment, topic) with customer and product context.",
    "lineage_runs": "Lineage. One row per pipeline run.",
    "lineage_jobs": "Lineage. One row per job per run: layer, inputs, outputs, rows in/out/dropped, status.",
    "lineage_outputs": "Lineage. Rows and column count of every dataset written, per job and run.",
    "lineage_edges": "Lineage. Dataset-level edges (source_dataset -> target_dataset) per job and run.",
    "lineage_columns": "Lineage. Column-level lineage with the transformation applied, per run.",
    "data_quality": "Lineage. Rows dropped per data-quality rule, job and run.",
    "contract_checks": "Lineage. Data-contract checks (row count, schema, types, not-null, unique key) per gold dataset and run.",
    "data_dictionary": "Lineage. Data dictionary: description, type, profile and upstream of every column, per run.",
}

DEFAULT_MAX_ROWS = 200


READER, ANALYST = "reader", "analyst"
# Reader masks per PII tag: a hint plus a short stable token (md5 prefix), so equal values stay equal and readers can
# still count distinct values and join on masked columns. Analysts see raw values.
_TOKEN = "'***-' || left(md5(CAST({c} AS VARCHAR)), 6)"
MASKS = {
    "email": "CASE WHEN {c} IS NULL THEN NULL WHEN contains(CAST({c} AS VARCHAR), '@') THEN "
             "left(CAST({c} AS VARCHAR), 1) || " + _TOKEN + " || regexp_extract(CAST({c} AS VARCHAR), '@[^@]*$') "
             "ELSE " + _TOKEN + " END",
    "name": "CASE WHEN {c} IS NULL THEN NULL ELSE left(CAST({c} AS VARCHAR), 1) || " + _TOKEN + " END",
}
DEFAULT_MASK = "CASE WHEN {c} IS NULL THEN NULL ELSE " + _TOKEN + " END"
MASKED = "***"


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
        self._reader: duckdb.DuckDBPyConnection | None = None
        self.pii: dict[str, dict[str, str]] = {}
        self.tables: dict[str, str] = {}
        self._values: dict[str, set[str]] = {}

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
        pii = self._pii(con, tables)
        reader = duckdb.connect(":memory:")
        for name in tables:
            masks = [f'{MASKS.get(tag, DEFAULT_MASK).format(c=_ident(col))} AS {_ident(col)}'
                     for col, tag in pii.get(name, {}).items()]
            if name == "data_dictionary" and "pii" in _columns(con, name):
                masks.append(f"CASE WHEN coalesce(pii, '') <> '' OR (dataset, column_name) IN (SELECT dataset, column_name "
                             f"FROM data_dictionary WHERE coalesce(pii, '') <> '') THEN '{MASKED}' "
                             f"ELSE sample_values END AS sample_values")
            select = f'SELECT * REPLACE ({", ".join(masks)}) FROM "{name}"' if masks else f'SELECT * FROM "{name}"'
            reader.register("_incoming", con.sql(select).to_arrow_table())
            reader.execute(f'CREATE TABLE "{name}" AS SELECT * FROM _incoming')
            reader.unregister("_incoming")
        for c in (con, reader):
            c.execute("SET enable_external_access = false")
            c.execute("SET lock_configuration = true")
        old = (self._con, self._reader)
        self._con, self._reader, self.pii = con, reader, pii
        self.tables = {name: bucket for name, (bucket, _) in tables.items()}
        self._values = {}
        for c in old:
            if c is not None:
                c.close()

    @staticmethod
    def _pii(con: duckdb.DuckDBPyConnection, tables: dict) -> dict[str, dict[str, str]]:
        """{table: {column: pii tag}} for the lake tables, from each dataset's most recent dictionary entry (a removed
        source keeps its last tags, as its tables stay in the lake)."""
        if "data_dictionary" not in tables or "lineage_runs" not in tables \
                or "pii" not in _columns(con, "data_dictionary"):
            return {}
        rows = con.execute(
            "SELECT dataset, column_name, pii FROM ("
            "  SELECT d.dataset, d.column_name, d.pii,"
            "         rank() OVER (PARTITION BY d.dataset ORDER BY r.started_at DESC) AS recency"
            "  FROM data_dictionary d JOIN lineage_runs r USING (run_id)"
            "  WHERE d.pii IS NOT NULL"
            ") WHERE recency = 1 AND pii <> ''").fetchall()
        out: dict[str, dict[str, str]] = {}
        for dataset, column, tag in rows:
            layer, name = dataset.split(".", 1)
            if tables.get(name, (None,))[0] == layer and column in _columns(con, name):
                out.setdefault(name, {})[column] = tag
        return out

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

    def _con_for(self, role: str) -> duckdb.DuckDBPyConnection:
        con = self.con
        return con if role == ANALYST else self._reader

    def query(self, sql: str, max_rows: int = DEFAULT_MAX_ROWS, role: str = READER) -> dict:
        """Read-only SELECT. The reader role queries copies of the tables with PII columns masked."""
        statements = duckdb.extract_statements(sql)
        if len(statements) != 1:
            raise QueryError("Exactly one SQL statement is allowed.")
        if statements[0].type != duckdb.StatementType.SELECT:
            raise QueryError("Only read-only SELECT queries are allowed.")
        max_rows = max(1, min(int(max_rows), 1000))
        with self._lock:
            try:
                cur = self._con_for(role).cursor().execute(statements[0].query)
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

    def describe_table(self, table: str, role: str = READER) -> dict:
        if table not in self.tables and (self.refresh() or table not in self.tables):
            raise QueryError(f"Unknown table '{table}'. Use list_tables to see available tables.")
        cols = self.con.execute(f'DESCRIBE "{table}"').fetchall()
        sample = self.query(f'SELECT * FROM "{table}" LIMIT 5', role=role)
        return {
            "table": table,
            "layer": self.tables[table],
            "description": TABLE_DOCS.get(table, ""),
            "columns": [{"name": c[0], "type": c[1], **({"pii": self.pii[table][c[0]]}
                                                          if c[0] in self.pii.get(table, {}) else {})} for c in cols],
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

    def contracts(self, run_id: str | None = None, now: datetime | None = None) -> dict:
        """Contract status per gold dataset: latest checks plus freshness against max_age_minutes."""
        run_id = run_id or self._latest_run_id()
        if "contract_checks" not in self.tables:
            raise QueryError("No contract checks recorded yet. Re-run the pipeline.")
        now = now or datetime.now(timezone.utc)
        checks = self._rows('SELECT dataset, owner, "check", expectation, observed, status, max_age_minutes '
                            "FROM contract_checks WHERE run_id = ? ORDER BY dataset", [run_id])
        updated: dict[str, str] = {}
        for j in self._rows("SELECT outputs, ended_at FROM lineage_jobs WHERE status = 'COMPLETE'", []):
            for out in filter(None, j["outputs"].split(", ")):
                updated[out] = max(updated.get(out, ""), j["ended_at"] or "")
        datasets: dict[str, dict] = {}
        for c in checks:
            d = datasets.setdefault(c["dataset"], {"dataset": c["dataset"], "owner": c["owner"],
                                                   "max_age_minutes": c["max_age_minutes"], "checks": []})
            d["checks"].append({k: c[k] for k in ("check", "expectation", "observed", "status")})
        for d in datasets.values():
            last = updated.get(d["dataset"])
            age = round((now - datetime.fromisoformat(last)).total_seconds() / 60, 1) if last else None
            limit = d["max_age_minutes"]
            d["last_updated"], d["age_minutes"] = last, age
            d["freshness"] = "unknown" if age is None else "fresh" if age <= limit else \
                "stale" if age <= 2 * limit else "expired"
            d["failed"] = sum(c["status"] == "fail" for c in d["checks"])
            d["status"] = "red" if d["failed"] or d["freshness"] in ("expired", "unknown") else \
                "amber" if d["freshness"] == "stale" else "green"
        return {"run_id": run_id, "checked_at": now.isoformat(), "datasets": list(datasets.values())}

    def explain_column(self, dataset: str, column: str, role: str = READER) -> dict:
        """Everything known about one column: dictionary entry, upstream trace, downstream uses,
        data-quality rules of the job that builds it and the dataset's contract status."""
        entry = next((c for d in self.data_dictionary(dataset, role=role)["datasets"] for c in d["columns"]
                      if c["column_name"] == column), None)
        if entry is None:
            raise QueryError(f"Unknown column {dataset}.{column}.")
        run_id = self._latest_run_id()
        try:
            upstream = self.column_lineage(dataset, column, run_id)
            steps, origins = upstream["steps"], upstream["origins"]
        except QueryError:
            steps, origins = [], [entry["upstream"]] if entry.get("upstream") else []
        raw = {r["ref"]: r["upstream"] for r in self._rows(
            "SELECT dataset || '.' || column_name AS ref, upstream FROM data_dictionary "
            "WHERE run_id = ? AND layer = 'bronze' AND coalesce(upstream, '') <> ''", [run_id])}
        origins = list(dict.fromkeys(raw.get(o, o) for o in origins))
        edges = self._rows("SELECT DISTINCT source_dataset, source_column, target_dataset, target_column, "
                           "transformation FROM lineage_columns WHERE run_id = ?", [run_id])
        downstream, frontier, seen = [], [(dataset, column)], set()
        while frontier:
            ref = frontier.pop()
            for e in edges:
                tgt = (e["target_dataset"], e["target_column"])
                if (e["source_dataset"], e["source_column"]) == ref and tgt not in seen:
                    seen.add(tgt)
                    frontier.append(tgt)
                    downstream.append({"dataset": tgt[0], "column": tgt[1], "from": ".".join(ref),
                                       "transformation": e["transformation"]})
        jobs = [j["job_name"] for j in self._rows("SELECT job_name, outputs FROM lineage_jobs WHERE run_id = ?", [run_id])
                if dataset in j["outputs"].split(", ")]
        quality = self._rows("SELECT job_name, rule, rows_affected FROM data_quality WHERE run_id = ? "
                             "AND list_contains(?, job_name) ORDER BY rows_affected DESC", [run_id, jobs]) \
            if jobs and "data_quality" in self.tables else []
        contract = None
        if "contract_checks" in self.tables:
            contract = next((d for d in self.contracts(run_id)["datasets"] if d["dataset"] == dataset), None)
        return {"run_id": run_id, "dataset": dataset, "column": column, **{k: v for k, v in entry.items()
                                                                         if k != "column_name"},
                "steps": steps, "origins": origins, "downstream": downstream, "built_by": jobs,
                "quality": quality,
                "contract": {k: contract[k] for k in ("status", "owner", "freshness", "age_minutes", "failed")}
                if contract else None}

    # ---- run history & run-to-run diff -------------------------------------------------

    def _snapshots(self, run_ids: list[str]) -> dict[str, dict]:
        """Per run: rows and schema per dataset, rows dropped per rule, contract check status."""
        snap = {r: {"rows": {}, "schema": {}, "quality": {}, "contracts": {}} for r in run_ids}
        if not run_ids:
            return snap
        marks = ", ".join("?" * len(run_ids))
        if "lineage_outputs" in self.tables:
            for r in self._rows(f"SELECT run_id, dataset, rows FROM lineage_outputs WHERE run_id IN ({marks})", run_ids):
                snap[r["run_id"]]["rows"][r["dataset"]] = r["rows"]
        older = [r for r in run_ids if not snap[r]["rows"]]  # runs recorded before lineage_outputs existed
        if older and "lineage_jobs" in self.tables:
            marks_old = ", ".join("?" * len(older))
            for r in self._rows(f"SELECT run_id, outputs, rows_out FROM lineage_jobs WHERE run_id IN ({marks_old})",
                                older):
                outs = [o for o in r["outputs"].split(", ") if o]
                for o in outs:
                    snap[r["run_id"]]["rows"][o] = r["rows_out"] if len(outs) == 1 else None
        if "data_dictionary" in self.tables:
            for r in self._rows(f"SELECT run_id, dataset, column_name, data_type FROM data_dictionary "
                                f"WHERE run_id IN ({marks}) AND NOT starts_with(column_name, '_') "
                                "ORDER BY position", run_ids):
                snap[r["run_id"]]["schema"].setdefault(r["dataset"], {})[r["column_name"]] = r["data_type"]
        if "data_quality" in self.tables:
            for r in self._rows(f"SELECT run_id, job_name, rule, SUM(rows_affected) AS n FROM data_quality "
                                f"WHERE run_id IN ({marks}) GROUP BY ALL", run_ids):
                snap[r["run_id"]]["quality"][(r["job_name"], r["rule"])] = int(r["n"])
        if "contract_checks" in self.tables:
            for r in self._rows(f'SELECT run_id, dataset, "check", status FROM contract_checks WHERE run_id IN ({marks})',
                                run_ids):
                snap[r["run_id"]]["contracts"][(r["dataset"], r["check"])] = r["status"]
        return snap

    def _runs(self) -> list[dict]:
        self.refresh()
        if "lineage_runs" not in self.tables:
            raise QueryError("No lineage recorded yet. Run the pipeline first.")
        return self._rows("SELECT * FROM lineage_runs ORDER BY started_at DESC", [])

    def run_history(self, limit: int = 20) -> dict:
        """Latest runs, newest first, each with totals and a summary of what changed vs the run before it."""
        runs = self._runs()
        shown = runs[:max(1, limit)]
        ids = [r["run_id"] for r in runs[:len(shown) + 1]]
        snap = self._snapshots(ids)
        out = []
        for i, r in enumerate(shown):
            cur = snap[r["run_id"]]
            prev = snap[ids[i + 1]] if i + 1 < len(ids) else None
            out.append({
                "run_id": r["run_id"], "started_at": r["started_at"], "ended_at": r.get("ended_at"),
                "status": r.get("status"), "trigger": r.get("trigger") or "manual", "jobs": r.get("jobs"),
                "duration_s": _seconds(r["started_at"], r.get("ended_at")),
                "datasets": len(cur["rows"]),
                "gold_rows": sum(n or 0 for d, n in cur["rows"].items() if d.startswith("gold.")),
                "rows_dropped": sum(cur["quality"].values()),
                "contract_failures": sum(st == "fail" for st in cur["contracts"].values()),
                "base_run_id": ids[i + 1] if prev else None,
                "changes": _diff(prev, cur)["summary"] if prev else None,
            })
        return {"total_runs": len(runs), "runs": out}

    def run_diff(self, run_id: str | None = None, base_run_id: str | None = None) -> dict:
        """Compare a run (default: latest) with a base run (default: the run before it)."""
        runs = self._runs()
        order = [r["run_id"] for r in runs]
        run_id = run_id or order[0]
        if run_id not in order:
            raise QueryError(f"Unknown run {run_id}.")
        if not base_run_id:
            i = order.index(run_id)
            if i + 1 >= len(order):
                raise QueryError("This is the first run: there is nothing to compare it with.")
            base_run_id = order[i + 1]
        if base_run_id not in order:
            raise QueryError(f"Unknown run {base_run_id}.")
        snap = self._snapshots([run_id, base_run_id])
        meta = {r["run_id"]: {k: r.get(k) for k in ("run_id", "started_at", "ended_at", "status")} for r in runs}
        return {"run": meta[run_id], "base": meta[base_run_id], **_diff(snap[base_run_id], snap[run_id])}

    # ---- data dictionary & what-if impact -----------------------------------------------

    def data_dictionary(self, dataset: str | None = None, search: str | None = None,
                        run_id: str | None = None, role: str = READER) -> dict:
        """Every dataset with its description, row count and documented columns (sample values of PII columns
        are masked for the reader role)."""
        run_id = run_id or self._latest_run_id()
        if "data_dictionary" not in self.tables:
            raise QueryError("No data dictionary recorded yet. Re-run the pipeline.")
        rows = self._rows(
            "SELECT dataset, layer, column_name, data_type, description, null_pct, distinct_count, sample_values, "
            "upstream, transformation, " + ("pii, pii_origin" if "pii" in _columns(self.con, "data_dictionary")
                                            else "'' AS pii, '' AS pii_origin")
            + " FROM data_dictionary WHERE run_id = ? ORDER BY dataset, position", [run_id])
        if role != ANALYST:
            rows = [{**r, "sample_values": MASKED} if r["pii"] else r for r in rows]
        graph = self.lineage_graph(run_id=run_id)
        rows_out = {n["id"]: n["rows"] for n in graph["nodes"]}
        docs = {o: j["description"] for j in graph["jobs"] for o in j["outputs"].split(", ")}
        needle = (search or "").strip().lower()
        out: dict[str, dict] = {}
        for r in rows:
            if dataset and r["dataset"] != dataset:
                continue
            name = r["dataset"].split(".", 1)[1]
            entry = out.setdefault(r["dataset"], {
                "dataset": r["dataset"], "layer": r["layer"], "rows": rows_out.get(r["dataset"]),
                "description": (TABLE_DOCS.get(name) if r["layer"] == "gold" else None) or docs.get(r["dataset"], ""),
                "columns": [],
            })
            haystack = " ".join(str(r[k] or "") for k in ("dataset", "column_name", "description", "upstream"))
            if not needle or needle in haystack.lower() or needle in (entry["description"] or "").lower():
                entry["columns"].append({k: v for k, v in r.items() if k not in ("dataset", "layer")})
        if dataset and not out:
            raise QueryError(f"Unknown dataset '{dataset}'.")
        datasets = [d for d in out.values() if d["columns"] or not needle]
        return {"run_id": run_id, "datasets": datasets}

    # ---- candidate sources: linking & validation ---------------------------------------------

    def link_targets(self, exclude: str = "") -> dict[str, dict]:
        """Silver and gold business columns a new source can link to, keyed by 'layer.dataset.column'.

        exclude: a source name whose own onboarded tables and columns are left out (re-onboarding)."""
        own = (f"silver.{exclude}.", f"bronze.{exclude}.") if exclude else ()
        out = {}
        for d in self.data_dictionary(role=ANALYST)["datasets"]:
            if d["layer"] not in ("silver", "gold") or (exclude and d["dataset"] == f"silver.{exclude}"):
                continue
            for c in d["columns"]:
                if c["column_name"].startswith("_") or (own and (c["upstream"] or "").startswith(own)):
                    continue
                ref = f'{d["dataset"]}.{c["column_name"]}'
                out[ref] = {"ref": ref, "dataset": d["dataset"], "column": c["column_name"],
                            "data_type": c["data_type"], "description": c["description"],
                            "is_key": bool(d["rows"]) and c["distinct_count"] >= sources.KEY_RATIO * d["rows"]}
        return out

    def column_values(self, ref: str, limit: int = 50000) -> set[str]:
        """Distinct non-null values of a silver/gold column, as strings (silver is read straight from S3)."""
        if ref in self._values:
            return self._values[ref]
        dataset, column = impact.split_ref(ref)
        layer, name = dataset.split(".", 1)
        quoted = column.replace('"', '""')
        if self.tables.get(name) == layer:
            with self._lock:
                rows = self.con.execute(f'SELECT DISTINCT CAST("{quoted}" AS VARCHAR) FROM "{name}" '
                                        f'WHERE "{quoted}" IS NOT NULL LIMIT {limit}').fetchall()
            values = {r[0] for r in rows}
        elif layer == "silver" and self.s3 is not None:
            body = self.s3.get_object(Bucket="silver", Key=f"{name}/{name}.parquet")["Body"].read()
            table = pq.read_table(io.BytesIO(body), columns=[column])
            values = sources.distinct(table.column(column).to_pylist())
        else:
            values = set()
        self._values[ref] = values
        return values

    def suggest_links(self, source: dict) -> dict:
        targets = self.link_targets(exclude=source["name"])
        links = sources.suggest(source, list(targets.values()), self.column_values)
        return {"source": source["name"], "links": links, "docs": sources.apply_docs(links, source, targets),
                "targets": [{k: t[k] for k in ("ref", "data_type", "description", "is_key")} for t in targets.values()]}

    def validate_links(self, source: dict, links: list[dict], feed: str = "") -> dict:
        targets = self.link_targets(exclude=source["name"])
        feed = feed or f"gold.{source['name']}"
        if not re.match(r"^(silver|gold)\.[a-z][a-z0-9_]*$", feed):
            raise QueryError("Feed dataset must look like gold.<name> or silver.<name>.")
        checks = sources.validate(source, links, targets, self.column_values)
        existing: dict[str, list[str]] = {}
        for t in targets.values():
            existing.setdefault(t["dataset"], []).append(t["column"])
        report = self.impact_analysis(sources.to_changes(source, links, feed, existing))
        status = "fail" if any(c["status"] == "fail" for c in checks) else \
            "warn" if any(c["status"] == "warn" for c in checks) else "ok"
        return {"source": source["name"], "feed": feed, "status": status, "checks": checks, "impact": report}

    def impact_analysis(self, changes: list[dict], run_id: str | None = None) -> dict:
        """Apply proposed lineage changes to the latest run's lineage without executing anything."""
        run_id = run_id or self._latest_run_id()
        columns: dict[str, list[str]] = {}
        deps: dict[impact.Col, list[impact.Col]] = {}
        for r in self._rows("SELECT target_dataset, target_column, source_dataset, source_column "
                            "FROM lineage_columns WHERE run_id = ?", [run_id]):
            deps.setdefault((r["target_dataset"], r["target_column"]), []).append(
                (r["source_dataset"], r["source_column"]))
        if "data_dictionary" in self.tables:
            for r in self._rows("SELECT dataset, column_name, upstream FROM data_dictionary WHERE run_id = ? "
                                "ORDER BY position", [run_id]):
                if r["column_name"].startswith("_"):
                    continue
                columns.setdefault(r["dataset"], []).append(r["column_name"])
                if r["dataset"].startswith("bronze.") and r["upstream"]:
                    deps[(r["dataset"], r["column_name"])] = [impact.split_ref(u) for u in r["upstream"].split(", ")]
        for col, srcs in deps.items():
            for d, c in (col, *srcs):
                if c not in columns.setdefault(d, []):
                    columns[d].append(c)
        edges = self.lineage_graph(run_id=run_id)["edges"]
        try:
            report = impact.analyze(columns, deps, edges, changes)
        except impact.ChangeError as exc:
            raise QueryError(str(exc)) from exc
        return {"run_id": run_id, **report}


LAYER_ORDER = {"source": 0, "bronze": 1, "silver": 2, "gold": 3}


def _seconds(start: str | None, end: str | None) -> float | None:
    try:
        return round((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds(), 1)
    except (TypeError, ValueError):
        return None


def _diff(base: dict, cur: dict) -> dict:
    """Dataset rows/schema, data-quality and contract differences between two run snapshots."""
    datasets = []
    for d in sorted(set(base["rows"]) | set(cur["rows"]) | set(base["schema"]) | set(cur["schema"]),
                    key=lambda d: (LAYER_ORDER.get(d.split(".", 1)[0], 9), d)):
        b_rows, c_rows = base["rows"].get(d), cur["rows"].get(d)
        b_cols, c_cols = base["schema"].get(d, {}), cur["schema"].get(d, {})
        in_base, in_cur = d in base["rows"] or d in base["schema"], d in cur["rows"] or d in cur["schema"]
        delta = c_rows - b_rows if None not in (b_rows, c_rows) else None
        entry = {
            "dataset": d, "layer": d.split(".", 1)[0], "rows_base": b_rows, "rows": c_rows, "rows_delta": delta,
            "rows_pct": round(100 * delta / b_rows, 1) if delta is not None and b_rows else None,
            "columns_added": [c for c in c_cols if c not in b_cols] if in_base else [],
            "columns_removed": [c for c in b_cols if c not in c_cols] if in_cur else [],
            "type_changes": [{"column": c, "from": b_cols[c], "to": t} for c, t in c_cols.items()
                             if c in b_cols and b_cols[c] != t],
        }
        entry["status"] = ("added" if not in_base else "removed" if not in_cur else
                           "changed" if delta or entry["columns_added"] or entry["columns_removed"]
                           or entry["type_changes"] else "unchanged")
        datasets.append(entry)
    quality = [{"job_name": j, "rule": r, "base": base["quality"].get((j, r), 0), "current": cur["quality"].get((j, r), 0)}
               for j, r in sorted(set(base["quality"]) | set(cur["quality"]))]
    quality = [{**q, "delta": q["current"] - q["base"]} for q in quality if q["current"] != q["base"]]
    contracts = [{"dataset": d, "check": c, "base": base["contracts"].get((d, c)), "current": cur["contracts"].get((d, c))}
                 for d, c in sorted(set(base["contracts"]) | set(cur["contracts"]))]
    contracts = [c for c in contracts if c["base"] != c["current"]]
    count = lambda st: sum(d["status"] == st for d in datasets)  # noqa: E731
    return {
        "summary": {
            "datasets_added": count("added"), "datasets_removed": count("removed"),
            "row_changes": sum(bool(d["rows_delta"]) for d in datasets),
            "schema_changes": sum(len(d["columns_added"]) + len(d["columns_removed"]) + len(d["type_changes"])
                                  for d in datasets if d["status"] == "changed"),
            "quality_changes": len(quality),
            "contract_changes": len(contracts),
        },
        "datasets": datasets, "quality": quality, "contracts": contracts,
    }


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


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _columns(con: duckdb.DuckDBPyConnection, table: str) -> set[str]:
    return {r[0] for r in con.execute(f'DESCRIBE "{table}"').fetchall()}
