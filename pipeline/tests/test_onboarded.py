import sqlite3
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from pipeline import onboarded
from pipeline.config import Settings
from pipeline.lake import Lake
from pipeline.run import run

ROOT = Path(__file__).resolve().parents[2] / "data"


@pytest.fixture
def lake():
    with mock_aws():
        settings = Settings(sources_dir=str(ROOT / "sources"), llm_provider="none", openlineage_url="")
        lk = Lake(settings, client=boto3.client("s3", region_name="us-east-1"))
        lk.ensure_buckets()
        yield lk


LOYALTY = {"name": "loyalty", "kind": "file", "filename": "loyalty.json", "feed": "gold.customer_360",
           "links": [{"source_column": "member_customer_id", "relation": "join_key", "target": "silver.customers.customer_id"},
                     {"source_column": "member_tier", "relation": "new"}, {"source_column": "points", "relation": "new"}]}
RETURNS = {"name": "returns", "kind": "file", "filename": "returns.csv", "feed": "gold.returns",
           "links": [{"source_column": "order_id", "relation": "join_key", "target": "silver.orders.order_id"},
                     {"source_column": "reason", "relation": "new"}]}


def test_onboarded_sources_flow_through_every_layer_with_lineage(lake):
    run(lake.settings, lake)
    onboarded.register(lake, LOYALTY, (ROOT / "samples" / "loyalty.json").read_text())
    onboarded.register(lake, RETURNS, (ROOT / "samples" / "returns.csv").read_text())
    summary = run(lake.settings, lake)

    assert summary["onboarding_errors"] == {}
    assert summary["onboarded"] == {"loyalty": "gold.customer_360", "returns": "gold.returns"}
    c360 = lake.read_parquet("gold", "customer_360")
    assert len(c360) == 60 and {"member_tier", "points"} <= set(c360.columns)
    assert c360["points"].notna().sum() == 40
    returns = lake.read_parquet("gold", "returns")
    assert {"return_id", "reason", "product_name", "customer_id"} <= set(returns.columns) and len(returns) == 40
    assert "orders_product_id" in returns.columns
    cols = lake.read_parquet("lineage", "lineage_columns")
    cols = cols[cols.run_id == summary["run_id"]]
    pts = cols[(cols.target_dataset == "gold.customer_360") & (cols.target_column == "points")].iloc[0]
    assert (pts.source_dataset, pts.source_column) == ("silver.loyalty", "points")
    assert set(cols[cols.target_dataset == "bronze.loyalty"].source_dataset) <= {"source.loyalty.json"}
    edges = lake.read_parquet("lineage", "lineage_edges")
    edges = set(map(tuple, edges[edges.run_id == summary["run_id"]][["source_dataset", "target_dataset"]].values))
    assert {("source.loyalty.json", "bronze.loyalty"), ("silver.loyalty", "gold.customer_360"),
            ("silver.orders", "gold.returns"), ("silver.returns", "gold.returns")} <= edges
    dd = lake.read_parquet("lineage", "data_dictionary")
    dd = dd[dd.run_id == summary["run_id"]]
    tier = dd[(dd.dataset == "silver.loyalty") & (dd.column_name == "member_tier")].iloc[0]
    assert tier.description.startswith("From onboarded source loyalty.json")

    onboarded.unregister(lake, "loyalty")
    after = run(lake.settings, lake)
    assert "member_tier" not in lake.read_parquet("gold", "customer_360").columns
    assert after["onboarded"] == {"returns": "gold.returns"}


def test_preview_rejects_non_unique_key_into_existing_dataset(lake, tmp_path):
    run(lake.settings, lake)
    db = tmp_path / "erp.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE tickets (ticket TEXT, cust TEXT)")
    con.executemany("INSERT INTO tickets VALUES (?, ?)", [("T1", "C0001"), ("T2", "C0001"), ("T3", "C0002")])
    con.commit()
    con.close()
    spec = {"name": "tickets", "kind": "database", "url": f"sqlite:///{db}", "table": "tickets",
            "feed": "gold.customer_360",
            "links": [{"source_column": "cust", "relation": "join_key", "target": "silver.customers.customer_id"}]}
    with pytest.raises(onboarded.OnboardError, match="not unique"):
        onboarded.preview(lake, spec)
    assert onboarded.preview(lake, {**spec, "feed": "gold.tickets"})["rows"] == 3
    with pytest.raises(onboarded.OnboardError):
        onboarded.check_spec({**spec, "feed": "silver.x"})


def test_clean_keeps_rows_with_at_least_one_join_key():
    import pandas as pd
    raw = pd.DataFrame({"a": ["1", None, None, "1"], "b": ["x", "y", None, "x"], "n": ["1", "2", "3", "1"]}).astype("string")
    spec = {"name": "t", "links": [{"source_column": "a", "relation": "join_key", "target": "silver.c.a"},
                                   {"source_column": "b", "relation": "join_key", "target": "silver.d.b"}]}
    df, dropped, _ = onboarded.clean(raw, spec)
    assert len(df) == 2 and dropped == {"duplicate_row": 1, "null_join_keys": 1}
    assert str(df["n"].dtype).lower().startswith("int")


def test_public_masks_database_password():
    assert onboarded.public({"url": "postgresql://reader:secret@db:5432/erp"})["url"] == "postgresql://reader:***@db:5432/erp"


def test_documented_descriptions_reach_the_dictionary():
    spec = {"name": "t", "kind": "json", "links": [
        {"source_column": "a", "relation": "new", "description": "Agent-set urgency."},
        {"source_column": "b", "relation": "maps_to", "target": "silver.customers.country"}]}
    assert onboarded.descriptions(spec, ["a", "b"]) == {
        "a": "Agent-set urgency. From onboarded source t.json: new attribute.",
        "b": "From onboarded source t.json: same meaning as silver.customers.country."}
    assert onboarded.check_spec({**spec, "plan_id": "abc"})["plan_id"] == "abc"


def test_onboarded_source_can_join_another_onboarded_silver_dataset(lake):
    run(lake.settings, lake)
    onboarded.register(lake, RETURNS, (ROOT / "samples" / "returns.csv").read_text())
    summary = run(lake.settings, lake)
    first = lake.read_parquet("silver", "returns")["return_id"].iloc[0]
    notes = {"name": "return_notes", "kind": "file", "filename": "return_notes.csv", "feed": "gold.return_notes",
             "links": [{"source_column": "return_id", "relation": "join_key", "target": "silver.returns.return_id"}]}
    onboarded.register(lake, notes, f"return_id,note\n{first},damaged box\n")
    summary = run(lake.settings, lake)
    assert summary["onboarding_errors"] == {}
    out = lake.read_parquet("gold", "return_notes")
    assert len(out) == 1 and {"note", "reason"} <= set(out.columns)
