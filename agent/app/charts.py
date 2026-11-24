"""`render_chart`: a chat tool that turns aggregated figures into a chart the UI draws under the answer."""

import json
from typing import Literal

from langchain_core.messages import ToolMessage
from langchain_core.tools import tool

KINDS = ("pie", "donut", "bar", "line")
MAX_POINTS = 24


def chart_spec(kind: str, title: str, labels: list, values: list, value_label: str = "") -> dict:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    if not labels or len(labels) != len(values):
        raise ValueError("labels and values must be non-empty and the same length")
    if len(labels) > MAX_POINTS:
        raise ValueError(f"at most {MAX_POINTS} points: aggregate further or keep the top N")
    nums = [float(v or 0) for v in values]
    if kind in ("pie", "donut") and any(v < 0 for v in nums):
        raise ValueError("pie and donut values must be >= 0")
    return {"kind": kind, "title": title, "labels": [str(label) for label in labels], "values": nums,
            "value_label": value_label}


@tool
def render_chart(kind: Literal["pie", "donut", "bar", "line"], title: str, labels: list[str], values: list[float],
                 value_label: str = "") -> str:
    """Draw a chart under your answer in the chat UI (also downloadable as PNG).

    Use it whenever the user asks for a chart, graph, visual or image insight. Query the figures first, then pass
    one label and one number per point (max 24). pie/donut: shares of a total; bar: rankings and comparisons;
    line: trends over time (labels in time order). value_label names the measure, e.g. "revenue (EUR)".
    """
    try:
        return json.dumps({"chart": chart_spec(kind, title, labels, values, value_label)})
    except (TypeError, ValueError) as exc:
        return json.dumps({"error": str(exc)})


def from_messages(messages: list) -> list[dict]:
    """Chart specs produced by render_chart calls in a graph result."""
    charts = []
    for m in messages:
        if not (isinstance(m, ToolMessage) and m.name == "render_chart"):
            continue
        try:
            spec = json.loads(m.content).get("chart")
        except (TypeError, ValueError, AttributeError):
            continue
        if spec:
            charts.append(spec)
    return charts
