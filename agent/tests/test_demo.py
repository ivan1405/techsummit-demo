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
