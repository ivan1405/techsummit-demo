"""MCP client: loads the data lake tools exposed by the MCP server."""

import asyncio
import contextvars
import json
import logging

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from .config import MCP_URL

log = logging.getLogger(__name__)

_client = MultiServerMCPClient({"datalake": {"url": MCP_URL, "transport": "streamable_http"}})
_tools: dict[str, BaseTool] = {}
_lock = asyncio.Lock()
# Source onboarding tools are driven by the UI, not offered to the chat agent.
ONBOARDING_TOOLS = {"stage_source", "get_staged_source", "suggest_source_links", "validate_source_links"}
calls: contextvars.ContextVar[list | None] = contextvars.ContextVar("mcp_calls", default=None)


async def get_tools(retries: int = 10, delay: float = 2.0) -> dict[str, BaseTool]:
    async with _lock:
        if _tools:
            return _tools
        for attempt in range(1, retries + 1):
            try:
                _tools.update({t.name: t for t in await _client.get_tools()})
                log.info("Loaded MCP tools: %s", sorted(_tools))
                return _tools
            except Exception as exc:  # noqa: BLE001 - MCP server may still be starting
                log.warning("MCP server not ready (attempt %d/%d): %s", attempt, retries, exc)
                await asyncio.sleep(delay)
        raise RuntimeError(f"Could not reach MCP server at {MCP_URL}")


def parse_result(result) -> dict | list | str:
    """Turn an MCP tool result (text content blocks) back into JSON."""
    if isinstance(result, list):
        result = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in result)
    if isinstance(result, tuple):
        result = result[0]
    try:
        return json.loads(result)
    except (TypeError, json.JSONDecodeError):
        return result


async def call(name: str, **args) -> dict | list | str:
    """Call an MCP tool directly; recorded in `calls` when a recorder list is set."""
    tools = await get_tools()
    result = parse_result(await tools[name].ainvoke(args))
    recorder = calls.get()
    if recorder is not None:
        recorder.append((name, args, result))
    return result
