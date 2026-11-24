import json
import sqlite3
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from pipeline import connectors, onboarded
from pipeline.config import Settings
from pipeline.lake import Lake
from pipeline.run import run

ROOT = Path(__file__).resolve().parents[2] / "data"


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "erp.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE tickets (ticket TEXT, cust TEXT, refund REAL)")
    con.executemany("INSERT INTO tickets VALUES (?, ?, ?)", [("T1", "C0001", 1.5), ("T2", "C0002", None)])
    con.execute("CREATE VIEW v_open AS SELECT ticket FROM tickets")
    con.commit()
    con.close()
    return f"sqlite:///{path}"


@pytest.fixture
def http_url():
    handler = partial(SimpleHTTPRequestHandler, directory=str(ROOT / "samples"))
    handler.log_message = lambda *a: None
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_sqlite_schema_read_and_write_protection(db):
    tables = {t["table"]: [c["name"] for c in t["columns"]] for t in connectors.schema(db)}
    assert tables == {"tickets": ["ticket", "cust", "refund"], "v_open": ["ticket"]}
    assert connectors.read(db, table="tickets") == [{"ticket": "T1", "cust": "C0001", "refund": 1.5},
                                                    {"ticket": "T2", "cust": "C0002", "refund": None}]
    for bad in ("DROP TABLE tickets", "SELECT 1; DELETE FROM tickets"):
        with pytest.raises(connectors.ConnectorError):
            connectors.read(db, query=bad)


def test_http_file_connector(http_url):
    url = f"{http_url}/returns.csv"
    assert connectors.schema(url)[0]["table"] == "returns.csv"
    assert len(connectors.read(url)) == 40
    with pytest.raises(connectors.ConnectorError):
        connectors.kind("ftp://x/y.csv")


def test_mask_hides_password():
    assert connectors.mask("mysql://u:p%40ss@h:3306/db") == "mysql://u:***@h:3306/db"


@pytest.fixture
def lake():
    with mock_aws():
        settings = Settings(sources_dir=str(ROOT / "sources"), llm_provider="none", openlineage_url="")
        lk = Lake(settings, client=boto3.client("s3", region_name="us-east-1"))
        lk.ensure_buckets()
        yield lk


def test_named_connection_feeds_an_onboarded_source(lake, db):
    saved = onboarded.save_connection(lake, {"name": "erp", "url": db, "description": "ERP replica"})
    assert saved["tables"] == 2 and saved["type"] == "sqlite"
    assert [c["name"] for c in onboarded.list_connections(lake)] == ["erp"]
    with pytest.raises(onboarded.OnboardError, match="Connection test failed"):
        onboarded.save_connection(lake, {"name": "bad", "url": "sqlite:////nope/missing.db"})
    run(lake.settings, lake)
    spec = {"name": "tickets", "kind": "connection", "connection": "erp", "table": "tickets", "feed": "gold.tickets",
            "links": [{"source_column": "cust", "relation": "join_key", "target": "silver.customers.customer_id"}]}
    onboarded.register(lake, onboarded.preview(lake, spec)["spec"])
    summary = run(lake.settings, lake)
    assert summary["onboarding_errors"] == {} and summary["gold"]["tickets"] == 2
    assert "full_name" in lake.read_parquet("gold", "tickets").columns
    with pytest.raises(onboarded.OnboardError, match="used by"):
        onboarded.delete_connection(lake, "erp")
    stored = json.loads(lake.get_bytes("registry", "sources/tickets.json"))
    assert "url" not in stored or not stored["url"]
