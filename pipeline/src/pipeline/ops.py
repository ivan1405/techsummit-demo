"""Operations: the pipeline schedule, source delivery SLAs (with simulated late deliveries) and alerts."""

import json
import logging
import urllib.request
from datetime import datetime, timedelta, timezone

import pandas as pd
from botocore.exceptions import ClientError

from .lake import Lake

log = logging.getLogger("pipeline.ops")

SCHEDULE_KEY = "ops/schedule.json"
LATE_KEY = "ops/late_sources.json"
# Max minutes between two deliveries of each core source before it counts as late.
SOURCE_SLAS = {"orders": 60, "customers": 24 * 60, "reviews": 24 * 60}
ALERT_COLUMNS = ["run_id", "raised_at", "severity", "kind", "dataset", "message"]
FRESHNESS_COLUMNS = ["run_id", "source", "last_arrival", "age_minutes", "max_age_minutes", "status"]


class OpsError(ValueError):
    pass


def _read(lake: Lake, key: str, default):
    try:
        return json.loads(lake.get_bytes(lake.settings.registry_bucket, key))
    except (ClientError, FileNotFoundError):
        return default


def _write(lake: Lake, key: str, value) -> None:
    lake.put_bytes(lake.settings.registry_bucket, key, json.dumps(value).encode())


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---- schedule -------------------------------------------------------------------------------

def schedule(lake: Lake, default_minutes: int = 0) -> dict:
    """Schedule plus when the next run is due. Without a saved schedule, SCHEDULE_MINUTES (0 = off) applies."""
    s = _read(lake, SCHEDULE_KEY, None)
    if s is None:
        s = {"enabled": default_minutes > 0, "interval_minutes": default_minutes or 5, "since": _now().isoformat(),
             "last_run_at": None}
        _write(lake, SCHEDULE_KEY, s)
    return {**s, "next_run_at": next_run_at(s)}


def next_run_at(s: dict) -> str | None:
    if not s.get("enabled"):
        return None
    base = max(filter(None, [s.get("since"), s.get("last_run_at")]))
    return (datetime.fromisoformat(base) + timedelta(minutes=s["interval_minutes"])).isoformat()


def set_schedule(lake: Lake, body: dict) -> dict:
    try:
        minutes = int(body.get("interval_minutes", 5))
    except (TypeError, ValueError) as exc:
        raise OpsError("interval_minutes must be a number.") from exc
    if not 1 <= minutes <= 24 * 60:
        raise OpsError("interval_minutes must be between 1 and 1440.")
    s = {**_read(lake, SCHEDULE_KEY, {}), "enabled": bool(body.get("enabled")), "interval_minutes": minutes,
         "since": _now().isoformat()}
    s.setdefault("last_run_at", None)
    _write(lake, SCHEDULE_KEY, s)
    return {**s, "next_run_at": next_run_at(s)}


def due(lake: Lake, default_minutes: int, now: datetime | None = None) -> bool:
    nxt = schedule(lake, default_minutes)["next_run_at"]
    return nxt is not None and (now or _now()) >= datetime.fromisoformat(nxt)


def mark_scheduled(lake: Lake, at: datetime) -> None:
    _write(lake, SCHEDULE_KEY, {**_read(lake, SCHEDULE_KEY, {}), "last_run_at": at.isoformat()})


# ---- source deliveries ----------------------------------------------------------------------

def _check_source(source: str) -> None:
    if source not in SOURCE_SLAS:
        raise OpsError(f"Unknown source '{source}'. Sources with a delivery SLA: {', '.join(SOURCE_SLAS)}.")


def delay(lake: Lake, source: str, minutes: int | None = None) -> list[dict]:
    """Simulate a late delivery: the source's last delivery moves back past its SLA (default: twice the SLA)."""
    _check_source(source)
    minutes = int(minutes or 2 * SOURCE_SLAS[source])
    if not 1 <= minutes <= 7 * 24 * 60:
        raise OpsError("minutes must be between 1 and 10080.")
    late = _read(lake, LATE_KEY, {})
    late[source] = (_now() - timedelta(minutes=minutes)).isoformat()
    _write(lake, LATE_KEY, late)
    return arrivals(lake)


def deliver(lake: Lake, source: str) -> list[dict]:
    """End a simulated delay: the source is delivered again from the next run."""
    _check_source(source)
    late = _read(lake, LATE_KEY, {})
    late.pop(source, None)
    _write(lake, LATE_KEY, late)
    return arrivals(lake)


def arrivals(lake: Lake, run_id: str = "", now: datetime | None = None) -> list[dict]:
    """Delivery status per core source. Bundled files are re-delivered on every run unless a delay is simulated."""
    now = now or _now()
    late = _read(lake, LATE_KEY, {})
    rows = []
    for source, sla in SOURCE_SLAS.items():
        last = datetime.fromisoformat(late[source]) if source in late else now
        age = round((now - last).total_seconds() / 60, 1)
        rows.append({"run_id": run_id, "source": source, "last_arrival": last.isoformat(), "age_minutes": age,
                     "max_age_minutes": sla, "status": "late" if age > sla else "on_time"})
    return rows


# ---- alerts ---------------------------------------------------------------------------------

def alerts(run_id: str, freshness: list[dict], contract_rows: list[dict], errors: dict[str, str]) -> list[dict]:
    raised = _now().isoformat()
    out = []
    for f in freshness:
        if f["status"] == "late":
            out.append({"severity": "critical" if f["age_minutes"] > 2 * f["max_age_minutes"] else "warning",
                        "kind": "late_source", "dataset": f"bronze.{f['source']}",
                        "message": f"No new {f['source']} delivery for {f['age_minutes']:.0f} min (SLA "
                                   f"{f['max_age_minutes']} min): bronze.{f['source']} and everything downstream "
                                   "still use the last delivery."})
    failed: dict[str, list[str]] = {}
    for c in contract_rows:
        if c["status"] == "fail":
            failed.setdefault(f"gold.{c['dataset']}".replace("gold.gold.", "gold."), []).append(c["check"])
    out += [{"severity": "critical", "kind": "contract_failed", "dataset": d,
             "message": f"Data contract failed: {', '.join(checks)}."} for d, checks in sorted(failed.items())]
    out += [{"severity": "critical", "kind": "source_failed", "dataset": f"source.{name}",
             "message": f"Onboarded source failed: {err}"} for name, err in sorted(errors.items())]
    return [{"run_id": run_id, "raised_at": raised, **a} for a in out]


def table(rows: list[dict], columns: list[str]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=columns)
    return df.astype({c: "string" for c in columns if df[c].dtype == object or df.empty})


def notify(url: str, run_alerts: list[dict]) -> None:
    """Best-effort POST of a run's alerts to a webhook (Slack-compatible {"text": ...} payload)."""
    if not url or not run_alerts:
        return
    lines = [f"[{a['severity'].upper()}] {a['dataset']}: {a['message']}" for a in run_alerts]
    body = json.dumps({"text": f"Data platform alerts (run {run_alerts[0]['run_id'][:8]}):\n" + "\n".join(lines),
                       "alerts": run_alerts}).encode()
    try:
        urllib.request.urlopen(urllib.request.Request(url, body, {"Content-Type": "application/json"}), timeout=5)
    except Exception as exc:  # noqa: BLE001 - alerting must never fail a run
        log.warning("Alert webhook failed: %s", exc)
