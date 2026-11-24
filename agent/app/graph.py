"""LangGraph agent that answers questions about the data lake through MCP tools."""

import json

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from . import demo, mcp_tools
from .config import LLM_MODEL, LLM_PROVIDER, LLM_TEMPERATURE

SYSTEM_PROMPT = """You are the Parser AI data assistant for the Tech Summit data platform.
You answer business questions using ONLY the data lake, accessed through your MCP tools.

Data platform:
- Medallion pipeline: sources -> bronze (raw) -> silver (filtered, cleaned, AI-enriched) -> gold (merged).
- Gold tables: sales, customer_360, product_performance, daily_revenue, review_insights.
- Lineage tables: lineage_runs, lineage_jobs, lineage_outputs, lineage_edges, lineage_columns, data_quality.

How to work:
1. If unsure about schemas, call list_tables / describe_table before writing SQL.
2. Use `query` with a single DuckDB SELECT. Prefer aggregated results; round money to 2 decimals.
3. For "where does X come from", "how is X calculated" or audit questions, use get_lineage /
   get_column_lineage and explain each hop with its transformation. Use get_data_quality for dropped rows.
   For "what changed" or run history questions use get_run_history / compare_runs.
   For a question about one column (e.g. "How is gold.sales.amount calculated?") call explain_column first.
4. Answer concisely in Markdown. Use tables for tabular results and finish with a short
   "Sources:" line naming the tables (and lineage) you used. Never invent numbers.
"""


def get_llm():
    if LLM_PROVIDER == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=LLM_MODEL, temperature=LLM_TEMPERATURE)
    if LLM_PROVIDER == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=LLM_MODEL, temperature=LLM_TEMPERATURE)
    return None


def build_graph(tools: list):
    llm = get_llm()
    graph = StateGraph(MessagesState)

    if llm is None:
        async def demo_agent(state: MessagesState):
            recorded: list = []
            token = mcp_tools.calls.set(recorded)
            try:
                reply = await demo.answer(state["messages"][-1].content)
            finally:
                mcp_tools.calls.reset(token)
            messages = []
            for i, (name, args, result) in enumerate(recorded):
                call_id = f"demo-{i}"
                messages.append(AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}]))
                messages.append(ToolMessage(content=json.dumps(result, default=str), tool_call_id=call_id, name=name))
            messages.append(AIMessage(content=reply))
            return {"messages": messages}

        graph.add_node("agent", demo_agent)
        graph.add_edge(START, "agent")
        graph.add_edge("agent", END)
        return graph.compile()

    model = llm.bind_tools(tools)

    async def agent(state: MessagesState):
        return {"messages": [await model.ainvoke([SystemMessage(SYSTEM_PROMPT), *state["messages"]])]}

    graph.add_node("agent", agent)
    graph.add_node("tools", ToolNode(tools))
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", tools_condition)
    graph.add_edge("tools", "agent")
    return graph.compile()
