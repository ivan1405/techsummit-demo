import os
from dataclasses import dataclass, field


def _env(name: str, default: str = ""):
    return field(default_factory=lambda: os.getenv(name, default))


@dataclass(frozen=True)
class Settings:
    s3_endpoint: str = _env("S3_ENDPOINT", "http://localhost:8333")
    s3_access_key: str = _env("S3_ACCESS_KEY", "lakeadmin")
    s3_secret_key: str = _env("S3_SECRET_KEY", "lakeadmin-secret")
    sources_dir: str = _env("SOURCES_DIR", "data/sources")
    bronze_bucket: str = _env("BRONZE_BUCKET", "bronze")
    silver_bucket: str = _env("SILVER_BUCKET", "silver")
    gold_bucket: str = _env("GOLD_BUCKET", "gold")
    lineage_bucket: str = _env("LINEAGE_BUCKET", "lineage")
    openlineage_url: str = _env("OPENLINEAGE_URL", "")
    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "none").lower())
    llm_model: str = _env("LLM_MODEL", "gpt-4o-mini")
