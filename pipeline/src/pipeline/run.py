"""Run the full medallion pipeline: sources -> bronze -> silver -> gold, recording lineage."""

import json
import logging
from datetime import datetime, timezone

import pandas as pd

from . import bronze, contracts, dictionary, gold, onboarded, silver
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
    dictionary_rows: list[dict] = []
    contract_rows: list[dict] = []
    for source, filename in bronze.SOURCES.items():
        with rec.job(f"bronze_{source}", "bronze", f"Land raw {filename} as-is in the bronze bucket") as job:
            key, rows = bronze.ingest_one(lake, settings.sources_dir, source, run_ts)
            job.input(f"source.{filename}", rows)
            job.output(f"bronze.{source}", rows=rows)
            landed[source] = key
        raw_df = bronze.read_frame(filename, lake.get_bytes(settings.bronze_bucket, key))
        dictionary_rows += dictionary.profile(
            f"bronze.{source}", raw_df,
            {c: [(f"source.{filename}", c, "landed as-is")] for c in raw_df.columns}, rec.run_id)

    specs = onboarded.load(lake)
    errors: dict[str, str] = {}
    raw_onboarded: dict[str, pd.DataFrame] = {}
    for spec in specs:
        name, src = spec["name"], f"source.{onboarded.label(spec)}"
        try:
            with rec.job(f"bronze_{name}", "bronze", f"Land onboarded {onboarded.label(spec)} as delivered") as job:
                filename, data = onboarded.extract(lake, spec)
                key = f"{name}/ingested_at={run_ts:%Y%m%dT%H%M%SZ}/{filename}"
                lake.put_bytes(settings.bronze_bucket, key, data)
                raw_df = onboarded.raw_frame(filename, data)
                job.input(src, len(raw_df))
                job.output(f"bronze.{name}", rows=len(raw_df))
                landed[name] = key
        except Exception as exc:  # noqa: BLE001 - an onboarded source must not break the core pipeline
            log.exception("Onboarded source %s failed in bronze", name)
            errors[name] = str(exc)
            continue
        raw_onboarded[name] = raw_df
        dictionary_rows += dictionary.profile(f"bronze.{name}", raw_df, {c: [(src, c, "landed as-is")] for c in raw_df.columns},
                                              rec.run_id, onboarded.descriptions(spec, list(raw_df.columns)))

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
            stored = _with_metadata(df, rec.run_id, processed_at, source_uri)
            lake.write_parquet(settings.silver_bucket, name, stored)
            job.output(f"silver.{name}", df, columns=silver.COLUMN_LINEAGE[name])
            silver_tables[name] = df
        dictionary_rows += dictionary.profile(f"silver.{name}", stored, silver.COLUMN_LINEAGE[name], rec.run_id)

    onboarded_silver: dict[str, pd.DataFrame] = {}
    for spec in specs:
        name = spec["name"]
        if name not in raw_onboarded:
            continue
        try:
            with rec.job(f"silver_{name}", "silver", f"Type, trim and deduplicate onboarded {name}") as job:
                job.input(f"bronze.{name}", len(raw_onboarded[name]))
                df, dropped, lineage = onboarded.clean(raw_onboarded[name], spec)
                job.dropped(dropped)
                stored = _with_metadata(df, rec.run_id, processed_at, f"s3://{settings.bronze_bucket}/{landed[name]}")
                lake.write_parquet(settings.silver_bucket, name, stored)
                job.output(f"silver.{name}", df, columns=lineage)
        except Exception as exc:  # noqa: BLE001
            log.exception("Onboarded source %s failed in silver", name)
            errors[name] = str(exc)
            continue
        onboarded_silver[name] = df
        dictionary_rows += dictionary.profile(f"silver.{name}", stored, lineage, rec.run_id,
                                              onboarded.descriptions(spec, list(df.columns)))

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
    enrichments: dict[str, list[dict]] = {}
    for spec in specs:
        if spec["name"] in onboarded_silver and spec["feed"].split(".", 1)[1] in builders:
            enrichments.setdefault(spec["feed"], []).append(spec)
    gold_tables: dict[str, pd.DataFrame] = {}
    for name, build in builders.items():
        with rec.job(f"gold_{name}", "gold", (gold.__dict__[f"build_{name}"].__doc__ or name).strip()) as job:
            for src in gold.GOLD_INPUTS[name]:
                job.input(src, len(available[src]))
            df = build()
            columns = dict(gold.COLUMN_LINEAGE[name])
            notes: dict[str, str] = {}
            for spec in enrichments.get(f"gold.{name}", []):
                try:
                    df, extra = onboarded.enrich_feed(df, onboarded_silver[spec["name"]], spec)
                except onboarded.OnboardError as exc:
                    log.warning("Skipping enrichment of %s by %s: %s", name, spec["name"], exc)
                    errors[spec["name"]] = str(exc)
                    continue
                job.input(f"silver.{spec['name']}", len(onboarded_silver[spec["name"]]))
                columns.update(extra)
                notes.update(onboarded.descriptions(spec, list(extra)))
            stored = _with_metadata(df, rec.run_id, processed_at)
            lake.write_parquet(settings.gold_bucket, name, stored)
            job.output(f"gold.{name}", df, columns=columns)
            gold_tables[name] = df
            available[f"gold.{name}"] = df
        dictionary_rows += dictionary.profile(f"gold.{name}", stored, columns, rec.run_id, notes)
        contract_rows += contracts.evaluate(name, df, rec.run_id)

    for spec in specs:
        name, feed = spec["name"], spec["feed"]
        if name not in onboarded_silver or feed in enrichments:
            continue
        feed_name = feed.split(".", 1)[1]
        try:
            with rec.job(f"gold_{feed_name}", "gold", f"Onboarded {name} joined with its linked datasets") as job:
                df, columns, inputs = onboarded.build_new_feed(onboarded_silver[name], spec, available)
                for src in inputs:
                    job.input(src, len(available.get(src, onboarded_silver[name])))
                stored = _with_metadata(df, rec.run_id, processed_at)
                lake.write_parquet(settings.gold_bucket, feed_name, stored)
                job.output(feed, df, columns=columns)
        except Exception as exc:  # noqa: BLE001
            log.exception("Onboarded source %s failed in gold", name)
            errors[name] = str(exc)
            continue
        gold_tables[feed_name] = df
        available[feed] = df
        notes = onboarded.descriptions(spec, list(onboarded_silver[name].columns))
        notes.update({c: f"From {s[0][0]}.{s[0][1]} ({s[0][2]})." for c, s in columns.items() if not s[0][0].startswith("silver." + name)})
        dictionary_rows += dictionary.profile(feed, stored, columns, rec.run_id, notes)
        contract_rows += contracts.evaluate(feed_name, df, rec.run_id)

    log.info("LINEAGE: persisting OpenLineage events and lineage tables")
    lake.put_bytes(settings.lineage_bucket, f"events/run_id={rec.run_id}/events.jsonl",
                   "\n".join(json.dumps(e) for e in rec.events).encode())
    for table, df in rec.tables().items():
        lake.append_parquet(settings.lineage_bucket, table, df)
    lake.append_parquet(settings.lineage_bucket, "data_dictionary", dictionary.table(dictionary_rows))
    lake.append_parquet(settings.lineage_bucket, "contract_checks", contracts.table(contract_rows))

    summary = {
        "run_id": rec.run_id,
        "run_at": processed_at,
        "bronze": landed,
        "silver": {**{name: len(df) for name, df in silver_tables.items()},
                   **{name: len(df) for name, df in onboarded_silver.items()}},
        "gold": {name: len(df) for name, df in gold_tables.items()},
        "dropped": {j.name: j.quality for j in rec.jobs if j.quality},
        "enrichment": reviews["enrichment_method"].value_counts().to_dict(),
        "onboarded": {s["name"]: s["feed"] for s in specs if s["name"] in onboarded_silver and s["name"] not in errors},
        "onboarding_errors": errors,
        "contracts_failed": sorted({r["dataset"] for r in contract_rows if r["status"] == "fail"}),
    }
    lake.put_bytes(settings.gold_bucket, "_manifest.json", json.dumps(summary, indent=2).encode())
    return summary


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log.info("Pipeline finished:\n%s", json.dumps(run(), indent=2))


if __name__ == "__main__":
    main()
