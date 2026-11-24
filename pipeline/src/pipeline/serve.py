"""Small HTTP API to onboard accepted sources and re-run the pipeline (used by the agent API)."""

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from datetime import datetime, timezone

from . import connectors, onboarded, ops
from .config import Settings
from .lake import Lake
from .run import run

log = logging.getLogger("pipeline.api")
_run_lock = threading.Lock()
MAX_BODY = 8_000_000


def _lake() -> Lake:
    lake = Lake(Settings())
    lake.ensure_buckets()
    return lake


def _run(lake: Lake, trigger: str = "manual") -> dict:
    with _run_lock:
        return run(lake.settings, lake, trigger)


def tick(lake: Lake, now: datetime | None = None) -> dict | None:
    """Start a scheduled run when one is due (skipped while another run is in progress)."""
    now = now or datetime.now(timezone.utc)
    if not ops.due(lake, lake.settings.schedule_minutes, now) or _run_lock.locked():
        return None
    ops.mark_scheduled(lake, now)
    return _run(lake, "scheduled")


def _scheduler(stop: threading.Event, every_s: float = 5) -> None:
    lake = None
    while not stop.wait(every_s):
        try:
            lake = lake or _lake()
            if summary := tick(lake):
                log.info("Scheduled run %s finished with %d alert(s)", summary["run_id"], len(summary["alerts"]))
        except Exception:  # noqa: BLE001 - keep the scheduler alive
            log.exception("Scheduled run failed")


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body) -> None:
        data = json.dumps(body, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise onboarded.OnboardError("Request too large.")
        return json.loads(self.rfile.read(length) or b"{}")

    def _handle(self, fn) -> None:
        try:
            self._send(200, fn())
        except (onboarded.OnboardError, connectors.ConnectorError, ops.OpsError, json.JSONDecodeError) as exc:
            self._send(400, {"detail": str(exc)})
        except Exception as exc:  # noqa: BLE001
            log.exception("Request failed")
            self._send(500, {"detail": f"{type(exc).__name__}: {exc}"})

    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            return self._send(200, {"status": "ok"})
        if self.path == "/sources":
            return self._handle(lambda: [onboarded.public(s) for s in onboarded.load(_lake())])
        if self.path == "/connections":
            return self._handle(lambda: onboarded.list_connections(_lake()))
        if self.path == "/schedule":
            return self._handle(lambda: ops.schedule(_lake(), Settings().schedule_minutes))
        if self.path == "/arrivals":
            return self._handle(lambda: ops.arrivals(_lake()))
        if self.path.startswith("/connections/") and self.path.endswith("/schema"):
            name = self.path.removeprefix("/connections/").removesuffix("/schema")
            return self._handle(lambda: connectors.schema(onboarded.get_connection(_lake(), name)["url"]))
        self._send(404, {"detail": "Not found"})

    def do_POST(self):  # noqa: N802
        if self.path == "/sources":
            return self._handle(self._onboard)
        if self.path == "/run":
            return self._handle(lambda: _run(_lake()))
        if self.path.startswith("/arrivals/") and self.path.endswith("/late"):
            source = self.path.removeprefix("/arrivals/").removesuffix("/late")
            return self._handle(lambda: ops.delay(_lake(), source, self._body().get("minutes")))
        if self.path == "/connections":
            return self._handle(lambda: onboarded.save_connection(_lake(), self._body()))
        if self.path.startswith("/connections/") and self.path.endswith("/sample"):
            name = self.path.removeprefix("/connections/").removesuffix("/sample")
            return self._handle(lambda: self._sample(name))
        self._send(404, {"detail": "Not found"})

    def do_PUT(self):  # noqa: N802
        if self.path == "/schedule":
            return self._handle(lambda: ops.set_schedule(_lake(), self._body()))
        self._send(404, {"detail": "Not found"})

    def do_DELETE(self):  # noqa: N802
        if self.path.startswith("/arrivals/") and self.path.endswith("/late"):
            source = self.path.removeprefix("/arrivals/").removesuffix("/late")
            return self._handle(lambda: ops.deliver(_lake(), source))
        if self.path.startswith("/sources/"):
            name = self.path.removeprefix("/sources/")
            return self._handle(lambda: self._offboard(name))
        if self.path.startswith("/connections/"):
            name = self.path.removeprefix("/connections/")
            return self._handle(lambda: onboarded.delete_connection(_lake(), name) or {"removed": name})
        self._send(404, {"detail": "Not found"})

    def _onboard(self) -> dict:
        body = self._body()
        lake = _lake()
        checked = onboarded.preview(lake, body.get("spec") or {}, body.get("content") or "")
        spec = onboarded.register(lake, checked["spec"], body.get("content") or "")
        summary = _run(lake)
        if spec["name"] in summary["onboarding_errors"]:
            onboarded.unregister(lake, spec["name"])
            raise onboarded.OnboardError(f"Pipeline failed for {spec['name']}: {summary['onboarding_errors'][spec['name']]}")
        return {"source": onboarded.public(spec), "run": summary}

    def _sample(self, name: str) -> dict:
        body = self._body()
        lake = _lake()
        url = onboarded.get_connection(lake, name)["url"]
        rows = connectors.read(url, body.get("table", ""), body.get("query", ""), min(int(body.get("limit", 5000)), 5000))
        return {"label": onboarded.connection_label(lake, body.get("name") or "source", name), "rows": rows}

    def _offboard(self, name: str) -> dict:
        lake = _lake()
        onboarded.unregister(lake, name)
        return {"removed": name, "run": _run(lake)}

    def log_message(self, fmt, *args):
        log.info("%s %s", self.address_string(), fmt % args)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    server = ThreadingHTTPServer(("0.0.0.0", 8090), Handler)
    threading.Thread(target=_scheduler, args=(threading.Event(),), daemon=True, name="scheduler").start()
    log.info("Pipeline API listening on :8090")
    server.serve_forever()


if __name__ == "__main__":
    main()
