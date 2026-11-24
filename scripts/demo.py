#!/usr/bin/env python3
"""Smoke-test the demo through the UI's /api proxy and print the presenter's guided tour.

    python3 scripts/demo.py --check   # verify every feature the tour shows, exit 1 on failure
    python3 scripts/demo.py --tour    # print the tour and open the UI with the in-app tour started

Standard library only, so `make demo` runs without installing anything on the host.
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import webbrowser

TOUR = [
    ("Chat", "#chat", 'Ask "Revenue by country". Expand the SQL and the MCP tool calls under the answer.'),
    ("Lineage", "#lineage", "Click gold.customer_360, then the revenue column: it traces back to orders.csv."),
    ("Data dictionary", "#dictionary",
     "Personal data is tagged (PII) and masked for the reader role. Switch to Analyst in the header to see raw "
     "values. Click ? next to a column to ask how it is calculated."),
    ("Onboard a source", "#dictionary",
     "+ Add data source > Saved connection 'erp' (demo Postgres), table support_tickets, or upload "
     "data/samples/loyalty.json. Review the suggested links, validate, approve the plan, Accept & implement."),
    ("Impact analysis", "#impact", "Propose dropping silver.orders.amount and see what turns red downstream."),
    ("Data quality", "#quality", "Rows dropped per rule and the data contract of every gold table."),
    ("Run history", "#runs",
     "Simulate late delivery for orders, then Run pipeline now: a critical alert appears and the header shows a "
     "red badge. Turn on the schedule to see runs arrive by themselves. Deliver now clears it."),
    ("Chat", "#chat", 'Ask "Is any data late?" and "What changed since the last run?".'),
]


ERP = {"name": "erp", "url": "postgresql://reader:reader@source-db:5432/erp",
       "description": "ERP read replica (demo Postgres)"}


def seed(base: str) -> None:
    """Save the demo Postgres as connection 'erp' so the onboarding step only needs a click."""
    if not any(c["name"] == ERP["name"] for c in call(base, "/connections")):
        call(base, "/connections", ERP)


def call(base: str, path: str, body: dict | None = None, method: str | None = None, timeout: float = 120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{base}/api{path}", data=data, method=method or ("POST" if data else "GET"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.loads(res.read() or b"null")


def wait_ready(base: str, timeout: float = 180) -> None:
    deadline = time.time() + timeout
    while True:
        try:
            if call(base, "/health", timeout=5).get("status") == "ok" and call(base, "/runs?limit=1", timeout=10):
                return
        except (OSError, ValueError, urllib.error.URLError):
            pass
        if time.time() > deadline:
            sys.exit(f"The stack at {base} is not ready. Run `make up` and check `docker compose logs`.")
        time.sleep(3)


def chat(base: str, question: str) -> str:
    return call(base, "/chat", {"messages": [{"role": "user", "content": question}]})["reply"]


def checks(base: str):
    yield "Pipeline ran", lambda: call(base, "/runs?limit=5")["runs"][0]["status"] == "COMPLETE"
    yield "Chat answers from gold", lambda: "revenue" in chat(base, "Revenue by country").lower()
    yield "Column lineage reaches the source", lambda: "bronze" in json.dumps(
        call(base, "/lineage/column?dataset=gold.customer_360&column=revenue"))
    yield "Dictionary has PII tags", lambda: any(
        c.get("pii") for d in call(base, "/dictionary")["datasets"] for c in d["columns"])
    yield "Data contracts evaluated", lambda: bool(call(base, "/contracts"))
    yield "Saved connection 'erp' lists support_tickets", lambda: "support_tickets" in json.dumps(
        call(base, f"/connections/{ERP['name']}/schema"))
    yield "Confluence docs searchable", lambda: bool(call(base, "/confluence/search?q=loyalty"))
    yield "Schedule API", lambda: "enabled" in call(base, "/schedule")
    yield "Alerts API", lambda: "active" in call(base, "/alerts")
    yield "Chat answers alert questions", lambda: "alert" in chat(base, "Is any data late?").lower()


def run_checks(base: str) -> bool:
    ok = True
    for name, check in checks(base):
        try:
            passed, detail = bool(check()), ""
        except Exception as exc:  # noqa: BLE001 - report every failing check, not just the first
            passed, detail = False, f" ({exc})"
        ok &= passed
        print(f"  {'ok ' if passed else 'FAIL'} {name}{detail}")
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://localhost:3000")
    parser.add_argument("--check", action="store_true", help="smoke-test every feature the tour shows")
    parser.add_argument("--tour", action="store_true", help="print the tour and open the UI")
    parser.add_argument("--no-open", action="store_true", help="don't open a browser")
    args = parser.parse_args()
    base = args.url.rstrip("/")
    wait_ready(base)
    seed(base)
    if args.check:
        print("Checking the demo:")
        if not run_checks(base):
            sys.exit("Some checks failed: see `docker compose logs`.")
    if args.tour:
        print(f"\nThe demo is ready at {base}. Guided tour (also in the app: Tour button in the header):")
        for i, (title, anchor, text) in enumerate(TOUR, 1):
            print(f"\n  {i}. {title}  {base}/{anchor}\n     {text}")
        print("\nWithout an LLM key the chatbot runs in demo mode. Stop with `make down`; start fresh with `make demo`.")
        if not args.no_open:
            webbrowser.open(f"{base}/?tour=1#chat")


if __name__ == "__main__":
    main()
