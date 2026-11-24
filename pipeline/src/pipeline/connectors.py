"""Connectors behind named connection strings: Postgres, MySQL, SQLite and HTTP(S) files.

All database access is read-only (read-only transactions / sessions, sqlite mode=ro) and row-capped."""

import csv
import io
import json
import re
import sqlite3
import urllib.parse
import urllib.request
from datetime import date, datetime
from decimal import Decimal

TABLE_RE = re.compile(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)?$")
TYPES = {"postgresql": "Postgres", "mysql": "MySQL", "sqlite": "SQLite", "http": "HTTP(S) file"}
SCHEMA_SQL = {
    "postgresql": "SELECT table_schema, table_name, column_name, data_type FROM information_schema.columns "
                  "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') "
                  "ORDER BY table_schema, table_name, ordinal_position",
    "mysql": "SELECT table_schema, table_name, column_name, data_type FROM information_schema.columns "
             "WHERE table_schema = DATABASE() ORDER BY table_name, ordinal_position",
}


class ConnectorError(ValueError):
    pass


def kind(url: str) -> str:
    scheme = urllib.parse.urlsplit(url).scheme.lower()
    if scheme in ("postgresql", "postgres"):
        return "postgresql"
    if scheme in ("mysql", "mysql+pymysql"):
        return "mysql"
    if scheme == "sqlite":
        return "sqlite"
    if scheme in ("http", "https"):
        return "http"
    raise ConnectorError("Supported: postgresql://, mysql://, sqlite:///path.db, http(s)://…/file.csv|json|jsonl")


def mask(url: str) -> str:
    return re.sub(r"(://[^:/@]+):[^@]*@", r"\1:***@", url)


def _scalar(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray)):
        return v.hex()
    return v


def _select(table: str = "", query: str = "", limit: int = 5000, quote: str = '"') -> str:
    if table:
        if not TABLE_RE.match(table):
            raise ConnectorError("Table must be a plain name, optionally schema-qualified.")
        sql = "SELECT * FROM " + ".".join(f"{quote}{p}{quote}" for p in table.split("."))
    else:
        sql = (query or "").strip().rstrip(";")
        if not re.match(r"(?is)^(select|with)\b", sql) or ";" in sql:
            raise ConnectorError("Query must be a single SELECT statement.")
    return f"SELECT * FROM ({sql}) AS src LIMIT {int(limit)}"


def _execute(url: str, sql: str) -> tuple[list[str], list[tuple]]:
    k = kind(url)
    try:
        if k == "postgresql":
            import psycopg

            with psycopg.connect(url, connect_timeout=5,
                                 options="-c default_transaction_read_only=on -c statement_timeout=30000") as conn:
                conn.read_only = True
                cur = conn.execute(sql)
                return [d.name for d in cur.description], cur.fetchall()
        if k == "mysql":
            import pymysql

            u = urllib.parse.urlsplit(url)
            conn = pymysql.connect(host=u.hostname, port=u.port or 3306, user=urllib.parse.unquote(u.username or ""),
                                   password=urllib.parse.unquote(u.password or ""), database=u.path.lstrip("/"),
                                   connect_timeout=5, read_timeout=30)
            try:
                with conn.cursor() as cur:
                    cur.execute("SET SESSION TRANSACTION READ ONLY")
                    cur.execute("START TRANSACTION READ ONLY")
                    cur.execute(sql)
                    return [d[0] for d in cur.description], list(cur.fetchall())
            finally:
                conn.close()
        if k == "sqlite":
            conn = sqlite3.connect(f"file:{url.removeprefix('sqlite:///')}?mode=ro", uri=True)
            try:
                cur = conn.execute(sql)
                return [d[0] for d in cur.description], cur.fetchall()
            finally:
                conn.close()
    except ConnectorError:
        raise
    except Exception as exc:  # noqa: BLE001 - driver errors, without the connection string
        raise ConnectorError(f"{TYPES[k]} error: {type(exc).__name__}: {str(exc).splitlines()[0]}") from exc
    raise ConnectorError("Not a database connection.")


def _fetch(url: str, max_bytes: int = 20_000_000) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "techsummit-connector"})
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            data = res.read(max_bytes + 1)
    except Exception as exc:  # noqa: BLE001
        raise ConnectorError(f"HTTP error: {exc}") from exc
    if len(data) > max_bytes:
        raise ConnectorError("File too large.")
    return data


def file_name(url: str) -> str:
    name = urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1]
    if not re.search(r"\.(csv|json|jsonl)$", name):
        raise ConnectorError("HTTP connections must point at a .csv, .json or .jsonl file.")
    return name


def _records_from_file(name: str, data: bytes) -> list[dict]:
    text = data.decode()
    if name.endswith(".csv"):
        return list(csv.DictReader(io.StringIO(text)))
    if name.endswith(".jsonl"):
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    obj = json.loads(text)
    if isinstance(obj, dict):
        lists = [v for v in obj.values() if isinstance(v, list) and v and isinstance(v[0], dict)]
        obj = lists[0] if len(lists) == 1 else [obj]
    return [r for r in obj if isinstance(r, dict)]


def read(url: str, table: str = "", query: str = "", limit: int = 5000) -> list[dict]:
    """Rows of a table / SELECT (databases) or of the file (HTTP), as JSON-safe dicts."""
    k = kind(url)
    if k == "http":
        return _records_from_file(file_name(url), _fetch(url))[:limit]
    cols, rows = _execute(url, _select(table, query, limit, "`" if k == "mysql" else '"'))
    return [{c: _scalar(v) for c, v in zip(cols, r)} for r in rows]


def schema(url: str) -> list[dict]:
    """Tables and columns available behind a connection."""
    k = kind(url)
    if k == "http":
        rows = read(url, limit=50)
        cols = list(dict.fromkeys(c for r in rows for c in r))
        return [{"table": file_name(url), "columns": [{"name": c, "type": "text"} for c in cols]}]
    if k == "sqlite":
        tables = [r[0] for r in _execute(url, "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') "
                                              "AND name NOT LIKE 'sqlite_%' ORDER BY name")[1]]
        out = []
        for t in tables:
            info = _execute(url, f"SELECT name, type FROM pragma_table_info('{t.replace(chr(39), '')}')")[1]
            out.append({"table": t, "columns": [{"name": n, "type": (ty or "").lower()} for n, ty in info]})
        return out
    tables: dict[str, list] = {}
    for sch, tbl, col, typ in _execute(url, SCHEMA_SQL[k])[1]:
        name = tbl if k == "mysql" or sch == "public" else f"{sch}.{tbl}"
        tables.setdefault(name, []).append({"name": col, "type": typ})
    return [{"table": t, "columns": c} for t, c in tables.items()]


def test(url: str) -> dict:
    tables = schema(url)
    return {"ok": True, "type": kind(url), "tables": len(tables)}
