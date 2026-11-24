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


@pytest.fixture
def dict_catalog():
    t = pa.Table.from_pylist
    run = [{"run_id": RUN, "started_at": "2026-01-01T00:00:00"}]
    cols = [  # (target_ds, target_col, source_ds, source_col)
        ("silver.orders", "amount", "bronze.orders", "quantity"),
        ("silver.orders", "amount", "bronze.orders", "unit_price"),
        ("silver.orders", "quantity", "bronze.orders", "quantity"),
        ("gold.sales", "amount", "silver.orders", "amount"),
        ("gold.sales", "quantity", "silver.orders", "quantity"),
        ("gold.daily", "revenue", "gold.sales", "amount"),
    ]
    edges = sorted({(s, d) for d, _, s, _ in cols} | {("source.orders.csv", "bronze.orders")})
    dd = [
        {"run_id": RUN, "dataset": "bronze.orders", "layer": "bronze", "column_name": c, "position": i,
         "data_type": "str", "description": f"raw {c}", "null_pct": 0.0, "distinct_count": 3,
         "sample_values": "1, 2", "upstream": f"source.orders.csv.{c}", "transformation": "landed as-is"}
        for i, c in enumerate(["quantity", "unit_price"])
    ] + [
        {"run_id": RUN, "dataset": "gold.sales", "layer": "gold", "column_name": "amount", "position": 0,
         "data_type": "float64", "description": "Order line amount in EUR.", "null_pct": 0.0,
         "distinct_count": 9, "sample_values": "10.0", "upstream": "silver.orders.amount",
         "transformation": "direct copy"},
        {"run_id": RUN, "dataset": "gold.sales", "layer": "gold", "column_name": "_run_id", "position": 1,
         "data_type": "str", "description": "run", "null_pct": 0.0, "distinct_count": 1, "sample_values": RUN,
         "upstream": "", "transformation": ""},
    ]
    return Catalog.from_tables({
        "lineage_runs": ("lineage", t(run)),
        "lineage_edges": ("lineage", t([{"run_id": RUN, "job_name": f"job_{d}", "source_dataset": s,
                                         "target_dataset": d} for s, d in edges])),
        "lineage_jobs": ("lineage", t([{"run_id": RUN, "job_name": f"job_{d}", "layer": d.split(".")[0],
                                        "description": f"builds {d}", "inputs": s, "outputs": d, "rows_in": 3,
                                        "rows_out": 2, "rows_dropped": 0, "status": "COMPLETE",
                                        "started_at": "x", "ended_at": "y"} for s, d in edges])),
        "lineage_columns": ("lineage", t([{"run_id": RUN, "job_name": f"job_{d}", "target_dataset": d,
                                           "target_column": c, "source_dataset": s, "source_column": sc,
                                           "transformation": "x"} for d, c, s, sc in cols])),
        "data_dictionary": ("lineage", t(dd)),
    })


def test_data_dictionary_groups_columns_and_searches(dict_catalog):
    res = dict_catalog.data_dictionary()
    sales = next(d for d in res["datasets"] if d["dataset"] == "gold.sales")
    assert sales["rows"] == 2 and [c["column_name"] for c in sales["columns"]] == ["amount", "_run_id"]
    found = dict_catalog.data_dictionary(search="EUR")["datasets"]
    assert [(d["dataset"], [c["column_name"] for c in d["columns"]]) for d in found] == [("gold.sales", ["amount"])]
    with pytest.raises(QueryError):
        dict_catalog.data_dictionary(dataset="gold.nope")


def _by_col(report):
    return {(c["dataset"], c["column"]): c for c in report["columns"]}


def test_impact_remove_column_breaks_and_impacts_downstream(dict_catalog):
    rep = dict_catalog.impact_analysis([{"type": "remove_column", "dataset": "bronze.orders", "column": "quantity"}])
    cols = _by_col(rep)
    assert cols[("bronze.orders", "quantity")]["status"] == "removed"
    assert cols[("silver.orders", "quantity")]["status"] == "broken"
    assert cols[("gold.sales", "quantity")]["status"] == "broken"
    assert cols[("silver.orders", "amount")]["status"] == "impacted"
    assert cols[("gold.daily", "revenue")]["status"] == "impacted"
    nodes = {n["id"]: n["status"] for n in rep["nodes"]}
    assert nodes["gold.sales"] == "broken" and nodes["gold.daily"] == "impacted"
    assert nodes["source.orders.csv"] == "unchanged"
    assert nodes["bronze.orders"] == "modified" and nodes["silver.orders"] == "broken"


def test_impact_add_dataset_and_column_adds_nodes_and_edges(dict_catalog):
    rep = dict_catalog.impact_analysis([
        {"type": "add_column", "dataset": "gold.returns", "column": "returned_amount",
         "sources": ["gold.sales.amount"], "transformation": "SUM where returned"},
        {"type": "rename_column", "dataset": "silver.orders", "column": "quantity", "new_name": "units"},
    ])
    nodes = {n["id"]: n["status"] for n in rep["nodes"]}
    assert nodes["gold.returns"] == "added"
    assert {"source": "gold.sales", "target": "gold.returns", "status": "added"} in rep["edges"]
    cols = _by_col(rep)
    assert cols[("gold.sales", "quantity")]["reason"].startswith("update mapping")
    assert rep["summary"]["added"] == 1 and rep["summary"]["renamed"] == 1


def test_impact_new_source_is_added_without_warning(dict_catalog):
    rep = dict_catalog.impact_analysis([
        {"type": "add_dataset", "dataset": "bronze.returns", "inputs": ["source.returns.csv"]},
        {"type": "add_dataset", "dataset": "gold.returns", "inputs": ["silver.nope"]},
    ])
    nodes = {n["id"]: n["status"] for n in rep["nodes"]}
    assert nodes["source.returns.csv"] == "added" and nodes["bronze.returns"] == "added"
    assert rep["warnings"] == ["gold.returns reads from unknown dataset silver.nope."]


def test_impact_rejects_unknown_columns(dict_catalog):
    with pytest.raises(QueryError, match="Unknown column"):
        dict_catalog.impact_analysis([{"type": "remove_column", "dataset": "gold.sales", "column": "nope"}])
