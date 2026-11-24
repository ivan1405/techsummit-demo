import pyarrow as pa
import pytest

from datalake_mcp.catalog import Catalog, QueryError

RUN = "run-1"


@pytest.fixture
def catalog():
    t = pa.Table.from_pylist
    return Catalog.from_tables({
        "sales": ("gold", t([{"order_id": "O1", "amount": 10.0}, {"order_id": "O2", "amount": 5.5}])),
        "lineage_runs": ("lineage", t([{"run_id": RUN, "started_at": "2026-01-01T00:00:00"}])),
        "lineage_edges": ("lineage", t([
            {"run_id": RUN, "job_name": "silver_orders", "source_dataset": "bronze.orders",
             "target_dataset": "silver.orders"},
            {"run_id": RUN, "job_name": "gold_sales", "source_dataset": "silver.orders",
             "target_dataset": "gold.sales"},
            {"run_id": RUN, "job_name": "silver_customers", "source_dataset": "bronze.customers",
             "target_dataset": "silver.customers"},
        ])),
        "lineage_jobs": ("lineage", t([
            {"run_id": RUN, "job_name": j, "layer": j.split("_")[0], "description": "", "inputs": i,
             "outputs": o, "rows_in": 3, "rows_out": 2, "rows_dropped": 1, "status": "COMPLETE",
             "started_at": "x", "ended_at": "y"}
            for j, i, o in [("silver_orders", "bronze.orders", "silver.orders"),
                            ("gold_sales", "silver.orders", "gold.sales"),
                            ("silver_customers", "bronze.customers", "silver.customers")]
        ])),
        "lineage_columns": ("lineage", t([
            {"run_id": RUN, "job_name": "gold_sales", "target_dataset": "gold.sales", "target_column": "amount",
             "source_dataset": "silver.orders", "source_column": "amount", "transformation": "direct copy"},
            {"run_id": RUN, "job_name": "silver_orders", "target_dataset": "silver.orders",
             "target_column": "amount", "source_dataset": "bronze.orders", "source_column": "quantity",
             "transformation": "quantity * unit_price"},
            {"run_id": RUN, "job_name": "silver_orders", "target_dataset": "silver.orders",
             "target_column": "amount", "source_dataset": "bronze.orders", "source_column": "unit_price",
             "transformation": "quantity * unit_price"},
        ])),
        "data_quality": ("lineage", t([
            {"run_id": RUN, "job_name": "silver_orders", "dataset": "bronze.orders", "rule": "dup",
             "rows_affected": 1}])),
    })


def test_query_returns_rows(catalog):
    res = catalog.query("SELECT order_id, amount FROM sales ORDER BY order_id", max_rows=1)
    assert res == {"columns": ["order_id", "amount"], "rows": [["O1", 10.0]], "row_count": 1, "truncated": True}


@pytest.mark.parametrize("sql", [
    "DROP TABLE sales",
    "INSERT INTO sales VALUES ('O3', 1.0)",
    "SELECT 1; DROP TABLE sales",
    "COPY sales TO '/tmp/x.csv'",
    "ATTACH '/tmp/x.db'",
])
def test_query_rejects_non_select(catalog, sql):
    with pytest.raises(QueryError):
        catalog.query(sql)


def test_query_cannot_read_local_files(catalog):
    with pytest.raises(QueryError):
        catalog.query("SELECT * FROM read_csv('/etc/passwd')")


def test_lineage_graph_filters_to_dataset(catalog):
    graph = catalog.lineage_graph("gold.sales")
    assert {n["id"] for n in graph["nodes"]} == {"bronze.orders", "silver.orders", "gold.sales"}
    assert graph["run_id"] == RUN


def test_column_lineage_traces_to_origins(catalog):
    res = catalog.column_lineage("gold.sales", "amount")
    assert res["origins"] == ["bronze.orders.quantity", "bronze.orders.unit_price"]
    assert len(res["steps"]) == 3


def test_data_quality(catalog):
    assert catalog.data_quality()["total_rows_dropped"] == 1
