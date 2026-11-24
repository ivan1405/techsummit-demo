"""AI enrichment used by the silver layer: review sentiment + topic classification.

Uses an LLM when LLM_PROVIDER is configured, otherwise a deterministic keyword fallback.
"""

import json
import logging
import os

log = logging.getLogger(__name__)

SENTIMENTS = ("positive", "neutral", "negative")
TOPICS = ("delivery", "quality", "price", "support", "other")

_KEYWORDS = {
    "delivery": ("deliver", "arrived", "shipping", "package", "late", "delayed", "early"),
    "support": ("support", "customer service", "emails", "answered"),
    "price": ("price", "expensive", "value", "cheap"),
    "quality": ("quality", "broke", "stopped working", "works", "sound", "build"),
}
_POSITIVE = ("love", "great", "excellent", "amazing", "perfect", "superb", "happy", "helpful")
_NEGATIVE = ("terrible", "awful", "poor", "broke", "late", "damaged", "nightmare", "never", "expensive",
             "stopped")

PROMPT = """You classify product reviews for an electronics shop.
For each review return an object with:
- "sentiment": one of {sentiments}
- "topic": the main subject, one of {topics}
Respond with ONLY a JSON array, same order and length as the input.

Reviews:
{reviews}"""


def _heuristic(text: str) -> dict:
    t = text.lower()
    pos = sum(w in t for w in _POSITIVE)
    neg = sum(w in t for w in _NEGATIVE)
    sentiment = "positive" if pos > neg else "negative" if neg > pos else "neutral"
    topic = next((k for k, words in _KEYWORDS.items() if any(w in t for w in words)), "other")
    return {"sentiment": sentiment, "topic": topic, "method": "heuristic"}


def get_chat_model(provider: str, model: str):
    # Unset = provider default. Newer models (e.g. claude-sonnet-5-5, gpt-5) reject anything else.
    temperature = float(t) if (t := os.getenv("LLM_TEMPERATURE")) else None
    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=model, temperature=temperature)
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=model, temperature=temperature)
    return None


def _llm_batch(llm, texts: list[str]) -> list[dict]:
    prompt = PROMPT.format(
        sentiments=", ".join(SENTIMENTS),
        topics=", ".join(TOPICS),
        reviews=json.dumps(texts, ensure_ascii=False),
    )
    content = llm.invoke(prompt).content
    if isinstance(content, list):
        content = "".join(c.get("text", "") if isinstance(c, dict) else str(c) for c in content)
    content = content.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
    items = json.loads(content)
    if len(items) != len(texts):
        raise ValueError("LLM returned a different number of items")
    out = []
    for item, text in zip(items, texts):
        if item.get("sentiment") in SENTIMENTS and item.get("topic") in TOPICS:
            out.append({"sentiment": item["sentiment"], "topic": item["topic"], "method": "llm"})
        else:
            out.append(_heuristic(text))
    return out


def classify_reviews(texts: list[str], provider: str = "none", model: str = "", batch_size: int = 25) -> list[dict]:
    llm = get_chat_model(provider, model)
    if llm is None:
        return [_heuristic(t) for t in texts]
    unique = list(dict.fromkeys(texts))
    cache: dict[str, dict] = {}
    for i in range(0, len(unique), batch_size):
        batch = unique[i : i + batch_size]
        try:
            results = _llm_batch(llm, batch)
        except Exception as exc:  # noqa: BLE001 - fall back on any LLM failure
            log.warning("LLM enrichment failed (%s); using heuristic for batch", exc)
            results = [_heuristic(t) for t in batch]
        cache.update(zip(batch, results))
    return [cache[t] for t in texts]
