"""FastAPI chat API in front of the LangGraph agent."""

import json
import logging

import httpx

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import BaseModel

from . import confluence, linker, mcp_tools, plan
from .config import CORS_ORIGINS, LLM_MODEL, LLM_PROVIDER, PIPELINE_URL
from .graph import build_graph

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("agent")

app = FastAPI(title="Tech Summit data agent")
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])
_graph = None


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]


async def get_graph():
    global _graph
    if _graph is None:
        tools = await mcp_tools.get_tools()
        _graph = build_graph([t for name, t in tools.items() if name not in mcp_tools.ONBOARDING_TOOLS])
    return _graph


@app.get("/api/health")
async def health():
    return {"status": "ok", "llm_provider": LLM_PROVIDER, "model": LLM_MODEL if LLM_PROVIDER != "none" else None}


@app.post("/api/chat")
async def chat(req: ChatRequest):
    if not req.messages or req.messages[-1].role != "user":
        raise HTTPException(400, "The last message must come from the user.")
    history = [HumanMessage(m.content) if m.role == "user" else AIMessage(m.content) for m in req.messages[-20:]]
    try:
        graph = await get_graph()
        result = await graph.ainvoke({"messages": history}, {"recursion_limit": 16})
    except Exception as exc:
        log.exception("Agent failed")
        raise HTTPException(502, f"Agent error: {exc}") from exc

    new = result["messages"][len(history):]
    results = {m.tool_call_id: m.content for m in new if isinstance(m, ToolMessage)}
    steps = [
        {"tool": tc["name"], "args": tc["args"], "result": _preview(results.get(tc["id"]))}
        for m in new if isinstance(m, AIMessage) for tc in m.tool_calls
    ]
    reply = new[-1].content if new else ""
    if isinstance(reply, list):
        reply = "".join(b.get("text", "") for b in reply if isinstance(b, dict))
    return {"reply": reply, "steps": steps, "mode": "llm" if LLM_PROVIDER != "none" else "demo"}


def _preview(content, limit: int = 1500) -> str:
    text = json.dumps(mcp_tools.parse_result(content), default=str) if content is not None else ""
    return text if len(text) <= limit else text[:limit] + "…"


async def _tool(name: str, **args):
    try:
        res = await mcp_tools.call(name, **args)
    except Exception as exc:
        raise HTTPException(502, f"MCP error: {exc}") from exc
    if isinstance(res, dict) and "error" in res:
        raise HTTPException(404, res["error"])
    return res


@app.get("/api/lineage")
async def lineage(dataset: str = ""):
    return await _tool("get_lineage", dataset=dataset)


@app.get("/api/lineage/column")
async def column_lineage(dataset: str, column: str):
    return await _tool("get_column_lineage", dataset=dataset, column=column)


@app.get("/api/quality")
async def quality():
    return await _tool("get_data_quality")


@app.get("/api/dictionary")
async def dictionary(dataset: str = "", search: str = ""):
    return await _tool("get_data_dictionary", dataset=dataset, search=search)


class ImpactRequest(BaseModel):
    changes: list[dict]


@app.post("/api/impact")
async def impact(req: ImpactRequest):
    try:
        res = await mcp_tools.call("analyze_change_impact", changes=req.changes)
    except Exception as exc:
        raise HTTPException(502, f"MCP error: {exc}") from exc
    if isinstance(res, dict) and "error" in res:
        raise HTTPException(400, res["error"])
    return res


class SourceRequest(BaseModel):
    kind: str
    name: str
    connection: str = ""
    filename: str = ""
    content: str = ""
    url: str = ""
    table: str = ""
    query: str = ""
    doc_page: str = ""
    doc_schema: int = 0


class LinksRequest(BaseModel):
    name: str
    links: list[dict] = []
    feed: str = ""


async def _checked(tool: str, /, **args):
    try:
        res = await mcp_tools.call(tool, **args)
    except Exception as exc:
        raise HTTPException(502, f"MCP error: {exc}") from exc
    if isinstance(res, dict) and "error" in res:
        raise HTTPException(400, res["error"])
    return res


async def _doc(page_id: str, index: int) -> tuple[dict, list[dict]]:
    try:
        page = await confluence.page(page_id)
    except confluence.ConfluenceError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not page["schemas"]:
        raise HTTPException(400, f"No schema table (Column + Type/Description) found on '{page['title']}'.")
    if not 0 <= index < len(page["schemas"]):
        raise HTTPException(400, f"'{page['title']}' has {len(page['schemas'])} schema table(s).")
    return {"title": page["title"], "url": page["url"]}, page["schemas"][index]


@app.post("/api/sources")
async def stage_source(req: SourceRequest):
    doc, docs = await _doc(req.doc_page, req.doc_schema) if req.doc_page else (None, None)
    if req.kind == "confluence":
        if not doc:
            raise HTTPException(400, "Pick a Confluence page.")
        return await _checked("stage_source", kind="schema", name=req.name, filename=f"{req.name}.confluence",
                              content=json.dumps([{"name": d["name"], "type": d["type"]} for d in docs]),
                              docs=docs, doc=doc)
    extra = {"docs": docs, "doc": doc} if doc else {}
    if req.kind != "connection":
        return await _checked("stage_source", **req.model_dump(exclude={"connection", "doc_page", "doc_schema"}), **extra)
    sample = await _pipeline("POST", f"/connections/{req.connection}/sample",
                             json={"name": req.name, "table": req.table, "query": req.query})
    return await _checked("stage_source", kind="records", name=req.name, filename=sample["label"],
                          content=json.dumps(sample["rows"], default=str), **extra)


async def _confluence(call):
    try:
        return await call
    except confluence.ConfluenceError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/confluence/status")
async def confluence_status():
    return confluence.status()


@app.get("/api/confluence/search")
async def confluence_search(q: str = "", space: str = "", label: str = ""):
    return await _confluence(confluence.search(q, space, label))


@app.get("/api/confluence/pages/{page_id}")
async def confluence_page(page_id: str):
    return await _confluence(confluence.page(page_id))


class ConnectionRequest(BaseModel):
    name: str
    url: str
    description: str = ""


@app.get("/api/connections")
async def connections():
    return await _pipeline("GET", "/connections")


@app.post("/api/connections")
async def save_connection(req: ConnectionRequest):
    return await _pipeline("POST", "/connections", json=req.model_dump())


@app.delete("/api/connections/{name}")
async def delete_connection(name: str):
    return await _pipeline("DELETE", f"/connections/{name}")


@app.get("/api/connections/{name}/schema")
async def connection_schema(name: str):
    return await _pipeline("GET", f"/connections/{name}/schema")


@app.post("/api/sources/suggest")
async def suggest_links(req: LinksRequest):
    suggestion = await _checked("suggest_source_links", name=req.name)
    profile = await _checked("get_staged_source", name=req.name)
    links, mode = await linker.review(profile, suggestion)
    return {"links": links, "mode": mode, "targets": suggestion["targets"], "docs": suggestion.get("docs") or {}}


@app.post("/api/sources/validate")
async def validate_links(req: LinksRequest):
    return await _checked("validate_source_links", name=req.name, links=req.links, feed=req.feed)


class AcceptRequest(SourceRequest):
    links: list[dict]
    feed: str = ""
    plan_id: str = ""


class PublishRequest(LinksRequest):
    space: str = ""


async def _plan(name: str, links: list[dict], feed: str) -> tuple[dict, dict]:
    check = await _checked("validate_source_links", name=name, links=links, feed=feed)
    staged = await _checked("get_staged_source", name=name)
    try:
        contracts = (await mcp_tools.call("get_contracts")).get("datasets", [])
    except Exception:  # noqa: BLE001 - no contracts yet only means every feed is new
        contracts = []
    return plan.build(staged, links, check, contracts), staged


@app.post("/api/sources/plan")
async def implementation_plan(req: LinksRequest):
    """What Accept & implement would build: layers, joins, quality rules, contract and impact."""
    p, _ = await _plan(req.name, req.links, req.feed)
    return {**p, "markdown": plan.to_markdown(p)}


@app.post("/api/sources/plan/publish")
async def publish_plan(req: PublishRequest):
    p, _ = await _plan(req.name, req.links, req.feed)
    page = await _confluence(confluence.publish(f"{p['title']} ({p['id']})", plan.to_storage(p), req.space))
    return {"plan_id": p["id"], "page": page}


async def _pipeline(method: str, path: str, **kwargs):
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            res = await client.request(method, f"{PIPELINE_URL}{path}", **kwargs)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Pipeline API unreachable: {exc}") from exc
    body = res.json()
    if res.status_code >= 400:
        raise HTTPException(400 if res.status_code < 500 else 502, body.get("detail", res.text))
    return body


@app.post("/api/sources/accept")
async def accept_source(req: AcceptRequest):
    """Rebuild the plan from the confirmed links; implement only the approved, implementable plan."""
    p, staged = await _plan(req.name, req.links, req.feed)
    if p["blockers"]:
        raise HTTPException(400, p["blockers"][0])
    if req.plan_id != p["id"]:
        raise HTTPException(409, "The links changed since the plan was approved: review and approve the new plan.")
    spec = {**req.model_dump(exclude={"content", "doc_page", "doc_schema", "plan_id"}), "feed": p["layers"][-1]["dataset"],
            "plan_id": p["id"]}
    if req.kind == "connection":
        spec["label"] = staged["dataset"].removeprefix("source.")
    return await _pipeline("POST", "/sources", json={"spec": spec, "content": req.content})


@app.get("/api/sources/registered")
async def registered_sources():
    return await _pipeline("GET", "/sources")


@app.delete("/api/sources/registered/{name}")
async def remove_source(name: str):
    return await _pipeline("DELETE", f"/sources/{name}")


@app.get("/api/contracts")
async def contracts():
    return await _tool("get_contracts")


@app.get("/api/tables")
async def tables():
    return await _tool("list_tables")


@app.get("/api/tables/{table}")
async def table(table: str):
    return await _tool("describe_table", table=table)
