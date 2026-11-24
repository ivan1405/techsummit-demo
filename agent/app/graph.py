"""LangGraph agent that answers questions about the data lake through MCP tools."""

from langchain_core.messages import AIMessage, SystemMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from . import demo
from .config import LLM_MODEL, LLM_PROVIDER

SYSTEM_PROMPT = """You are the Parser AI data assistant for the Tech Summit data platform.
You answer business questions using ONLY the data lake, accessed through your MCP tools.

Data platform:
- Medallion pipeline: sources -> bronze (raw) -> silver (filtered, cleaned, AI-enriched) -> gold (merged).
- Gold tables: sales, customer_360, product_performance, daily_revenue, review_insights.
- Lineage tables: lineage_runs, lineage_jobs, lineage_edges, lineage_columns, data_quality.

How to work:
1. If unsure about schemas, call list_tables / describe_table before writing SQL.
2. Use `query` with a single DuckDB SELECT. Prefer aggregated results; round money to 2 decimals.
3. For "where does X come from", "how is X calculated" or audit questions, use get_lineage /
   get_column_lineage and explain each hop with its transformation. Use get_data_quality for dropped rows.
4. Answer concisely in Markdown. Use tables for tabular results and finish with a short
   "Sources:" line naming the tables (and lineage) you used. Never invent numbers.
"""


def get_llm():
    if LLM_PROVIDER == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=LLM_MODEL, temperature=0)
    if LLM_PROVIDER == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=LLM_MODEL, temperature=0)
    return None


def build_graph(tools: list):
    llm = get_llm()
    graph = StateGraph(MessagesState)

    if llm is None:
        async def demo_agent(state: MessagesState):
            return {"messages": [AIMessage(content=await demo.answer(state["messages"][-1].content))]}

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
