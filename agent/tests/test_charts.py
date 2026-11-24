import json

from langchain_core.messages import ToolMessage

from app import charts


def test_render_chart_validates():
    ok = json.loads(charts.render_chart.invoke({"kind": "pie", "title": "t", "labels": ["a", "b"], "values": [1, 2]}))
    assert ok["chart"]["values"] == [1.0, 2.0]
    bad = json.loads(charts.render_chart.invoke({"kind": "bar", "title": "t", "labels": ["a"], "values": [1, 2]}))
    assert "same length" in bad["error"]
    neg = json.loads(charts.render_chart.invoke({"kind": "pie", "title": "t", "labels": ["a"], "values": [-1]}))
    assert "error" in neg


def test_from_messages_keeps_only_charts():
    spec = {"kind": "bar", "title": "t", "labels": ["a"], "values": [1.0], "value_label": ""}
    msgs = [ToolMessage(json.dumps({"chart": spec}), tool_call_id="1", name="render_chart"),
            ToolMessage(json.dumps({"error": "x"}), tool_call_id="2", name="render_chart"),
            ToolMessage(json.dumps({"chart": spec}), tool_call_id="3", name="query")]
    assert charts.from_messages(msgs) == [spec]
