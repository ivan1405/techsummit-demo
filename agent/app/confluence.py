"""Confluence Cloud (or the bundled mock) as a source of documented schemas and a place to publish plans.

Reads use the v1 REST API: CQL search and page bodies in storage format. A schema is any table on the
page whose header has a column-name cell and a type or description cell.
"""

import html
import re
from html.parser import HTMLParser

import httpx

from .config import CONFLUENCE_API_TOKEN, CONFLUENCE_EMAIL, CONFLUENCE_SPACE, CONFLUENCE_URL

HEADERS = {
    "name": ("column", "name", "field", "attribute", "column name"),
    "type": ("type", "data type", "datatype"),
    "description": ("description", "meaning", "definition", "notes"),
    "links_to": ("links to", "link", "references", "foreign key", "join", "maps to", "lineage"),
}


class ConfluenceError(RuntimeError):
    pass


def status() -> dict:
    mock = not CONFLUENCE_API_TOKEN
    return {"configured": bool(CONFLUENCE_URL), "mode": "mock" if mock else "cloud",
            "url": CONFLUENCE_URL, "space": CONFLUENCE_SPACE}


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def cql(query: str = "", space: str = "", label: str = "") -> str:
    parts = ["type = page"]
    if space:
        parts.append(f"space = {_quote(space)}")
    if label:
        parts.append(f"label = {_quote(label)}")
    if query.strip():
        parts.append(f"(title ~ {_quote(query.strip())} OR text ~ {_quote(query.strip())})")
    return " AND ".join(parts)


async def _request(method: str, path: str, **kwargs) -> dict:
    if not CONFLUENCE_URL:
        raise ConfluenceError("Confluence is not configured: set CONFLUENCE_URL.")
    auth = (CONFLUENCE_EMAIL, CONFLUENCE_API_TOKEN) if CONFLUENCE_API_TOKEN else None
    try:
        async with httpx.AsyncClient(timeout=20, auth=auth) as client:
            res = await client.request(method, f"{CONFLUENCE_URL}/wiki/rest/api{path}", **kwargs)
    except httpx.HTTPError as exc:
        raise ConfluenceError(f"Confluence unreachable: {exc}") from exc
    if res.status_code in (401, 403):
        raise ConfluenceError("Confluence rejected the credentials (check CONFLUENCE_EMAIL and CONFLUENCE_API_TOKEN).")
    if res.status_code >= 400:
        try:
            detail = res.json().get("message", res.text)
        except ValueError:
            detail = res.text
        raise ConfluenceError(f"Confluence error {res.status_code}: {str(detail)[:200]}")
    return res.json()


def _page_url(page: dict, base: str = "") -> str:
    webui = (page.get("_links") or {}).get("webui", "")
    base = base or (page.get("_links") or {}).get("base") or f"{CONFLUENCE_URL}/wiki"
    return f"{base}{webui}" if webui else ""


def _summary(page: dict, base: str = "") -> dict:
    labels = (((page.get("metadata") or {}).get("labels") or {}).get("results")) or []
    return {"id": str(page["id"]), "title": page.get("title", ""), "space": (page.get("space") or {}).get("key", ""),
            "labels": [x.get("name") for x in labels if x.get("name")], "url": _page_url(page, base)}


async def search(query: str = "", space: str = "", label: str = "") -> dict:
    space = space or CONFLUENCE_SPACE
    res = await _request("GET", "/content/search", params={"cql": cql(query, space, label), "limit": 25,
                                                           "expand": "space,metadata.labels"})
    base = (res.get("_links") or {}).get("base", "")
    return {"cql": cql(query, space, label), "results": [_summary(p, base) for p in res.get("results", [])]}


class _Tables(HTMLParser):
    """Collect every <table> as rows of cell text."""

    def __init__(self):
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._depth = 0
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._depth += 1
            if self._depth == 1:
                self.tables.append([])
        elif self._depth == 1 and tag == "tr":
            self._row = []
        elif self._depth == 1 and tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag):
        if tag == "table":
            self._depth -= 1
        elif self._depth == 1 and tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None
        elif self._depth == 1 and tag == "tr" and self._row is not None:
            if self._row:
                self.tables[-1].append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def _header_map(row: list[str]) -> dict[str, int]:
    found = {}
    for i, cell in enumerate(row):
        text = cell.lower().strip(" :*")
        for field, names in HEADERS.items():
            if field not in found and text in names:
                found[field] = i
    return found


def extract_schemas(storage: str) -> list[list[dict]]:
    """Schema tables on a page: [[{name, type, description, links_to}, ...], ...]."""
    parser = _Tables()
    parser.feed(storage)
    schemas = []
    for rows in parser.tables:
        if not rows:
            continue
        cols = _header_map(rows[0])
        if "name" not in cols or not ({"type", "description"} & cols.keys()):
            continue
        out = []
        for row in rows[1:]:
            cell = {f: (row[i] if i < len(row) else "") for f, i in cols.items()}
            if cell.get("name"):
                out.append({f: cell.get(f, "") for f in HEADERS})
        if out:
            schemas.append(out)
    return schemas


def _text(storage: str, limit: int = 600) -> str:
    no_tables = re.sub(r"<table.*?</table>", " ", storage, flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", no_tables))).strip()[:limit]


async def page(page_id: str) -> dict:
    if not re.fullmatch(r"\d{1,20}", page_id):
        raise ConfluenceError("Page id must be numeric.")
    res = await _request("GET", f"/content/{page_id}", params={"expand": "body.storage,space,metadata.labels"})
    storage = ((res.get("body") or {}).get("storage") or {}).get("value", "")
    return {**_summary(res), "summary": _text(storage), "schemas": extract_schemas(storage)}


async def publish(title: str, storage: str, space: str = "") -> dict:
    res = await _request("POST", "/content", json={"type": "page", "title": title,
                                                   "space": {"key": space or CONFLUENCE_SPACE},
                                                   "body": {"storage": {"value": storage, "representation": "storage"}}})
    return _summary(res)
