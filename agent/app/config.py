import os

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "none").lower()
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
# Unset = provider default. Newer models (e.g. claude-sonnet-5-5, gpt-5) reject anything else.
LLM_TEMPERATURE = float(t) if (t := os.getenv("LLM_TEMPERATURE")) else None
MCP_URL = os.getenv("MCP_URL", "http://localhost:8000/mcp")
PIPELINE_URL = os.getenv("PIPELINE_URL", "http://localhost:8090").rstrip("/")
CORS_ORIGINS = [o for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if o]
