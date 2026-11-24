import asyncio

from langchain_core.messages import AIMessage

from app import main


class AuthError(Exception):
    status_code = 401


class Broken:
    async def ainvoke(self, *args, **kwargs):
        raise AuthError("Error code: 401 - invalid x-api-key, request req_secret")


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
    assert res["mode"] == "demo" and "HTTP 401" in res["reply"] and "Revenue by country" in res["reply"]
    assert "req_secret" not in res["reply"]


class Hanging:
    async def ainvoke(self, *args, **kwargs):
        await asyncio.sleep(10)


def test_chat_falls_back_when_the_llm_hangs(monkeypatch):
    async def hanging_graph():
        return Hanging()

    monkeypatch.setattr(main, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(main, "LLM_DEADLINE_S", 0.1)
    monkeypatch.setattr(main, "get_graph", hanging_graph)
    monkeypatch.setattr(main, "get_demo_graph", lambda: Demo())
    res = asyncio.run(main.chat(main.ChatRequest(messages=[{"role": "user", "content": "Revenue by country"}])))
    assert res["mode"] == "demo" and "no reply within 0 s" in res["reply"]
