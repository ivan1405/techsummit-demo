import os

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "none").lower()
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
# Unset = provider default. Newer models (e.g. claude-sonnet-5-5, gpt-5) reject anything else.
LLM_TEMPERATURE = float(t) if (t := os.getenv("LLM_TEMPERATURE")) else None
MCP_URL = os.getenv("MCP_URL", "http://localhost:8000/mcp")
# Shared with the MCP server: without it every caller gets the reader role (PII masked).
MCP_ANALYST_TOKEN = os.getenv("MCP_ANALYST_TOKEN", "")
PIPELINE_URL = os.getenv("PIPELINE_URL", "http://localhost:8090").rstrip("/")
CORS_ORIGINS = [o for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if o]
# Confluence for documented source schemas. Without an API token the bundled mock is assumed (no auth).
CONFLUENCE_URL = os.getenv("CONFLUENCE_URL", "").rstrip("/").removesuffix("/wiki")
CONFLUENCE_EMAIL = os.getenv("CONFLUENCE_EMAIL", "")
CONFLUENCE_API_TOKEN = os.getenv("CONFLUENCE_API_TOKEN", "")
CONFLUENCE_SPACE = os.getenv("CONFLUENCE_SPACE", "DATA")
