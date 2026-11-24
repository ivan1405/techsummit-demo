"""Minimal offline stand-in for the Confluence Cloud REST API (v1) used by the agent.

GET  /wiki/rest/api/content/search?cql=...   space = "X", label = "y", title ~ "z", text ~ "z"
GET  /wiki/rest/api/content/<id>?expand=body.storage
POST /wiki/rest/api/content                  create a page (kept in memory)
GET  /wiki/spaces/<space>/pages/<id>         human-readable page
"""

import html
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PUBLIC_URL = os.getenv("PUBLIC_URL", "http://localhost:8095").rstrip("/")
PAGES: dict[str, dict] = {}
LOCK = threading.Lock()


def load_pages(folder: Path) -> None:
    for path in sorted(folder.glob("*.html")):
        head, _, body = path.read_text().partition("-->")
        meta = dict(re.findall(r"(\w+):\s*([^;]+)", head.removeprefix("<!--")))
        PAGES[meta["id"].strip()] = {"id": meta["id"].strip(), "title": meta["title"].strip(),
                                     "space": meta["space"].strip(), "body": body.strip(),
                                     "labels": [x.strip() for x in meta.get("labels", "").split(",") if x.strip()]}


def _text(page: dict) -> str:
    return (page["title"] + " " + re.sub(r"<[^>]+>", " ", page["body"])).lower()


def matches(page: dict, cql: str) -> bool:
    for field, op, value in re.findall(r'(\w+)\s*(=|~)\s*"((?:[^"\\]|\\.)*)"', cql):
        value = value.replace('\\"', '"').lower()
        if field == "space" and page["space"].lower() != value:
            return False
        if field == "label" and value not in [x.lower() for x in page["labels"]]:
            return False
        if field == "title" and op == "=" and page["title"].lower() != value:
            return False
    terms = [v.replace('\\"', '"').lower() for f, v in re.findall(r'(title|text)\s*~\s*"((?:[^"\\]|\\.)*)"', cql)]
    return not terms or any(all(w in _text(page) for w in t.split()) for t in terms)


def summary(page: dict) -> dict:
    return {"id": page["id"], "type": "page", "title": page["title"], "space": {"key": page["space"]},
            "metadata": {"labels": {"results": [{"name": x} for x in page["labels"]]}},
            "_links": {"webui": f"/spaces/{page['space']}/pages/{page['id']}"}}


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body, content_type: str = "application/json") -> None:
        data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        url = urlparse(self.path)
        if url.path == "/health":
            return self._send(200, {"status": "ok", "pages": len(PAGES)})
        if url.path == "/wiki/rest/api/content/search":
            cql = parse_qs(url.query).get("cql", [""])[0]
            with LOCK:
                found = [summary(p) for p in PAGES.values() if matches(p, cql)]
            return self._send(200, {"results": found[:25], "size": len(found), "_links": {"base": f"{PUBLIC_URL}/wiki"}})
        if m := re.fullmatch(r"/wiki/rest/api/content/(\d+)", url.path):
            page = PAGES.get(m.group(1))
            if not page:
                return self._send(404, {"message": "No content found with id " + m.group(1)})
            return self._send(200, {**summary(page), "body": {"storage": {"value": page["body"], "representation": "storage"}},
                                    "_links": {**summary(page)["_links"], "base": f"{PUBLIC_URL}/wiki"}})
        if m := re.fullmatch(r"/wiki/spaces/\w+/pages/(\d+)(/.*)?", url.path):
            page = PAGES.get(m.group(1))
            if not page:
                return self._send(404, "Not found", "text/plain")
            return self._send(200, f"<!doctype html><meta charset=utf-8><title>{html.escape(page['title'])}</title>"
                                   "<style>body{font:15px system-ui;max-width:900px;margin:2rem auto}"
                                   "table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:4px 8px}</style>"
                                   f"<p>Mock Confluence · space {page['space']}</p><h1>{html.escape(page['title'])}</h1>"
                                   f"{page['body']}", "text/html; charset=utf-8")
        self._send(404, {"message": "Not found"})

    def do_POST(self):  # noqa: N802
        if urlparse(self.path).path != "/wiki/rest/api/content":
            return self._send(404, {"message": "Not found"})
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        title, space = body.get("title", "").strip(), (body.get("space") or {}).get("key", "")
        value = ((body.get("body") or {}).get("storage") or {}).get("value", "")
        if not title or not space or not value:
            return self._send(400, {"message": "title, space.key and body.storage.value are required"})
        with LOCK:
            if any(p["title"] == title and p["space"] == space for p in PAGES.values()):
                return self._send(400, {"message": f"A page with this title already exists in space {space}"})
            page_id = str(max(map(int, PAGES), default=1000) + 1)
            PAGES[page_id] = {"id": page_id, "title": title, "space": space, "body": value,
                              "labels": ["implementation-plan"]}
        self._send(200, {**summary(PAGES[page_id]), "_links": {**summary(PAGES[page_id])["_links"], "base": f"{PUBLIC_URL}/wiki"}})

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    load_pages(Path(__file__).parent / "pages")
    ThreadingHTTPServer(("0.0.0.0", int(os.getenv("PORT", "8095"))), Handler).serve_forever()
