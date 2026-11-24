from datetime import datetime, timedelta, timezone

import pytest
from test_run import lake  # noqa: F401

from pipeline import ops, serve
from pipeline.run import run


def test_schedule_validation_and_next_run(lake):  # noqa: F811
    lake.ensure_buckets()
    assert ops.schedule(lake)["enabled"] is False and ops.schedule(lake)["next_run_at"] is None
    with pytest.raises(ops.OpsError):
        ops.set_schedule(lake, {"enabled": True, "interval_minutes": 0})
    s = ops.set_schedule(lake, {"enabled": True, "interval_minutes": 5})
    since = datetime.fromisoformat(s["since"])
    assert datetime.fromisoformat(s["next_run_at"]) == since + timedelta(minutes=5)
    assert not ops.due(lake, 0, since + timedelta(minutes=4))
    assert ops.due(lake, 0, since + timedelta(minutes=5))


def test_tick_runs_the_pipeline_when_due_and_records_the_trigger(lake):  # noqa: F811
    lake.ensure_buckets()
    s = ops.set_schedule(lake, {"enabled": True, "interval_minutes": 1})
    now = datetime.fromisoformat(s["since"])
    assert serve.tick(lake, now) is None
    summary = serve.tick(lake, now + timedelta(minutes=1))
    assert summary["trigger"] == "scheduled" and summary["alerts"] == []
    assert serve.tick(lake, now + timedelta(minutes=1, seconds=30)) is None
    assert lake.read_parquet("lineage", "lineage_runs")["trigger"].tolist() == ["scheduled"]


def test_late_source_reuses_last_delivery_and_raises_an_alert(lake):  # noqa: F811
    first = run(lake.settings, lake)
    assert ops.arrivals(lake)[0]["status"] == "on_time"
    with pytest.raises(ops.OpsError):
        ops.delay(lake, "nope")
    assert {a["source"]: a["status"] for a in ops.delay(lake, "orders")}["orders"] == "late"
    second = run(lake.settings, lake)
    assert second["bronze"]["orders"] == first["bronze"]["orders"]
    assert [(a["severity"], a["kind"], a["dataset"]) for a in second["alerts"]] == [
        ("critical", "late_source", "bronze.orders")]
    alerts = lake.read_parquet("lineage", "alerts")
    assert alerts["run_id"].tolist() == [second["run_id"]]
    freshness = lake.read_parquet("lineage", "source_freshness")
    assert len(freshness) == 6 and freshness[freshness["status"] == "late"]["source"].tolist() == ["orders"]
    ops.deliver(lake, "orders")
    assert run(lake.settings, lake)["alerts"] == []


def test_alerts_cover_contracts_and_failed_sources():
    rows = ops.alerts("r1", [], [{"dataset": "sales", "check": "rows", "status": "fail"}], {"loyalty": "boom"})
    assert [(a["kind"], a["dataset"]) for a in rows] == [("contract_failed", "gold.sales"),
                                                         ("source_failed", "source.loyalty")]


def test_notify_posts_alerts_to_the_webhook(monkeypatch):
    sent = []
    monkeypatch.setattr(ops.urllib.request, "urlopen", lambda req, timeout: sent.append(req.data))
    ops.notify("http://hook", [{"run_id": "r1", "severity": "warning", "dataset": "bronze.orders", "message": "late"}])
    ops.notify("", [{"run_id": "r1"}])
    assert len(sent) == 1 and b"[WARNING] bronze.orders: late" in sent[0]
