"""Run the full medallion pipeline: sources -> bronze -> silver -> gold, recording lineage."""

import json
import logging
from datetime import datetime, timezone

import pandas as pd

from . import bronze, gold, silver
from .config import Settings
from .lake import Lake
from .lineage import LineageRecorder

log = logging.getLogger("pipeline")


def _with_metadata(df: pd.DataFrame, run_id: str, processed_at: str, source_uri: str | None = None) -> pd.DataFrame:
    df = df.copy()
    if source_uri:
        df["_source_uri"] = source_uri
    df["_run_id"] = run_id
    df["_processed_at"] = processed_at
    return df


def run(settings: Settings | None = None, lake: Lake | None = None) -> dict:
    settings = settings or Settings()
    lake = lake or Lake(settings)
    lake.ensure_buckets()
    rec = LineageRecorder(emit_url=settings.openlineage_url)
    run_ts = datetime.now(timezone.utc)
    processed_at = run_ts.isoformat()
    log.info("Run %s started", rec.run_id)

    log.info("BRONZE: landing raw sources")
    landed: dict[str, str] = {}
    for source, filename in bronze.SOURCES.items():
        with rec.job(f"bronze_{source}", "bronze", f"Land raw {filename} as-is in the bronze bucket") as job:
            key, rows = bronze.ingest_one(lake, settings.sources_dir, source, run_ts)
            job.input(f"source.{filename}", rows)
            job.output(f"bronze.{source}", rows=rows)
            landed[source] = key

    log.info("SILVER: filter, clean and AI enrichment (provider=%s)", settings.llm_provider)
    cleaners = {
        "orders": (silver.clean_orders, "Deduplicate, parse dates/prices, drop invalid orders"),
        "customers": (silver.clean_customers, "Deduplicate and normalise names, emails, countries"),
        "reviews": (lambda raw: silver.clean_reviews(raw, settings.llm_provider, settings.llm_model),
                    "Clean reviews and AI-classify sentiment/topic"),
    }
    silver_tables: dict[str, pd.DataFrame] = {}
    for name, (clean, description) in cleaners.items():
        with rec.job(f"silver_{name}", "silver", description) as job:
            raw = lake.get_bytes(settings.bronze_bucket, landed[name])
            job.input(f"bronze.{name}", bronze.count_records(bronze.SOURCES[name], raw))
            df, dropped = clean(raw)
            job.dropped(dropped)
            source_uri = f"s3://{settings.bronze_bucket}/{landed[name]}"
            lake.write_parquet(settings.silver_bucket, name, _with_metadata(df, rec.run_id, processed_at, source_uri))
            job.output(f"silver.{name}", df, columns=silver.COLUMN_LINEAGE[name])
            silver_tables[name] = df

    log.info("GOLD: merge and combine into business datasets")
    orders, customers, reviews = (silver_tables[k] for k in ("orders", "customers", "reviews"))
    builders = {
        "sales": lambda: gold.build_sales(orders, customers),
        "customer_360": lambda: gold.build_customer_360(gold_tables["sales"], customers, reviews),
        "product_performance": lambda: gold.build_product_performance(gold_tables["sales"], reviews),
        "daily_revenue": lambda: gold.build_daily_revenue(gold_tables["sales"]),
        "review_insights": lambda: gold.build_review_insights(reviews, gold_tables["sales"]),
    }
    available = {f"silver.{k}": v for k, v in silver_tables.items()}
    gold_tables: dict[str, pd.DataFrame] = {}
    for name, build in builders.items():
        with rec.job(f"gold_{name}", "gold", (gold.__dict__[f"build_{name}"].__doc__ or name).strip()) as job:
            for src in gold.GOLD_INPUTS[name]:
                job.input(src, len(available[src]))
            df = build()
            lake.write_parquet(settings.gold_bucket, name, _with_metadata(df, rec.run_id, processed_at))
            job.output(f"gold.{name}", df, columns=gold.COLUMN_LINEAGE[name])
            gold_tables[name] = df
            available[f"gold.{name}"] = df

    log.info("LINEAGE: persisting OpenLineage events and lineage tables")
    lake.put_bytes(settings.lineage_bucket, f"events/run_id={rec.run_id}/events.jsonl",
                   "\n".join(json.dumps(e) for e in rec.events).encode())
    for table, df in rec.tables().items():
        lake.append_parquet(settings.lineage_bucket, table, df)

    summary = {
        "run_id": rec.run_id,
        "run_at": processed_at,
        "bronze": landed,
        "silver": {name: len(df) for name, df in silver_tables.items()},
        "gold": {name: len(df) for name, df in gold_tables.items()},
        "dropped": {j.name: j.quality for j in rec.jobs if j.quality},
        "enrichment": reviews["enrichment_method"].value_counts().to_dict(),
    }
    lake.put_bytes(settings.gold_bucket, "_manifest.json", json.dumps(summary, indent=2).encode())
    return summary


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log.info("Pipeline finished:\n%s", json.dumps(run(), indent=2))


if __name__ == "__main__":
    main()
