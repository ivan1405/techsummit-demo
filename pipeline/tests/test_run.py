from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from pipeline.config import Settings
from pipeline.lake import Lake
from pipeline.run import run

SOURCES = Path(__file__).resolve().parents[2] / "data" / "sources"


@pytest.fixture
def lake():
    with mock_aws():
        settings = Settings(sources_dir=str(SOURCES), llm_provider="none", openlineage_url="")
        yield Lake(settings, client=boto3.client("s3", region_name="us-east-1"))


def test_full_pipeline_writes_all_layers_and_lineage(lake):
    summary = run(lake.settings, lake)

    assert set(summary["gold"]) == {"sales", "customer_360", "product_performance", "daily_revenue",
                                    "review_insights"}
    sales = lake.read_parquet("gold", "sales")
    assert len(sales) == summary["gold"]["sales"] > 0
    assert (sales["_run_id"] == summary["run_id"]).all()
    assert lake.read_parquet("silver", "orders")["_source_uri"].str.startswith("s3://bronze/orders/").all()

    jobs = lake.read_parquet("lineage", "lineage_jobs")
    assert len(jobs) == 11 and set(jobs["status"]) == {"COMPLETE"}
    cols = lake.read_parquet("lineage", "lineage_columns")
    revenue = cols[(cols.target_dataset == "gold.customer_360") & (cols.target_column == "revenue")]
    assert set(revenue["source_dataset"]) == {"gold.sales"}
    amount = cols[(cols.target_dataset == "silver.orders") & (cols.target_column == "amount")]
    assert set(amount["source_column"]) == {"quantity", "unit_price"}
    dq = lake.read_parquet("lineage", "data_quality")
    assert dq["rows_affected"].sum() > 0

    run(lake.settings, lake)
    assert len(lake.read_parquet("lineage", "lineage_runs")) == 2


def test_pipeline_writes_data_dictionary_for_every_layer(lake):
    summary = run(lake.settings, lake)

    dd = lake.read_parquet("lineage", "data_dictionary")
    assert set(dd["run_id"]) == {summary["run_id"]}
    assert {"bronze.orders", "silver.reviews", "gold.customer_360"} <= set(dd["dataset"])
    revenue = dd[(dd.dataset == "gold.customer_360") & (dd.column_name == "revenue")].iloc[0]
    assert revenue["description"].startswith("Completed revenue")
    assert revenue["upstream"] == "gold.sales.amount, gold.sales.status"
    raw = dd[(dd.dataset == "bronze.orders") & (dd.column_name == "unit_price")].iloc[0]
    assert raw["upstream"] == "source.orders.csv.unit_price" and raw["sample_values"]
    business = dd[~dd.column_name.str.startswith("_")]
    assert (business["description"] != "").all(), business[business["description"] == ""][["dataset", "column_name"]]
