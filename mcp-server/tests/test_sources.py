import json
import sqlite3

import pyarrow as pa
import pytest

from datalake_mcp import sources
from datalake_mcp.catalog import Catalog

RUN = "run-1"
CUSTOMERS = [f"C{i:04d}" for i in range(1, 11)]


def test_read_file_csv_infers_types_and_snake_cases():
    t = sources.read_file("t.csv", "Ticket ID,Cust,Refund EUR\nT1,C0001,10.5\nT2,C0002,\n")
    assert t.column_names == ["ticket_id", "cust", "refund_eur"]
    assert t.column("refund_eur").to_pylist() == [10.5, None]


def test_read_json_object_flattens_and_unwraps_single_list():
    one = sources.read_json('{"customer": {"id": "C0001", "tier": "gold"}, "points": 10}')
    assert one.column_names == ["customer_id", "customer_tier", "points"]
    wrapped = sources.read_json(json.dumps({"members": [{"id": 1}, {"id": 2}]}))
    assert wrapped.num_rows == 2


def test_read_db_sqlite_is_read_only_and_rejects_writes(tmp_path):
    db = tmp_path / "erp.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE tickets (ticket_id TEXT, cust TEXT)")
    con.executemany("INSERT INTO tickets VALUES (?, ?)", [("T1", "C0001"), ("T2", "C0002")])
    con.commit()
    con.close()
    assert sources.read_db(f"sqlite:///{db}", table="tickets").num_rows == 2
    assert sources.read_db(f"sqlite:///{db}", query="SELECT cust FROM tickets WHERE ticket_id = 'T1'").num_rows == 1
    for bad in ("DELETE FROM tickets", "SELECT 1; DROP TABLE tickets"):
        with pytest.raises(sources.SourceError):
            sources.read_db(f"sqlite:///{db}", query=bad)
    with pytest.raises(sources.SourceError):
        sources.read_db(f"sqlite:///{db}", table="tickets; drop")


@pytest.fixture
def catalog():
    t = pa.Table.from_pylist
    customers = [{"customer_id": c, "country": "ES" if i % 2 else "FR"} for i, c in enumerate(CUSTOMERS)]
    sales = [{"order_id": f"O{i:03d}", "customer_id": CUSTOMERS[i % 10], "amount": 10.0 * i} for i in range(30)]
    dd = [
        {"run_id": RUN, "dataset": ds, "layer": ds.split(".")[0], "column_name": col, "position": i,
         "data_type": typ, "description": f"{col} of {ds}", "null_pct": 0.0, "distinct_count": n,
         "sample_values": "", "upstream": "", "transformation": ""}
        for ds, cols in {
            "silver.customers": [("customer_id", "string", 10), ("country", "string", 2)],
            "gold.sales": [("order_id", "string", 30), ("customer_id", "string", 10), ("amount", "float64", 30)],
        }.items() for i, (col, typ, n) in enumerate(cols)
    ]
    edges = [("silver.customers", "gold.sales")]
    return Catalog.from_tables({
        "customers": ("silver", t(customers)),
        "sales": ("gold", t(sales)),
        "lineage_runs": ("lineage", t([{"run_id": RUN, "started_at": "2026-01-01"}])),
        "lineage_edges": ("lineage", t([{"run_id": RUN, "job_name": "j", "source_dataset": s, "target_dataset": d}
                                        for s, d in edges])),
        "lineage_jobs": ("lineage", t([{"run_id": RUN, "job_name": "j", "layer": "gold", "description": "builds",
                                        "inputs": "silver.customers", "outputs": o, "rows_in": 10, "rows_out": n,
                                        "rows_dropped": 0, "status": "COMPLETE", "started_at": "x", "ended_at": "y"}
                                       for o, n in [("silver.customers", 10), ("gold.sales", 30)]])),
        "lineage_columns": ("lineage", t([{"run_id": RUN, "job_name": "j", "target_dataset": "gold.sales",
                                           "target_column": "customer_id", "source_dataset": "silver.customers",
                                           "source_column": "customer_id", "transformation": "join"}])),
        "data_dictionary": ("lineage", t(dd)),
    })


@pytest.fixture
def tickets():
    store = sources.SourceStore()
    rows = [{"ticket_id": f"T{i}", "cust": CUSTOMERS[i % 10] if i < 9 else "C9999", "country": "ES",
             "priority": "high"} for i in range(10)]
    store.stage("tickets", "json", "tickets.json", sources.to_table(rows))
    return store.get("tickets")


def test_suggest_finds_join_key_by_values_and_maps_by_name(catalog, tickets):
    links = {lk["source_column"]: lk for lk in catalog.suggest_links(tickets)["links"]}
    assert links["cust"]["relation"] == "join_key"
    assert links["cust"]["target"] == "silver.customers.customer_id"
    assert links["country"]["relation"] == "maps_to" and links["country"]["target"] == "silver.customers.country"
    assert links["priority"]["relation"] == "new" and links["priority"]["target"] is None


def test_validate_reports_orphans_bad_targets_and_previews_impact(catalog, tickets):
    res = catalog.validate_links(tickets, [
        {"source_column": "cust", "relation": "join_key", "target": "silver.customers.customer_id"},
        {"source_column": "priority", "relation": "new"},
        {"source_column": "ticket_id", "relation": "join_key", "target": "gold.sales.order_id"},
        {"source_column": "country", "relation": "maps_to", "target": "gold.nope.country"},
    ], feed="gold.customer_tickets")
    checks = {c["source_column"]: c for c in res["checks"]}
    assert checks["cust"]["status"] == "warn" and checks["cust"]["overlap"] == 0.9 and "C9999" in checks["cust"]["message"]
    assert checks["priority"]["status"] == "ok"
    assert checks["ticket_id"]["status"] == "fail"
    assert checks["country"]["status"] == "fail"
    assert res["status"] == "fail"
    nodes = {n["id"]: n["status"] for n in res["impact"]["nodes"]}
    assert nodes["source.tickets.json"] == nodes["bronze.tickets"] == nodes["gold.customer_tickets"] == "added"
    edges = {(e["source"], e["target"]): e["status"] for e in res["impact"]["edges"]}
    assert edges[("silver.customers", "gold.customer_tickets")] == "added"
    assert edges[("silver.tickets", "gold.customer_tickets")] == "added"
    assert res["impact"]["warnings"] == []
