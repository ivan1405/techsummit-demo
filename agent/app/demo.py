"""Keyword-routed answers used when no LLM is configured (LLM_PROVIDER=none).

Still goes through the MCP server, so the whole stack can be demoed without an API key.
"""

import re

from .mcp_tools import call

COLUMN_REF = re.compile(r"\b((?:bronze|silver|gold)\.[a-z0-9_]+)\.([a-z0-9_]+)\b", re.I)

HELP = """I'm running in **demo mode** (no LLM configured), so I understand a fixed set of questions:

- *Revenue by country*
- *Top products*
- *Top customers*
- *What are customers complaining about?*
- *Where does customer revenue come from?* (column lineage)
- *How is gold.sales.amount calculated?* (any `layer.dataset.column`, or use **?** in the dictionary)
- *Show the pipeline lineage*
- *What data quality issues were found?*
- *What changed since the last run?*
- *What tables are available?*

Set `LLM_PROVIDER` and an API key in `.env` to ask anything."""

QUERIES = {
    "country": ("Revenue by country (completed orders)",
                "SELECT country, SUM(orders) AS orders, ROUND(SUM(revenue), 2) AS revenue "
                "FROM daily_revenue GROUP BY country ORDER BY revenue DESC", "daily_revenue"),
    "product": ("Top products by revenue",
                "SELECT product_name, category, units_sold, revenue, avg_rating, return_rate, top_complaint "
                "FROM product_performance ORDER BY revenue DESC LIMIT 8", "product_performance"),
    "customer": ("Top 10 customers by revenue",
                 "SELECT full_name, country, segment, orders, revenue, avg_rating "
                 "FROM customer_360 ORDER BY revenue DESC LIMIT 10", "customer_360"),
    "complain": ("Negative reviews by topic",
                 "SELECT topic, COUNT(*) AS negative_reviews, ROUND(AVG(rating), 2) AS avg_rating "
                 "FROM review_insights WHERE sentiment = 'negative' GROUP BY topic ORDER BY negative_reviews DESC",
                 "review_insights"),
}

KEYWORDS = [
    (("changed", "last run", "previous run", "run history", "diff"), "changes"),
    (("come from", "origin", "calculated", "column lineage"), "column_lineage"),
    (("lineage", "pipeline", "flow"), "lineage"),
    (("quality", "dropped", "invalid", "clean"), "quality"),
    (("table", "dataset", "available"), "tables"),
    (("complain", "sentiment", "negative", "review"), "complain"),
    (("country", "countries", "region"), "country"),
    (("product", "item", "best"), "product"),
    (("customer", "client"), "customer"),
]


def md_table(columns: list[str], rows: list[list]) -> str:
    def fmt(v):
        return "" if v is None else f"{v:,.2f}" if isinstance(v, float) else str(v).replace("|", "\\|")

    lines = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    lines += ["| " + " | ".join(fmt(v) for v in row) + " |" for row in rows]
    return "\n".join(lines)


async def explain(dataset: str, column: str) -> str:
    res = await call("explain_column", dataset=dataset, column=column)
    if "error" in res:
        return f"Error: {res['error']}"
    ref = f"{dataset}.{column}"
    out = [f"**`{ref}`** ({res['data_type']}): {res.get('description') or '_undocumented_'}",
           f"Profile: {res['null_pct']}% nulls, {res['distinct_count']} distinct, e.g. {res['sample_values']}."]
    if res["steps"]:
        out.append("**How it is calculated**\n\n" + md_table(
            ["column", "derived from", "transformation"],
            [[f'{s["target_dataset"]}.{s["target_column"]}', f'{s["source_dataset"]}.{s["source_column"]}',
              s["transformation"]] for s in res["steps"]]))
    if res["origins"]:
        out.append("Original source columns: " + ", ".join(f"`{o}`" for o in res["origins"]))
    if res["downstream"]:
        out.append("**Used by**: " + ", ".join(f'`{d["dataset"]}.{d["column"]}`' for d in res["downstream"]))
    if res["quality"]:
        out.append("**Data quality** (job " + ", ".join(res["built_by"]) + "): "
                   + ", ".join(f'{q["rule"]} dropped {q["rows_affected"]}' for q in res["quality"]))
    if c := res.get("contract"):
        out.append(f"**Contract**: {c['status']} (owner {c['owner']}, {c['freshness']}, {c['failed']} failed check(s))")
    out.append(f"Sources: `data_dictionary`, `lineage_columns` (run `{res['run_id'][:8]}`)")
    return "\n\n".join(out)


async def answer(question: str) -> str:
    if m := COLUMN_REF.search(question):
        return await explain(m.group(1).lower(), m.group(2).lower())
    q = question.lower()
    intent = next((name for words, name in KEYWORDS if any(w in q for w in words)), None)

    if intent in QUERIES:
        title, sql, table = QUERIES[intent]
        res = await call("query", sql=sql)
        if "error" in res:
            return f"Error: {res['error']}"
        return f"**{title}**\n\n{md_table(res['columns'], res['rows'])}\n\nSources: `gold.{table}`\n\n```sql\n{sql}\n```"

    if intent == "column_lineage":
        res = await call("get_column_lineage", dataset="gold.customer_360", column="revenue")
        if "error" in res:
            return f"Error: {res['error']}"
        rows = [[f'{s["target_dataset"]}.{s["target_column"]}', f'{s["source_dataset"]}.{s["source_column"]}',
                 s["transformation"]] for s in res["steps"]]
        return ("**Lineage of `gold.customer_360.revenue`**\n\n" + md_table(["column", "derived from", "transformation"], rows)
                + "\n\nOriginal source columns: " + ", ".join(f"`{o}`" for o in res["origins"])
                + f"\n\nSources: `lineage_columns` (run `{res['run_id'][:8]}`)")

    if intent == "lineage":
        res = await call("get_lineage")
        rows = [[j["job_name"], j["layer"], j["inputs"], j["outputs"], j["rows_in"], j["rows_out"], j["rows_dropped"]]
                for j in res["jobs"]]
        return (f"**Pipeline lineage (run `{res['run_id'][:8]}`)**\n\n"
                + md_table(["job", "layer", "inputs", "outputs", "rows in", "rows out", "dropped"], rows)
                + "\n\nOpen the **Lineage** tab for the graph.\n\nSources: `lineage_jobs`, `lineage_edges`")

    if intent == "quality":
        res = await call("get_data_quality")
        rows = [[r["job_name"], r["rule"], r["rows_affected"]] for r in res["rules"]]
        return (f"**Data quality, run `{res['run_id'][:8]}`: {res['total_rows_dropped']} rows dropped**\n\n"
                + md_table(["job", "rule", "rows dropped"], rows) + "\n\nSources: `data_quality`")

    if intent == "changes":
        res = await call("compare_runs")
        if "error" in res:
            return f"Error: {res['error']}"
        s = res["summary"]
        rows = [[d["dataset"], d["status"], d["rows_base"], d["rows"], d["rows_delta"],
                 ", ".join([f"+{c}" for c in d["columns_added"]] + [f"-{c}" for c in d["columns_removed"]]
                           + [f'{t["column"]}: {t["from"]}→{t["to"]}' for t in d["type_changes"]])]
                for d in res["datasets"] if d["status"] != "unchanged"]
        out = (f"**Run `{res['run']['run_id'][:8]}` vs `{res['base']['run_id'][:8]}`:** "
               f"{s['datasets_added']} dataset(s) added, {s['datasets_removed']} removed, "
               f"{s['row_changes']} row-count change(s), {s['schema_changes']} schema change(s), "
               f"{s['quality_changes']} data-quality and {s['contract_changes']} contract change(s).\n\n")
        out += md_table(["dataset", "status", "rows before", "rows after", "Δ", "schema"], rows) if rows else \
            "No dataset changed.\n"
        if res["quality"]:
            out += "\n\n" + md_table(["job", "rule", "dropped before", "dropped now"],
                                      [[q["job_name"], q["rule"], q["base"], q["current"]] for q in res["quality"]])
        if res["contracts"]:
            out += "\n\n" + md_table(["dataset", "check", "before", "now"],
                                      [[c["dataset"], c["check"], c["base"], c["current"]] for c in res["contracts"]])
        return out + "\n\nOpen the **Run history** tab to compare any two runs.\n\nSources: `lineage_outputs`, " \
            "`data_dictionary`, `data_quality`, `contract_checks`"

    if intent == "tables":
        res = await call("list_tables")
        return "**Available tables**\n\n" + md_table(["table", "layer", "rows", "description"],
                                                    [[t["table"], t["layer"], t["rows"], t["description"]] for t in res])

    return HELP
