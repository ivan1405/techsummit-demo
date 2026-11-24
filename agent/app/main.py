"""FastAPI chat API in front of the LangGraph agent."""

import json
import logging

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import BaseModel

from . import mcp_tools
from .config import CORS_ORIGINS, LLM_MODEL, LLM_PROVIDER
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
        _graph = build_graph(list((await mcp_tools.get_tools()).values()))
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


@app.get("/api/tables")
async def tables():
    return await _tool("list_tables")


@app.get("/api/tables/{table}")
async def table(table: str):
    return await _tool("describe_table", table=table)
