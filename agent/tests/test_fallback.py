import asyncio

from langchain_core.messages import AIMessage

from app import main


class Broken:
    async def ainvoke(self, *args, **kwargs):
        raise RuntimeError("Error code: 401 - invalid x-api-key")


class Demo:
    async def ainvoke(self, state, config):
        return {"messages": [*state["messages"], AIMessage(content="Revenue by country: ...")]}


def test_chat_falls_back_to_demo_mode_when_the_llm_fails(monkeypatch):
    async def broken_graph():
        return Broken()

    monkeypatch.setattr(main, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(main, "get_graph", broken_graph)
    monkeypatch.setattr(main, "get_demo_graph", lambda: Demo())
    res = asyncio.run(main.chat(main.ChatRequest(messages=[{"role": "user", "content": "Revenue by country"}])))
    assert res["mode"] == "demo" and "invalid x-api-key" in res["reply"] and "Revenue by country" in res["reply"]
