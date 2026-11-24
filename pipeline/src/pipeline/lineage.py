"""Data lineage: OpenLineage events + queryable lineage tables for reporting.

Every pipeline step is recorded as a job with its input/output datasets, column-level
lineage, row counts and data-quality drops. Events follow the OpenLineage spec so they can
be shipped to any compatible backend (e.g. Marquez) via OPENLINEAGE_URL.
"""

import json
import logging
import urllib.request
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd

log = logging.getLogger(__name__)

NAMESPACE = "techsummit"
PRODUCER = "https://github.com/ivan1405/techsummit-demo/tree/main/pipeline"
RUN_EVENT_SCHEMA = "https://openlineage.io/spec/2-0-2/OpenLineage.json#/definitions/RunEvent"
SCHEMA_FACET = "https://openlineage.io/spec/facets/1-1-1/SchemaDatasetFacet.json"
COLUMN_LINEAGE_FACET = "https://openlineage.io/spec/facets/1-2-0/ColumnLineageDatasetFacet.json"
OUTPUT_STATS_FACET = "https://openlineage.io/spec/facets/1-0-2/OutputStatisticsOutputDatasetFacet.json"
DOC_FACET = "https://openlineage.io/spec/facets/1-0-1/DocumentationJobFacet.json"

# {output_column: [(input_dataset, input_column, transformation), ...]}
ColumnLineage = dict[str, list[tuple[str, str, str]]]


def dataset_namespace(dataset: str) -> tuple[str, str]:
    """Map 'layer.name' keys to OpenLineage (namespace, name)."""
    layer, name = dataset.split(".", 1)
    if layer == "source":
        return "file://data/sources", name
    return f"s3://{layer}", name


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _arrow_type(dtype) -> str:
    return str(dtype).replace("object", "string")


@dataclass
class Job:
    name: str
    layer: str
    description: str
    inputs: list[str] = field(default_factory=list)
    outputs: list[dict] = field(default_factory=list)
    rows_in: int = 0
    rows_out: int = 0
    quality: dict[str, int] = field(default_factory=dict)
    started_at: str = ""
    ended_at: str = ""
    status: str = "RUNNING"

    def input(self, dataset: str, rows: int | None = None) -> None:
        self.inputs.append(dataset)
        if rows is not None:
            self.rows_in += rows

    def output(self, dataset: str, df: pd.DataFrame | None = None, rows: int | None = None,
               columns: ColumnLineage | None = None) -> None:
        count = len(df) if df is not None else (rows or 0)
        schema = [(c, _arrow_type(t)) for c, t in df.dtypes.items()] if df is not None else []
        self.outputs.append({"dataset": dataset, "rows": count, "schema": schema, "columns": columns or {}})
        self.rows_out += count

    def dropped(self, quality: dict[str, int]) -> None:
        for rule, n in quality.items():
            if n:
                self.quality[rule] = self.quality.get(rule, 0) + int(n)


class LineageRecorder:
    def __init__(self, run_id: str | None = None, emit_url: str | None = None):
        self.run_id = run_id or str(uuid.uuid4())
        self.emit_url = (emit_url or "").rstrip("/")
        self.started_at = _now()
        self.jobs: list[Job] = []
        self.events: list[dict] = []

    @contextmanager
    def job(self, name: str, layer: str, description: str):
        job = Job(name=name, layer=layer, description=description, started_at=_now())
        self._emit(self._event("START", job))
        try:
            yield job
        except Exception:
            job.status, job.ended_at = "FAIL", _now()
            self._emit(self._event("FAIL", job))
            self.jobs.append(job)
            raise
        job.status, job.ended_at = "COMPLETE", _now()
        self._emit(self._event("COMPLETE", job))
        self.jobs.append(job)

    def _event(self, event_type: str, job: Job) -> dict:
        def ds(key: str) -> dict:
            ns, name = dataset_namespace(key)
            return {"namespace": ns, "name": name, "facets": {}}

        outputs = []
        for out in job.outputs:
            d = ds(out["dataset"])
            if out["schema"]:
                d["facets"]["schema"] = {
                    "_producer": PRODUCER, "_schemaURL": SCHEMA_FACET,
                    "fields": [{"name": n, "type": t} for n, t in out["schema"]],
                }
            if out["columns"]:
                d["facets"]["columnLineage"] = {
                    "_producer": PRODUCER, "_schemaURL": COLUMN_LINEAGE_FACET,
                    "fields": {
                        col: {"inputFields": [
                            {**dict(zip(("namespace", "name"), dataset_namespace(src))), "field": src_col,
                             "transformations": [{"type": "DIRECT", "subtype": "TRANSFORMATION",
                                                  "description": how}]}
                            for src, src_col, how in sources
                        ]}
                        for col, sources in out["columns"].items()
                    },
                }
            d["outputFacets"] = {"outputStatistics": {
                "_producer": PRODUCER, "_schemaURL": OUTPUT_STATS_FACET, "rowCount": out["rows"]}}
            outputs.append(d)

        return {
            "eventType": event_type,
            "eventTime": job.ended_at or job.started_at,
            "producer": PRODUCER,
            "schemaURL": RUN_EVENT_SCHEMA,
            "run": {"runId": self.run_id, "facets": {}},
            "job": {"namespace": NAMESPACE, "name": job.name, "facets": {
                "documentation": {"_producer": PRODUCER, "_schemaURL": DOC_FACET, "description": job.description}}},
            "inputs": [ds(i) for i in job.inputs],
            "outputs": outputs if event_type != "START" else [],
        }

    def _emit(self, event: dict) -> None:
        self.events.append(event)
        if not self.emit_url:
            return
        req = urllib.request.Request(
            f"{self.emit_url}/api/v1/lineage", data=json.dumps(event).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5).close()
        except Exception as exc:  # noqa: BLE001 - lineage backend is optional
            log.warning("Could not send OpenLineage event to %s: %s", self.emit_url, exc)

    def tables(self) -> dict[str, pd.DataFrame]:
        """Flatten the recorded run into tables suitable for reporting."""
        jobs, edges, columns, quality = [], [], [], []
        for j in self.jobs:
            jobs.append({
                "run_id": self.run_id, "job_name": j.name, "layer": j.layer, "description": j.description,
                "inputs": ", ".join(j.inputs), "outputs": ", ".join(o["dataset"] for o in j.outputs),
                "rows_in": j.rows_in, "rows_out": j.rows_out, "rows_dropped": sum(j.quality.values()),
                "status": j.status, "started_at": j.started_at, "ended_at": j.ended_at,
            })
            for out in j.outputs:
                for src in j.inputs:
                    edges.append({"run_id": self.run_id, "job_name": j.name, "source_dataset": src,
                                  "target_dataset": out["dataset"]})
                for col, sources in out["columns"].items():
                    for src, src_col, how in sources:
                        columns.append({
                            "run_id": self.run_id, "job_name": j.name, "target_dataset": out["dataset"],
                            "target_column": col, "source_dataset": src, "source_column": src_col,
                            "transformation": how,
                        })
            for rule, n in j.quality.items():
                quality.append({"run_id": self.run_id, "job_name": j.name, "dataset": ", ".join(j.inputs),
                                "rule": rule, "rows_affected": n})
        status = "FAIL" if any(j.status == "FAIL" for j in self.jobs) else "COMPLETE"
        runs = [{"run_id": self.run_id, "started_at": self.started_at, "ended_at": _now(), "status": status,
                 "jobs": len(self.jobs)}]
        return {
            "lineage_runs": pd.DataFrame(runs),
            "lineage_jobs": pd.DataFrame(jobs),
            "lineage_edges": pd.DataFrame(edges),
            "lineage_columns": pd.DataFrame(columns),
            "data_quality": pd.DataFrame(quality, columns=["run_id", "job_name", "dataset", "rule", "rows_affected"]),
        }
