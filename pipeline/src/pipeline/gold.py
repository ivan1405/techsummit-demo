"""Gold layer: merge and combine silver tables into business-ready datasets."""

import pandas as pd


def build_sales(orders: pd.DataFrame, customers: pd.DataFrame) -> pd.DataFrame:
    """Join orders with customers into an order-level sales fact table."""
    sales = orders.merge(
        customers[["customer_id", "full_name", "country", "segment"]], on="customer_id", how="left"
    )
    sales = sales.rename(columns={"full_name": "customer_name"})
    cols = ["order_id", "order_date", "customer_id", "customer_name", "country", "segment", "product_id",
            "product_name", "category", "quantity", "unit_price", "amount", "status"]
    return sales[cols].sort_values("order_date").reset_index(drop=True)


def build_customer_360(sales: pd.DataFrame, customers: pd.DataFrame, reviews: pd.DataFrame) -> pd.DataFrame:
    """Combine sales and reviews into a per-customer 360 view."""
    completed = sales[~sales["status"].isin(["cancelled", "returned"])]
    agg = completed.groupby("customer_id").agg(
        orders=("order_id", "nunique"),
        revenue=("amount", "sum"),
        last_order_date=("order_date", "max"),
    )
    rev = reviews.merge(sales[["order_id", "customer_id"]], on="order_id", how="inner")
    rev_agg = rev.groupby("customer_id").agg(
        reviews=("review_id", "count"),
        avg_rating=("rating", "mean"),
        negative_reviews=("sentiment", lambda s: int((s == "negative").sum())),
    )
    out = customers[["customer_id", "full_name", "email", "country", "segment", "signup_date"]].set_index(
        "customer_id"
    )
    out = out.join(agg).join(rev_agg).reset_index()
    out[["orders", "reviews", "negative_reviews"]] = out[["orders", "reviews", "negative_reviews"]].fillna(0).astype(int)
    out["revenue"] = out["revenue"].fillna(0).round(2)
    out["avg_rating"] = out["avg_rating"].round(2)
    return out.sort_values("revenue", ascending=False).reset_index(drop=True)


def build_product_performance(sales: pd.DataFrame, reviews: pd.DataFrame) -> pd.DataFrame:
    """Combine sales and reviews into per-product KPIs."""
    completed = sales[~sales["status"].isin(["cancelled", "returned"])]
    agg = completed.groupby(["product_id", "product_name", "category"]).agg(
        units_sold=("quantity", "sum"), revenue=("amount", "sum"), orders=("order_id", "nunique")
    )
    returns = sales.groupby("product_id")["status"].apply(lambda s: round((s == "returned").mean(), 3))
    rev = reviews.groupby("product_id").agg(
        reviews=("review_id", "count"),
        avg_rating=("rating", "mean"),
        positive_share=("sentiment", lambda s: round((s == "positive").mean(), 3)),
        negative_share=("sentiment", lambda s: round((s == "negative").mean(), 3)),
        top_complaint=("topic", lambda t: _top_complaint(reviews.loc[t.index])),
    )
    out = agg.reset_index().set_index("product_id").join(returns.rename("return_rate")).join(rev).reset_index()
    out["revenue"] = out["revenue"].round(2)
    out["avg_rating"] = out["avg_rating"].round(2)
    return out.sort_values("revenue", ascending=False).reset_index(drop=True)


def _top_complaint(product_reviews: pd.DataFrame) -> str | None:
    neg = product_reviews[product_reviews["sentiment"] == "negative"]["topic"]
    return None if neg.empty else neg.value_counts().idxmax()


def build_daily_revenue(sales: pd.DataFrame) -> pd.DataFrame:
    """Aggregate completed sales revenue per day and country."""
    completed = sales[~sales["status"].isin(["cancelled", "returned"])]
    out = completed.groupby(["order_date", "country"]).agg(
        orders=("order_id", "nunique"), units=("quantity", "sum"), revenue=("amount", "sum")
    )
    out["revenue"] = out["revenue"].round(2)
    return out.reset_index().sort_values(["order_date", "country"]).reset_index(drop=True)


def build_review_insights(reviews: pd.DataFrame, sales: pd.DataFrame) -> pd.DataFrame:
    """Merge AI-enriched reviews with sales context."""
    out = reviews.merge(
        sales[["order_id", "customer_id", "customer_name", "country", "product_name", "category"]],
        on="order_id", how="left",
    )
    cols = ["review_id", "order_id", "customer_id", "customer_name", "country", "product_id", "product_name",
            "category", "rating", "sentiment", "topic", "review_text", "enrichment_method"]
    return out[cols]


_COMPLETED = "excluding cancelled/returned orders"


def _same(dataset: str, cols: list[str], how: str = "direct copy") -> dict:
    return {c: [(dataset, c, how)] for c in cols}


COLUMN_LINEAGE = {
    "sales": {
        **_same("silver.orders", ["order_id", "order_date", "customer_id", "product_id", "product_name", "category",
                                  "quantity", "unit_price", "amount", "status"]),
        "customer_name": [("silver.customers", "full_name", "LEFT JOIN customers ON customer_id")],
        "country": [("silver.customers", "country", "LEFT JOIN customers ON customer_id")],
        "segment": [("silver.customers", "segment", "LEFT JOIN customers ON customer_id")],
    },
    "customer_360": {
        **_same("silver.customers", ["customer_id", "full_name", "email", "country", "segment", "signup_date"]),
        "orders": [("gold.sales", "order_id", f"COUNT DISTINCT per customer {_COMPLETED}"),
                   ("gold.sales", "status", "filter")],
        "revenue": [("gold.sales", "amount", f"SUM per customer {_COMPLETED}"), ("gold.sales", "status", "filter")],
        "last_order_date": [("gold.sales", "order_date", f"MAX per customer {_COMPLETED}")],
        "reviews": [("silver.reviews", "review_id", "COUNT per customer (via order_id)")],
        "avg_rating": [("silver.reviews", "rating", "AVG per customer (via order_id)")],
        "negative_reviews": [("silver.reviews", "sentiment", "COUNT where sentiment = negative")],
    },
    "product_performance": {
        **_same("gold.sales", ["product_id", "product_name", "category"], "GROUP BY key"),
        "units_sold": [("gold.sales", "quantity", f"SUM {_COMPLETED}")],
        "revenue": [("gold.sales", "amount", f"SUM {_COMPLETED}")],
        "orders": [("gold.sales", "order_id", f"COUNT DISTINCT {_COMPLETED}")],
        "return_rate": [("gold.sales", "status", "share of orders with status = returned")],
        "reviews": [("silver.reviews", "review_id", "COUNT per product")],
        "avg_rating": [("silver.reviews", "rating", "AVG per product")],
        "positive_share": [("silver.reviews", "sentiment", "share with sentiment = positive")],
        "negative_share": [("silver.reviews", "sentiment", "share with sentiment = negative")],
        "top_complaint": [("silver.reviews", "topic", "most frequent topic among negative reviews"),
                          ("silver.reviews", "sentiment", "filter negative")],
    },
    "daily_revenue": {
        **_same("gold.sales", ["order_date", "country"], "GROUP BY key"),
        "orders": [("gold.sales", "order_id", f"COUNT DISTINCT {_COMPLETED}")],
        "units": [("gold.sales", "quantity", f"SUM {_COMPLETED}")],
        "revenue": [("gold.sales", "amount", f"SUM {_COMPLETED}")],
    },
    "review_insights": {
        **_same("silver.reviews", ["review_id", "order_id", "product_id", "rating", "sentiment", "topic",
                                   "review_text", "enrichment_method"]),
        **_same("gold.sales", ["customer_id", "customer_name", "country", "product_name", "category"],
                "LEFT JOIN sales ON order_id"),
    },
}

GOLD_INPUTS = {
    "sales": ["silver.orders", "silver.customers"],
    "customer_360": ["gold.sales", "silver.customers", "silver.reviews"],
    "product_performance": ["gold.sales", "silver.reviews"],
    "daily_revenue": ["gold.sales"],
    "review_insights": ["silver.reviews", "gold.sales"],
}
