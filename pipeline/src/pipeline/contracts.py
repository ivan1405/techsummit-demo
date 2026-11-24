"""Data contracts for gold datasets: schema, volume, completeness and uniqueness checked on every run.
Freshness (max_age_minutes) is evaluated at read time by the MCP server."""

import pandas as pd

CONTRACTS = {
    "sales": {"owner": "Sales Ops", "key": ["order_id"], "not_null": ["order_id", "order_date", "customer_id", "amount"],
              "rows": (100, 100_000), "max_age_minutes": 60,
              "types": {"order_id": "string", "amount": "number", "quantity": "number"}},
    "customer_360": {"owner": "CRM", "key": ["customer_id"], "not_null": ["customer_id", "email", "revenue"],
                     "rows": (10, 100_000), "max_age_minutes": 24 * 60,
                     "types": {"customer_id": "string", "revenue": "number", "orders": "number"}},
    "product_performance": {"owner": "Merchandising", "key": ["product_id"], "not_null": ["product_id", "revenue"],
                            "rows": (1, 10_000), "max_age_minutes": 24 * 60,
                            "types": {"revenue": "number", "return_rate": "number"}},
    "daily_revenue": {"owner": "Finance", "key": ["order_date", "country"], "not_null": ["order_date", "revenue"],
                      "rows": (30, 100_000), "max_age_minutes": 60, "types": {"revenue": "number"}},
    "review_insights": {"owner": "Customer Care", "key": ["review_id"], "not_null": ["review_id", "sentiment"],
                        "rows": (10, 100_000), "max_age_minutes": 24 * 60,
                        "types": {"sentiment": "string", "rating": "number"}},
}

COLUMNS = ["run_id", "dataset", "owner", "check", "expectation", "observed", "status", "max_age_minutes"]


def default_contract(df: pd.DataFrame) -> dict:
    """Onboarded datasets: the schema they were accepted with, and at least one row."""
    return {"owner": "Onboarded source", "key": [], "not_null": [], "rows": (1, 10_000_000),
            "max_age_minutes": 24 * 60, "columns": [c for c in df.columns if not c.startswith("_")]}


def _family(dtype) -> str:
    return "number" if pd.api.types.is_numeric_dtype(dtype) and not pd.api.types.is_bool_dtype(dtype) else "string"


def evaluate(name: str, df: pd.DataFrame, run_id: str, contract: dict | None = None) -> list[dict]:
    c = contract or CONTRACTS.get(name) or default_contract(df)
    dataset = f"gold.{name}"
    rows = []

    def add(check: str, expectation: str, observed, ok: bool) -> None:
        rows.append({"run_id": run_id, "dataset": dataset, "owner": c["owner"], "check": check,
                     "expectation": expectation, "observed": str(observed), "status": "pass" if ok else "fail",
                     "max_age_minutes": c["max_age_minutes"]})

    lo, hi = c["rows"]
    add("row_count", f"{lo}..{hi} rows", len(df), lo <= len(df) <= hi)
    expected = c.get("columns") or [*c["types"], *c["not_null"], *c["key"]]
    missing = sorted({col for col in expected if col not in df.columns})
    add("schema", f"columns present: {', '.join(dict.fromkeys(expected))}",
        f"missing: {', '.join(missing)}" if missing else "all present", not missing)
    for col, family in c.get("types", {}).items():
        if col in df.columns:
            add(f"type:{col}", family, _family(df[col].dtype), _family(df[col].dtype) == family)
    for col in c["not_null"]:
        if col in df.columns:
            nulls = int(df[col].isna().sum())
            add(f"not_null:{col}", "0 nulls", f"{nulls} nulls", nulls == 0)
    if c["key"] and all(k in df.columns for k in c["key"]):
        dupes = int(df.duplicated(subset=c["key"]).sum())
        add("unique:" + "+".join(c["key"]), "no duplicate keys", f"{dupes} duplicates", dupes == 0)
    return rows


def table(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=COLUMNS)
