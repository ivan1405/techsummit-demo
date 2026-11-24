"""Silver layer: filter, clean, deduplicate and AI-enrich the raw data.

Each clean_* function returns the cleaned frame and the number of rows dropped per rule.
"""

import io
import json
import re

import pandas as pd

from . import enrich

COUNTRY_MAP = {
    "es": "ES", "spain": "ES", "españa": "ES",
    "pt": "PT", "portugal": "PT",
    "fr": "FR", "france": "FR",
    "gb": "GB", "uk": "GB", "united kingdom": "GB",
    "ar": "AR", "argentina": "AR",
}

DATE_FORMATS = "parse YYYY-MM-DD | DD/MM/YYYY | YYYY/MM/DD"

COLUMN_LINEAGE = {
    "orders": {
        "order_id": [("bronze.orders", "order_id", "deduplicated (first occurrence kept)")],
        "order_date": [("bronze.orders", "order_date", f"{DATE_FORMATS}; invalid dates dropped")],
        "customer_id": [("bronze.orders", "customer_id", "trimmed; empty values dropped")],
        "product_id": [("bronze.orders", "product_id", "direct copy")],
        "product_name": [("bronze.orders", "product_name", "direct copy")],
        "category": [("bronze.orders", "category", "direct copy")],
        "quantity": [("bronze.orders", "quantity", "cast to int; rows with quantity <= 0 dropped")],
        "unit_price": [("bronze.orders", "unit_price", "strip currency, comma decimal -> float; invalid dropped")],
        "status": [("bronze.orders", "status", "trimmed + lowercased")],
        "amount": [("bronze.orders", "quantity", "quantity * unit_price"),
                   ("bronze.orders", "unit_price", "quantity * unit_price")],
    },
    "customers": {
        "customer_id": [("bronze.customers", "customer_id", "null ids dropped; deduplicated")],
        "full_name": [("bronze.customers", "full_name", "trimmed + title-cased")],
        "email": [("bronze.customers", "email", "trimmed + lowercased")],
        "country": [("bronze.customers", "country", "normalised to ISO-3166 alpha-2")],
        "segment": [("bronze.customers", "segment", "trimmed + lowercased")],
        "signup_date": [("bronze.customers", "signup_date", DATE_FORMATS)],
    },
    "reviews": {
        "review_id": [("bronze.reviews", "review_id", "deduplicated")],
        "order_id": [("bronze.reviews", "order_id", "direct copy")],
        "product_id": [("bronze.reviews", "product_id", "direct copy")],
        "rating": [("bronze.reviews", "rating", "parse 'N' or 'N/5' -> int 1..5; invalid dropped")],
        "review_text": [("bronze.reviews", "review_text", "trimmed; empty dropped")],
        "sentiment": [("bronze.reviews", "review_text", "AI classification (LLM, heuristic fallback)")],
        "topic": [("bronze.reviews", "review_text", "AI classification (LLM, heuristic fallback)")],
        "enrichment_method": [("bronze.reviews", "review_text", "which classifier produced sentiment/topic")],
    },
}


def parse_date(value) -> pd.Timestamp:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return pd.NaT
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
        ts = pd.to_datetime(str(value).strip(), format=fmt, errors="coerce")
        if not pd.isna(ts):
            return ts
    return pd.NaT


def parse_price(value) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    cleaned = re.sub(r"[^0-9,.\-]", "", str(value)).replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_rating(value) -> int | None:
    match = re.match(r"^\s*(\d+)", str(value or ""))
    if not match:
        return None
    rating = int(match.group(1))
    return rating if 1 <= rating <= 5 else None


def _drop(df: pd.DataFrame, mask: pd.Series, rule: str, dropped: dict[str, int]) -> pd.DataFrame:
    dropped[rule] = int(mask.sum())
    return df[~mask]


def clean_orders(raw: bytes) -> tuple[pd.DataFrame, dict[str, int]]:
    dropped: dict[str, int] = {}
    df = pd.read_csv(io.BytesIO(raw), dtype=str)
    df = _drop(df, df.duplicated(subset="order_id", keep="first"), "duplicate_order_id", dropped).copy()
    df["order_date"] = df["order_date"].map(parse_date)
    df["unit_price"] = df["unit_price"].map(parse_price)
    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce")
    df["status"] = df["status"].str.strip().str.lower()
    df["customer_id"] = df["customer_id"].fillna("").str.strip()
    df = _drop(df, df["customer_id"] == "", "missing_customer_id", dropped)
    df = _drop(df, df["order_date"].isna(), "invalid_order_date", dropped)
    df = _drop(df, df["unit_price"].isna(), "invalid_unit_price", dropped)
    df = _drop(df, df["quantity"].isna() | (df["quantity"] <= 0), "non_positive_quantity", dropped).copy()
    df["quantity"] = df["quantity"].astype(int)
    df["amount"] = (df["quantity"] * df["unit_price"]).round(2)
    df["order_date"] = df["order_date"].dt.date
    return df.reset_index(drop=True), dropped


def clean_customers(raw: bytes) -> tuple[pd.DataFrame, dict[str, int]]:
    dropped: dict[str, int] = {}
    df = pd.DataFrame(json.loads(raw))
    df = _drop(df, df["customer_id"].isna(), "missing_customer_id", dropped)
    df = _drop(df, df.duplicated(subset="customer_id", keep="first"), "duplicate_customer_id", dropped).copy()
    df["full_name"] = df["full_name"].str.strip().str.title()
    df["email"] = df["email"].str.strip().str.lower()
    df["country"] = df["country"].str.strip().str.lower().map(COUNTRY_MAP)
    df["segment"] = df["segment"].str.strip().str.lower()
    df["signup_date"] = df["signup_date"].map(parse_date).dt.date
    return df.reset_index(drop=True), dropped


def clean_reviews(raw: bytes, llm_provider: str = "none", llm_model: str = "") -> tuple[pd.DataFrame, dict[str, int]]:
    dropped: dict[str, int] = {}
    df = pd.read_json(io.BytesIO(raw), lines=True, dtype={"rating": str})
    df["review_text"] = df["review_text"].fillna("").str.strip()
    df["rating"] = df["rating"].map(parse_rating)
    df = _drop(df, df["review_text"] == "", "empty_review_text", dropped)
    df = _drop(df, df["rating"].isna(), "invalid_rating", dropped)
    df = _drop(df, df.duplicated(subset="review_id"), "duplicate_review_id", dropped).copy()
    df["rating"] = df["rating"].astype(int)
    enriched = enrich.classify_reviews(df["review_text"].tolist(), llm_provider, llm_model)
    df["sentiment"] = [e["sentiment"] for e in enriched]
    df["topic"] = [e["topic"] for e in enriched]
    df["enrichment_method"] = [e["method"] for e in enriched]
    return df.reset_index(drop=True), dropped
