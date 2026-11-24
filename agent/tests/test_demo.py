import asyncio

from app import demo

DIFF = {
    "run": {"run_id": "bbbbbbbb-2"}, "base": {"run_id": "aaaaaaaa-1"},
    "summary": {"datasets_added": 1, "datasets_removed": 0, "row_changes": 1, "schema_changes": 2,
                "quality_changes": 1, "contract_changes": 0},
    "datasets": [
        {"dataset": "gold.customer_360", "status": "changed", "rows_base": 60, "rows": 60, "rows_delta": 0,
         "columns_added": ["points", "member_tier"], "columns_removed": [], "type_changes": []},
        {"dataset": "silver.orders", "status": "unchanged", "rows_base": 9, "rows": 9, "rows_delta": 0,
         "columns_added": [], "columns_removed": [], "type_changes": []},
    ],
    "quality": [{"job_name": "silver_orders", "rule": "dup", "base": 1, "current": 3, "delta": 2}],
    "contracts": [],
}


def test_what_changed_uses_compare_runs(monkeypatch):
    calls = []

    async def fake_call(name, **kwargs):
        calls.append(name)
        return DIFF

    monkeypatch.setattr(demo, "call", fake_call)
    out = asyncio.run(demo.answer("What changed since the last run?"))
    assert calls == ["compare_runs"]
    assert "`bbbbbbbb` vs `aaaaaaaa`" in out and "+points, +member_tier" in out
    assert "silver.orders" not in out and "| silver_orders | dup | 1 | 3 |" in out


def test_column_question_uses_explain_column(monkeypatch):
    calls = []

    async def fake_call(name, **kwargs):
        calls.append((name, kwargs))
        return {"run_id": "r1234567890", "data_type": "float64", "description": "Revenue in EUR.", "null_pct": 0.0,
                "distinct_count": 50, "sample_values": "10.0", "origins": ["source.orders.csv.unit_price"],
                "steps": [{"target_dataset": "gold.customer_360", "target_column": "revenue",
                           "source_dataset": "gold.sales", "source_column": "amount", "transformation": "SUM"}],
                "downstream": [], "built_by": ["gold_customer_360"], "quality": [],
                "contract": {"status": "green", "owner": "CRM", "freshness": "fresh", "failed": 0}}

    monkeypatch.setattr(demo, "call", fake_call)
    out = asyncio.run(demo.answer("How is Gold.Customer_360.revenue calculated and where does it come from?"))
    assert calls == [("explain_column", {"dataset": "gold.customer_360", "column": "revenue"})]
    assert "Revenue in EUR." in out and "| gold.customer_360.revenue | gold.sales.amount | SUM |" in out
    assert "**Contract**: green (owner CRM" in out


def test_md_table_escapes_pipes_and_raw_file_refs_are_not_columns():
    assert demo.md_table(["rule"], [["%Y-%m-%d | %d/%m/%Y"]]).endswith("| %Y-%m-%d \\| %d/%m/%Y |")
    assert demo.COLUMN_REF.search("where is source.orders.csv.quantity used?") is None


def test_alerts_question_uses_get_alerts(monkeypatch):
    calls = []

    async def fake_call(name, **kwargs):
        calls.append(name)
        return {"run_id": "abcdef123", "active": [{"severity": "critical", "dataset": "bronze.orders",
                                                   "message": "No new orders delivery for 180 min"}],
                "sources": [{"source": "orders", "status": "late", "age_minutes": 180.0, "max_age_minutes": 60}]}

    monkeypatch.setattr(demo, "call", fake_call)
    text = asyncio.run(demo.answer("Is any data late?"))
    assert calls == ["get_alerts"] and "1 active alert" in text and "bronze.orders" in text


def test_insights_draw_charts(monkeypatch):
    from app import mcp_tools

    async def fake_call(name, sql, **kwargs):
        if "country" in sql:
            return {"columns": ["country", "orders", "revenue"], "rows": [["ES", 3, 300.0], ["FR", 1, 100.0]]}
        if "order_date" in sql:
            return {"columns": ["day", "revenue"], "rows": [["2026-07-01", 50.0], ["2026-07-02", 80.0]]}
        return {"columns": ["topic", "negative_reviews", "avg_rating"], "rows": [["delivery", 4, 1.5]]}

    monkeypatch.setattr(demo, "call", fake_call)
    recorded = []
    token = mcp_tools.calls.set(recorded)
    try:
        out = asyncio.run(demo.answer("Generate image insights"))
    finally:
        mcp_tools.calls.reset(token)
    charts = [r for r in recorded if r[0] == "render_chart"]
    assert [c[2]["chart"]["kind"] for c in charts] == ["pie", "line", "bar"]
    assert charts[0][2]["chart"]["labels"] == ["ES", "FR"] and "75%" in out and "delivery" in out
