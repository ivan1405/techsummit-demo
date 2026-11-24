"""MCP server exposing the data lake (gold datasets + lineage) to AI agents."""

import json
import logging
import os

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

from .catalog import DEFAULT_MAX_ROWS, Catalog, QueryError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

catalog = Catalog.from_env()

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


def _safe(fn, *args, **kwargs) -> str:
    try:
        return json.dumps(fn(*args, **kwargs), default=str)
    except QueryError as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool()
def list_tables() -> str:
    """List all queryable tables in the data lake with their layer, row count and description."""
    return _safe(catalog.list_tables)


@mcp.tool()
def describe_table(table: str) -> str:
    """Show the columns, types and 5 sample rows of a table."""
    return _safe(catalog.describe_table, table)


@mcp.tool()
def query(sql: str, max_rows: int = DEFAULT_MAX_ROWS) -> str:
    """Run ONE read-only DuckDB SELECT query over the lake tables and return columns + rows (max 1000)."""
    return _safe(catalog.query, sql, max_rows)


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
def get_data_quality(run_id: str = "") -> str:
    """Rows dropped per data-quality rule for the latest or given pipeline run."""
    return _safe(catalog.data_quality, run_id or None)


@mcp.tool()
def get_data_dictionary(dataset: str = "", search: str = "") -> str:
    """Data dictionary of every bronze/silver/gold dataset: description, row count and, per column, type,
    business description, null %, distinct count, sample values and upstream columns.
    Filter with dataset (e.g. 'gold.sales') or a free-text search (e.g. 'email')."""
    return _safe(catalog.data_dictionary, dataset or None, search or None)


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
