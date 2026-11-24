"""Data dictionary: business description, type, profile and upstream lineage for every column.

Descriptions live here, next to the code that produces the columns; the pipeline profiles each
table it writes and stores the result as the `data_dictionary` lineage table.
"""

import pandas as pd

from .lineage import ColumnLineage

METADATA = {
    "_source_uri": "Bronze object the row was read from (s3://bronze/...).",
    "_run_id": "Pipeline run that produced the row; joins to lineage_runs.run_id.",
    "_processed_at": "UTC timestamp at which the pipeline run processed the row.",
}

_ORDER = {
    "order_id": "Order identifier from the e-commerce platform.",
    "order_date": "Date the order was placed.",
    "customer_id": "Customer who placed the order; joins to customers.customer_id.",
    "product_id": "Product SKU.",
    "product_name": "Product display name.",
    "category": "Product category.",
    "quantity": "Units ordered.",
    "unit_price": "Price per unit in EUR.",
    "status": "Order status: delivered, shipped, cancelled or returned.",
}
_CUSTOMER = {
    "customer_id": "Customer identifier from the CRM.",
    "full_name": "Customer full name.",
    "email": "Customer contact email (personal data).",
    "country": "Customer country.",
    "segment": "Commercial segment: consumer, smb or enterprise.",
    "signup_date": "Date the customer registered.",
}
_REVIEW = {
    "review_id": "Review identifier.",
    "order_id": "Order the review refers to; joins to orders.order_id.",
    "product_id": "Reviewed product SKU.",
    "rating": "Star rating given by the customer.",
    "review_text": "Free-text review written by the customer.",
}
_RAW = " Raw text, as delivered by the source."

DESCRIPTIONS: dict[str, dict[str, str]] = {
    "bronze.orders": {c: d + _RAW for c, d in _ORDER.items()},
    "bronze.customers": {c: d + _RAW for c, d in _CUSTOMER.items()},
    "bronze.reviews": {c: d + _RAW for c, d in _REVIEW.items()},
    "silver.orders": {
        **_ORDER,
        "order_date": "Date the order was placed (parsed from mixed formats).",
        "unit_price": "Price per unit in EUR (currency symbols and comma decimals normalised).",
        "amount": "Order line amount in EUR: quantity × unit_price.",
    },
    "silver.customers": {
        **_CUSTOMER,
        "country": "Customer country as ISO-3166 alpha-2 code.",
        "email": "Customer contact email, lowercased (personal data).",
    },
    "silver.reviews": {
        **_REVIEW,
        "rating": "Star rating 1-5.",
        "sentiment": "AI-classified sentiment: positive, neutral or negative.",
        "topic": "AI-classified main topic (delivery, quality, price, support, ...).",
        "enrichment_method": "Classifier that produced sentiment/topic: llm or heuristic.",
    },
    "gold.sales": {
        **_ORDER,
        "customer_name": "Customer full name at order time.",
        "country": "Customer country (ISO-3166 alpha-2).",
        "segment": "Customer commercial segment.",
        "amount": "Order line amount in EUR.",
    },
    "gold.customer_360": {
        **_CUSTOMER,
        "country": "Customer country (ISO-3166 alpha-2).",
        "orders": "Completed orders (excludes cancelled and returned).",
        "revenue": "Completed revenue in EUR (excludes cancelled and returned).",
        "last_order_date": "Date of the most recent completed order.",
        "reviews": "Reviews written by the customer.",
        "avg_rating": "Average star rating given by the customer.",
        "negative_reviews": "Reviews classified as negative.",
    },
    "gold.product_performance": {
        "product_id": "Product SKU.",
        "product_name": "Product display name.",
        "category": "Product category.",
        "units_sold": "Units sold in completed orders.",
        "revenue": "Completed revenue in EUR.",
        "orders": "Completed orders containing the product.",
        "return_rate": "Share of the product's orders that were returned (0-1).",
        "reviews": "Reviews of the product.",
        "avg_rating": "Average star rating of the product.",
        "positive_share": "Share of reviews classified as positive (0-1).",
        "negative_share": "Share of reviews classified as negative (0-1).",
        "top_complaint": "Most frequent topic among negative reviews.",
    },
    "gold.daily_revenue": {
        "order_date": "Calendar day.",
        "country": "Customer country (ISO-3166 alpha-2).",
        "orders": "Completed orders that day.",
        "units": "Units sold in completed orders that day.",
        "revenue": "Completed revenue in EUR that day.",
    },
    "gold.review_insights": {
        **_REVIEW,
        "rating": "Star rating 1-5.",
        "customer_id": "Customer who wrote the review (via the order).",
        "customer_name": "Customer full name.",
        "country": "Customer country (ISO-3166 alpha-2).",
        "product_name": "Reviewed product name.",
        "category": "Reviewed product category.",
        "sentiment": "AI-classified sentiment: positive, neutral or negative.",
        "topic": "AI-classified main topic.",
        "enrichment_method": "Classifier that produced sentiment/topic: llm or heuristic.",
    },
}

COLUMNS = ["run_id", "dataset", "layer", "column_name", "position", "data_type", "description", "null_pct",
           "distinct_count", "sample_values", "upstream", "transformation"]


def describe(dataset: str, column: str) -> str:
    return DESCRIPTIONS.get(dataset, {}).get(column) or METADATA.get(column, "")


def profile(dataset: str, df: pd.DataFrame, lineage: ColumnLineage, run_id: str,
            descriptions: dict[str, str] | None = None) -> list[dict]:
    """One dictionary row per column of a table, with its profile and direct upstream columns."""
    rows = []
    for pos, (col, dtype) in enumerate(df.dtypes.items()):
        values = df[col]
        samples = values.dropna().astype(str).drop_duplicates().head(3).tolist()
        sources = lineage.get(col, [])
        rows.append({
            "run_id": run_id,
            "dataset": dataset,
            "layer": dataset.split(".", 1)[0],
            "column_name": col,
            "position": pos,
            "data_type": "string" if str(dtype) in ("object", "str", "string") else str(dtype),
            "description": (descriptions or {}).get(col) or describe(dataset, col),
            "null_pct": round(float(values.isna().mean()) * 100, 1) if len(df) else 0.0,
            "distinct_count": int(values.nunique(dropna=True)),
            "sample_values": ", ".join(s[:40] for s in samples),
            "upstream": ", ".join(f"{d}.{c}" for d, c, _ in sources),
            "transformation": "; ".join(dict.fromkeys(how for _, _, how in sources)),
        })
    return rows


def table(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=COLUMNS)
