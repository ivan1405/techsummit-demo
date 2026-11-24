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


def test_impact_if_missing_skips_existing_columns(dict_catalog):
    rep = dict_catalog.impact_analysis([{"type": "add_column", "dataset": "gold.sales", "column": "amount",
                                         "if_missing": True}])
    assert rep["summary"]["added"] == 0


def test_contracts_combine_checks_and_freshness():
    from datetime import datetime, timezone

    t = pa.Table.from_pylist
    cat = Catalog.from_tables({
        "lineage_runs": ("lineage", t([{"run_id": RUN, "started_at": "2026-01-01T10:00:00+00:00"}])),
        "lineage_jobs": ("lineage", t([{"run_id": RUN, "job_name": f"gold_{n}", "layer": "gold", "description": "",
                                        "inputs": "", "outputs": f"gold.{n}", "rows_in": 1, "rows_out": 1,
                                        "rows_dropped": 0, "status": "COMPLETE", "started_at": "x",
                                        "ended_at": "2026-01-01T10:00:00+00:00"} for n in ("a", "b", "c")])),
        "contract_checks": ("lineage", t([
            {"run_id": RUN, "dataset": f"gold.{n}", "owner": "o", "check": "row_count", "expectation": "1..9",
             "observed": "1", "status": st, "max_age_minutes": age}
            for n, st, age in (("a", "pass", 60), ("b", "pass", 20), ("c", "fail", 600))])),
    })
    res = cat.contracts(now=datetime(2026, 1, 1, 10, 30, tzinfo=timezone.utc))
    by = {d["dataset"]: d for d in res["datasets"]}
    assert (by["gold.a"]["status"], by["gold.a"]["freshness"]) == ("green", "fresh")
    assert (by["gold.b"]["status"], by["gold.b"]["freshness"]) == ("amber", "stale")
    assert by["gold.c"]["status"] == "red" and by["gold.c"]["failed"] == 1 and by["gold.a"]["age_minutes"] == 30.0


@pytest.fixture
def runs_catalog():
    t = pa.Table.from_pylist
    r1, r2 = "run-a", "run-b"
    outputs = [(r1, "silver.orders", 10), (r1, "gold.sales", 8), (r1, "gold.old", 3),
               (r2, "silver.orders", 12), (r2, "gold.sales", 8), (r2, "gold.loyalty", 5)]
    dd = [(r1, "gold.sales", "amount", "float64"), (r1, "gold.sales", "note", "str"),
          (r2, "gold.sales", "amount", "str"), (r2, "gold.sales", "tier", "str"), (r2, "gold.sales", "_run_id", "str")]
    return Catalog.from_tables({
        "lineage_runs": ("lineage", t([
            {"run_id": r1, "started_at": "2026-01-01T10:00:00+00:00", "ended_at": "2026-01-01T10:00:04+00:00",
             "status": "COMPLETE", "jobs": 3},
            {"run_id": r2, "started_at": "2026-01-01T11:00:00+00:00", "ended_at": "2026-01-01T11:00:06+00:00",
             "status": "COMPLETE", "jobs": 3}])),
        "lineage_outputs": ("lineage", t([{"run_id": r, "job_name": f"job_{d}", "dataset": d, "rows": n, "columns": 2}
                                          for r, d, n in outputs])),
        "data_dictionary": ("lineage", t([{"run_id": r, "dataset": d, "column_name": c, "data_type": ty, "position": i}
                                          for i, (r, d, c, ty) in enumerate(dd)])),
        "data_quality": ("lineage", t([
            {"run_id": r1, "job_name": "silver_orders", "dataset": "bronze.orders", "rule": "dup", "rows_affected": 1},
            {"run_id": r2, "job_name": "silver_orders", "dataset": "bronze.orders", "rule": "dup", "rows_affected": 4}])),
        "contract_checks": ("lineage", t([
            {"run_id": r1, "dataset": "gold.sales", "owner": "o", "check": "schema", "expectation": "", "observed": "",
             "status": "pass", "max_age_minutes": 60},
            {"run_id": r2, "dataset": "gold.sales", "owner": "o", "check": "schema", "expectation": "", "observed": "",
             "status": "fail", "max_age_minutes": 60}])),
    })


def test_run_diff_defaults_to_latest_vs_previous(runs_catalog):
    res = runs_catalog.run_diff()
    assert (res["run"]["run_id"], res["base"]["run_id"]) == ("run-b", "run-a")
    by = {d["dataset"]: d for d in res["datasets"]}
    assert by["gold.loyalty"]["status"] == "added" and by["gold.old"]["status"] == "removed"
    assert (by["silver.orders"]["rows_delta"], by["silver.orders"]["rows_pct"]) == (2, 20.0)
    sales = by["gold.sales"]
    assert sales["status"] == "changed" and sales["columns_added"] == ["tier"] and sales["columns_removed"] == ["note"]
    assert sales["type_changes"] == [{"column": "amount", "from": "float64", "to": "str"}]
    assert res["quality"] == [{"job_name": "silver_orders", "rule": "dup", "base": 1, "current": 4, "delta": 3}]
    assert res["contracts"] == [{"dataset": "gold.sales", "check": "schema", "base": "pass", "current": "fail"}]
    assert res["summary"] == {"datasets_added": 1, "datasets_removed": 1, "row_changes": 1, "schema_changes": 3,
                              "quality_changes": 1, "contract_changes": 1}
    assert [d["layer"] for d in res["datasets"]] == ["silver", "gold", "gold", "gold"]


def test_run_history_newest_first_with_changes(runs_catalog):
    res = runs_catalog.run_history()
    latest, first = res["runs"]
    assert res["total_runs"] == 2 and latest["run_id"] == "run-b" and latest["base_run_id"] == "run-a"
    assert (latest["gold_rows"], latest["rows_dropped"], latest["contract_failures"], latest["duration_s"]) == (13, 4, 1, 6.0)
    assert latest["changes"]["datasets_added"] == 1 and first["changes"] is None


def test_run_diff_errors(runs_catalog):
    with pytest.raises(QueryError, match="first run"):
        runs_catalog.run_diff("run-a")
    with pytest.raises(QueryError, match="Unknown run"):
        runs_catalog.run_diff("nope")


def test_explain_column_combines_dictionary_upstream_and_downstream(dict_catalog):
    res = dict_catalog.explain_column("gold.sales", "amount")
    assert res["description"] == "Order line amount in EUR." and res["data_type"] == "float64"
    assert {s["source_column"] for s in res["steps"]} >= {"quantity", "unit_price"}
    assert res["downstream"] == [{"dataset": "gold.daily", "column": "revenue", "from": "gold.sales.amount",
                                  "transformation": "x"}]
    assert "source.orders.csv.quantity" in res["origins"]
    assert res["built_by"] == ["job_gold.sales"] and res["contract"] is None


def test_explain_column_without_column_lineage_falls_back_to_upstream(dict_catalog):
    res = dict_catalog.explain_column("bronze.orders", "quantity")
    assert res["steps"] == [] and res["origins"] == ["source.orders.csv.quantity"]
    assert res["downstream"][0]["dataset"] == "silver.orders"
    with pytest.raises(QueryError, match="Unknown column"):
        dict_catalog.explain_column("gold.sales", "nope")


@pytest.fixture
def pii_catalog():
    t = pa.Table.from_pylist
    dd = [{"run_id": RUN, "dataset": "gold.customer_360", "layer": "gold", "column_name": c, "position": i,
           "data_type": "str", "description": c, "null_pct": 0.0, "distinct_count": 2, "sample_values": s,
           "upstream": "", "transformation": "", "pii": tag, "pii_origin": f"bronze.customers.{c}" if tag else ""}
          for i, (c, s, tag) in enumerate([("customer_id", "C1", ""), ("full_name", "Ana Diaz", "name"),
                                           ("email", "ana@x.com", "email"), ("phone", "+34 600", "phone")])]
    return Catalog.from_tables({
        "lineage_runs": ("lineage", t([{"run_id": RUN, "started_at": "2026-01-01T00:00:00", "ended_at": "x",
                                        "status": "COMPLETE"}])),
        "data_dictionary": ("lineage", t(dd)),
        "lineage_edges": ("lineage", t([{"run_id": RUN, "job_name": "gold_customer_360",
                                         "source_dataset": "bronze.customers", "target_dataset": "gold.customer_360"}])),
        "lineage_jobs": ("lineage", t([{"run_id": RUN, "job_name": "gold_customer_360", "layer": "gold",
                                        "description": "builds", "inputs": "bronze.customers",
                                        "outputs": "gold.customer_360", "rows_in": 2, "rows_out": 2,
                                        "rows_dropped": 0, "status": "COMPLETE", "started_at": "x",
                                        "ended_at": "y"}])),
        "lineage_columns": ("lineage", t([{"run_id": RUN, "job_name": "gold_customer_360",
                                           "target_dataset": "gold.customer_360", "target_column": "email",
                                           "source_dataset": "bronze.customers", "source_column": "email",
                                           "transformation": "direct copy"}])),
        "customer_360": ("gold", t([{"customer_id": "C1", "full_name": "Ana Diaz", "email": "ana@x.com",
                                     "phone": "+34 600"},
                                    {"customer_id": "C2", "full_name": None, "email": None, "phone": None}])),
    })


def test_reader_role_masks_pii_in_sql_samples_and_dictionary(pii_catalog):
    sql = "SELECT * FROM customer_360 ORDER BY customer_id"
    assert pii_catalog.query(sql)["rows"] == [["C1", "A***", "a***@x.com", "***"], ["C2", None, None, None]]
    assert pii_catalog.query(sql, role="analyst")["rows"][0] == ["C1", "Ana Diaz", "ana@x.com", "+34 600"]
    assert pii_catalog.query("SELECT count(DISTINCT email) FROM customer_360")["rows"] == [[1]]
    desc = pii_catalog.describe_table("customer_360")
    assert {c["name"]: c.get("pii") for c in desc["columns"]}["email"] == "email"
    assert desc["sample"]["rows"][0][2] == "a***@x.com"
    cols = {c["column_name"]: c for c in pii_catalog.data_dictionary("gold.customer_360")["datasets"][0]["columns"]}
    assert cols["email"]["sample_values"] == "***" and cols["customer_id"]["sample_values"] == "C1"
    assert cols["email"]["pii_origin"] == "bronze.customers.email"
    assert pii_catalog.query("SELECT sample_values FROM data_dictionary WHERE column_name = 'email'")["rows"] == [["***"]]
    analyst = pii_catalog.data_dictionary("gold.customer_360", role="analyst")["datasets"][0]["columns"]
    assert analyst[2]["sample_values"] == "ana@x.com"


def test_role_from_headers_requires_the_analyst_token(monkeypatch):
    from datalake_mcp import server
    monkeypatch.setattr(server, "ANALYST_TOKEN", "s3cret")
    assert server.role_from_headers({"x-data-role": "analyst", "x-data-token": "s3cret"}) == "analyst"
    assert server.role_from_headers({"x-data-role": "analyst", "x-data-token": "nope"}) == "reader"
    assert server.role_from_headers({"x-data-role": "analyst"}) == "reader"
    monkeypatch.setattr(server, "ANALYST_TOKEN", "")
    assert server.role_from_headers({"x-data-role": "analyst", "x-data-token": ""}) == "reader"
