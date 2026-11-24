"""MCP server exposing the data lake (gold datasets + lineage) to AI agents."""

import json
import logging
import os

import hmac

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import sources
from .catalog import ANALYST, DEFAULT_MAX_ROWS, READER, Catalog, QueryError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

catalog = Catalog.from_env()
staged = sources.SourceStore()

mcp = FastMCP(
    "datalake",
    instructions=(
        "Read-only access to the Tech Summit data lake. Business data lives in the gold tables "
        "(sales, customer_360, product_performance, daily_revenue, review_insights). Lineage and data "
        "quality live in lineage_* and data_quality tables, and get_lineage/get_column_lineage explain "
        "where any dataset or column comes from. get_data_dictionary documents every column, and "
        "analyze_change_impact previews how proposed requirements would change lineage. Always call list_tables/describe_table before writing SQL."
    ),
    host=os.getenv("MCP_HOST", "0.0.0.0"),
    port=int(os.getenv("MCP_PORT", "8000")),
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


ANALYST_TOKEN = os.getenv("MCP_ANALYST_TOKEN", "")


def role_from_headers(headers) -> str:
    """Callers get unmasked PII only with X-Data-Role: analyst plus the shared analyst token."""
    token = headers.get("x-data-token", "")
    if headers.get("x-data-role") == ANALYST and ANALYST_TOKEN and hmac.compare_digest(token, ANALYST_TOKEN):
        return ANALYST
    return READER


def _role(ctx: Context) -> str:
    try:
        request = ctx.request_context.request
    except (AttributeError, ValueError):
        return READER
    return role_from_headers(request.headers) if request is not None else READER


def _safe(fn, *args, **kwargs) -> str:
    try:
        return json.dumps(fn(*args, **kwargs), default=str)
    except (QueryError, sources.SourceError) as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool()
def list_tables() -> str:
    """List all queryable tables in the data lake with their layer, row count and description."""
    return _safe(catalog.list_tables)


@mcp.tool()
def describe_table(table: str, ctx: Context) -> str:
    """Show the columns, types (with PII tags) and 5 sample rows of a table."""
    return _safe(catalog.describe_table, table, _role(ctx))


@mcp.tool()
def query(sql: str, ctx: Context, max_rows: int = DEFAULT_MAX_ROWS) -> str:
    """Run ONE read-only DuckDB SELECT query over the lake tables and return columns + rows (max 1000).
    For the reader role, columns tagged as PII (emails, names, ...) come back masked."""
    return _safe(catalog.query, sql, max_rows, _role(ctx))


@mcp.tool()
def get_lineage(dataset: str = "", run_id: str = "") -> str:
    """Dataset-level lineage graph (nodes, edges, jobs with row counts) for the latest or given run.

    Dataset ids look like 'source.orders.csv', 'bronze.orders', 'silver.orders', 'gold.customer_360'.
    Pass a dataset to restrict the graph to its upstream and downstream.
    """
    return _safe(catalog.lineage_graph, dataset or None, run_id or None)


@mcp.tool()
def get_column_lineage(dataset: str, column: str, run_id: str = "") -> str:
    """Trace a column (e.g. dataset='gold.customer_360', column='revenue') back to its source columns,
    listing the transformation applied at every hop."""
    return _safe(catalog.column_lineage, dataset, column, run_id or None)


@mcp.tool()
def explain_column(dataset: str, column: str, ctx: Context) -> str:
    """Explain one column (e.g. dataset='gold.customer_360', column='revenue'): business description, type,
    profile, how it is calculated (each upstream hop with its transformation, back to the source columns),
    where it is used downstream, data-quality rules of the job that builds it and the dataset's contract status.
    Use it for 'how is X calculated', 'where does X come from' or 'can I trust X'."""
    return _safe(catalog.explain_column, dataset, column, _role(ctx))


@mcp.tool()
def get_data_quality(run_id: str = "") -> str:
    """Rows dropped per data-quality rule for the latest or given pipeline run."""
    return _safe(catalog.data_quality, run_id or None)


@mcp.tool()
def get_contracts() -> str:
    """Data-contract status of every gold dataset: owner, checks (row count, schema, types, not-null,
    unique key) of the latest run, and freshness vs. the contract's max age. status is green, amber
    (stale) or red (failed check or expired). Use it to answer 'can I trust this table?'."""
    return _safe(catalog.contracts)


@mcp.tool()
def get_run_history(limit: int = 20) -> str:
    """Pipeline run history, newest first: status, duration, datasets written, gold rows, rows dropped,
    contract failures and a summary of what changed vs the previous run (datasets added/removed, row-count
    and schema changes, data-quality and contract changes)."""
    return _safe(catalog.run_history, limit)


@mcp.tool()
def get_alerts(limit: int = 20) -> str:
    """Operational alerts: active alerts of the latest pipeline run (late source deliveries vs their SLA, failed data
    contracts, failed onboarded sources), the most recent alerts across runs, and each source's last delivery. Use it
    to answer 'is any data late?' or 'are there any alerts?'."""
    return _safe(catalog.alerts, limit)


@mcp.tool()
def compare_runs(run_id: str = "", base_run_id: str = "") -> str:
    """Run-to-run diff. Defaults: the latest run vs the run before it. Per dataset: rows before/after, delta
    and %, columns added/removed and type changes; plus changed data-quality rule counts and contract checks.
    Use it for 'what changed since the last run?'."""
    return _safe(catalog.run_diff, run_id or None, base_run_id or None)


@mcp.tool()
def get_data_dictionary(ctx: Context, dataset: str = "", search: str = "") -> str:
    """Data dictionary of every bronze/silver/gold dataset: description, row count and, per column, type,
    business description, null %, distinct count, sample values, upstream columns and PII tag (pii, pii_origin).
    Filter with dataset (e.g. 'gold.sales') or a free-text search (e.g. 'email')."""
    return _safe(catalog.data_dictionary, dataset or None, search or None, role=_role(ctx))


@mcp.tool()
def analyze_change_impact(changes: list[dict]) -> str:
    """Preview, without executing anything, how proposed changes would alter lineage.

    Each change is an object with a 'type':
    - add_dataset {dataset, inputs: [datasets]}            e.g. dataset='gold.returns'
    - remove_dataset {dataset}
    - add_column {dataset, column, sources: ['layer.dataset.column'], transformation}
    - remove_column {dataset, column}
    - rename_column {dataset, column, new_name}
    - change_logic {dataset, column, transformation}
    Returns the added/removed datasets and edges plus every downstream column that would be
    broken (all inputs lost) or impacted (some input lost, renamed or with new logic).
    """
    return _safe(catalog.impact_analysis, changes)


def _stage(kind: str, name: str, filename: str, content: str, url: str, table: str, query: str,
           docs: list[dict] | None, doc: dict | None) -> dict:
    if kind == "file":
        label, data = filename, sources.read_file(filename, content)
    elif kind == "json":
        label, data = f"{name}.json", sources.read_json(content)
    elif kind == "database":
        label, data = f"{name}.db", sources.read_db(url, table, query)
    elif kind == "records":
        label, data = filename or f"{name}.db", sources.read_json(content)
    elif kind == "schema":
        try:
            columns = json.loads(content or "[]")
        except json.JSONDecodeError as exc:
            raise sources.SourceError(f"Invalid schema JSON: {exc}") from exc
        label, data = filename or f"{name}.schema", sources.schema_table(columns)
    else:
        raise sources.SourceError("kind must be file, json, database, records or schema.")
    return staged.stage(name, kind, label, data, docs, doc)


@mcp.tool()
def stage_source(kind: str, name: str, filename: str = "", content: str = "", url: str = "", table: str = "",
                 query: str = "", docs: list[dict] | None = None, doc: dict | None = None) -> str:
    """Stage and profile a candidate data source WITHOUT ingesting it.

    kind='file' (filename .csv/.json/.jsonl + content), kind='json' (content = a JSON object or array)
    or kind='database' (url postgresql://... or sqlite:///..., plus a table or a single SELECT query;
    read-only, at most 5000 rows), kind='records' (content = JSON array already read from a named
    connection, filename = its source label) or kind='schema' (content = JSON [{name, type}] of a source
    that is only documented so far: links are checked by name and type, not values).
    docs: optional documented columns [{name, type, description, links_to}] (e.g. from Confluence);
    doc: {title, url} of that documentation. name is the snake_case dataset name, e.g. 'support_tickets'.
    """
    return _safe(_stage, kind, name, filename, content, url, table, query, docs, doc)


@mcp.tool()
def get_staged_source(name: str) -> str:
    """Profile and sample rows of a staged source."""
    return _safe(lambda: {k: v for k, v in staged.get(name).items() if k != "table"})


@mcp.tool()
def suggest_source_links(name: str) -> str:
    """Suggest how each column of a staged source links to existing silver/gold columns
    (join_key, maps_to or new), using name similarity and real value overlap."""
    return _safe(lambda: catalog.suggest_links(staged.get(name)))


@mcp.tool()
def validate_source_links(name: str, links: list[dict], feed: str = "") -> str:
    """Validate confirmed links of a staged source against real data and preview the lineage impact.

    links: [{source_column, relation: join_key|maps_to|new, target: 'layer.dataset.column'}].
    feed: dataset the source feeds (existing or new, default gold.<name>).
    """
    return _safe(lambda: catalog.validate_links(staged.get(name), links, feed))


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    try:
        tables = len(catalog.list_tables())
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"status": "degraded", "error": str(exc)}, status_code=503)
    return JSONResponse({"status": "ok", "tables": tables})


def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
