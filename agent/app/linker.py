"""LLM review of heuristic source-to-lake column links; falls back to the heuristics in demo mode."""

import json
import logging
import re

from langchain_core.messages import HumanMessage, SystemMessage

from .graph import get_llm

log = logging.getLogger(__name__)
RELATIONS = {"join_key", "maps_to", "new"}

PROMPT = """You are a data engineer onboarding a new data source into a medallion lakehouse.
For each column of the new source decide how it relates to the existing silver/gold columns:
- join_key: the column identifies rows of an existing dataset (values match its key), so the source joins there.
- maps_to: same business meaning as an existing column (e.g. country, email) but not used to join.
- new: a new attribute with no existing equivalent (target null).
You get the source profile, heuristic suggestions (name similarity + real value overlap) and the
catalog of existing columns. Prefer silver datasets and unique keys for joins. Only use targets from the catalog.
Reply with ONLY a JSON array: [{"source_column", "relation", "target", "confidence" (0-1), "reason"}],
one entry per source column, reason in one short sentence."""


def _parse(text: str) -> list:
    match = re.search(r"\[.*\]", text, re.S)
    return json.loads(match.group(0)) if match else []


def merge(heuristic: list[dict], proposed: list, allowed: set[str]) -> list[dict]:
    """Keep the LLM's valid decisions; anything malformed falls back to the heuristic link."""
    by_col = {p.get("source_column"): p for p in proposed if isinstance(p, dict)}
    out = []
    for h in heuristic:
        p = by_col.get(h["source_column"])
        target = p and (p.get("target") or None)
        valid = p and p.get("relation") in RELATIONS and (
            (p["relation"] == "new" and target is None) or (p["relation"] != "new" and target in allowed))
        if not valid:
            out.append({**h, "origin": "heuristic"})
            continue
        try:
            confidence = round(min(max(float(p.get("confidence", 0.5)), 0.0), 1.0), 2)
        except (TypeError, ValueError):
            confidence = 0.5
        out.append({**h, "relation": p["relation"], "target": target, "confidence": confidence,
                    "reason": str(p.get("reason") or h["reason"])[:300], "origin": "ai"})
    return out


async def review(source: dict, suggestion: dict) -> tuple[list[dict], str]:
    heuristic = suggestion["links"]
    llm = get_llm()
    if llm is None:
        return [{**h, "origin": "heuristic"} for h in heuristic], "heuristic"
    payload = {"source": {k: source[k] for k in ("name", "dataset", "rows", "columns", "sample")},
               "heuristic": [{k: h[k] for k in ("source_column", "relation", "target", "reason", "candidates")}
                             for h in heuristic],
               "catalog": suggestion["targets"]}
    try:
        reply = await llm.ainvoke([SystemMessage(PROMPT), HumanMessage(json.dumps(payload, default=str))])
        text = reply.content if isinstance(reply.content, str) else "".join(
            b.get("text", "") for b in reply.content if isinstance(b, dict))
        links = merge(heuristic, _parse(text), {t["ref"] for t in suggestion["targets"]})
        return links, "llm"
    except Exception:  # noqa: BLE001 - never block onboarding on the LLM
        log.exception("LLM link review failed; using heuristics")
        return [{**h, "origin": "heuristic"} for h in heuristic], "heuristic"
