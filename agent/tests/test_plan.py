from app import plan

SOURCE = {"name": "tickets", "dataset": "source.tickets.db", "kind": "records", "rows": 120,
          "columns": [{"column_name": c} for c in ("ticket_no", "cust", "priority")],
          "docs": {"priority": {"description": "Agent-set urgency"}}, "doc": {"title": "Tickets", "url": "http://x/1"}}
LINKS = [{"source_column": "cust", "relation": "join_key", "target": "silver.customers.customer_id"},
         {"source_column": "priority", "relation": "new", "target": None}]


def validation(feed="gold.tickets", status="ok"):
    return {"feed": feed, "status": status,
            "checks": [{"source_column": "cust", "status": status, "overlap": 0.95, "message": "95% match"}],
            "impact": {"summary": {"added": 2, "impacted": 0}, "warnings": [],
                       "columns": [{"dataset": feed, "column": "priority", "status": "added"},
                                   {"dataset": feed, "column": "ticket_no", "status": "added"}],
                       "nodes": [{"id": feed, "status": "added", "columns": ["priority", "ticket_no"]},
                                 {"id": "gold.sales", "status": "unchanged", "columns": []}]}}


def test_new_feed_plan():
    p = plan.build(SOURCE, LINKS, validation(), [])
    assert p["implementable"] and p["id"] == plan.plan_id(SOURCE, LINKS, "gold.tickets")
    assert [x["dataset"] for x in p["layers"]] == ["bronze.tickets", "silver.tickets", "gold.tickets"]
    assert p["layers"][2]["how"] == "silver.tickets LEFT JOIN silver.customers."
    assert p["joins"][0]["expected_matches"] == 114
    assert p["contract"]["owner"] == "Onboarded source" and p["contract"]["columns"] == ["ticket_no", "cust", "priority"]
    assert p["contract"]["rules"][-1] == "Columns joined from silver.customers are part of the schema."
    assert [q["rule"] for q in p["quality"]] == ["duplicate_row", "null_join_keys", "typing"]
    assert p["mappings"][2]["description"] == "Agent-set urgency"
    assert p["impact"]["datasets"] == ["gold.tickets"]
    md = plan.to_markdown(p)
    assert "| cust | silver.customers.customer_id | 95% | 114 | ok |" in md and "[Tickets](http://x/1)" in md
    assert "<h2>Joins</h2>" in plan.to_storage(p)


def test_existing_feed_extends_contract_and_plan_id_tracks_links():
    p = plan.build(SOURCE, LINKS, validation("gold.customer_360"),
                   [{"dataset": "gold.customer_360", "owner": "CRM", "max_age_minutes": 60}])
    assert p["layers"][2]["action"] == "enrich" and p["contract"]["owner"] == "CRM"
    assert p["contract"]["add_columns"] == ["priority", "ticket_no"]
    moved = [{**LINKS[0], "target": "silver.orders.customer_id"}, LINKS[1]]
    assert plan.plan_id(SOURCE, moved, "gold.tickets") != plan.plan_id(SOURCE, LINKS, "gold.tickets")


def test_blockers():
    p = plan.build({**SOURCE, "schema_only": True}, LINKS, validation(status="fail"), [])
    assert not p["implementable"] and len(p["blockers"]) == 2
