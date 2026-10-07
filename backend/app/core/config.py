import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# .env lives at the repo root, one level above backend/. FINOPS_ENV_FILE layers another
# file on top (e.g. .env.cloud to run locally against the real cloud services).
_ROOT = Path(__file__).resolve().parents[3]
_ENV_FILES = tuple(p for p in (_ROOT / ".env", os.environ.get("FINOPS_ENV_FILE")) if p)


class Settings(BaseSettings):
    # hide_input_in_errors: a bad value (a URL with a password, a key) must never be
    # echoed into logs by a validation error.
    model_config = SettingsConfigDict(
        env_file=_ENV_FILES, extra="ignore", hide_input_in_errors=True
    )

    env: str = "dev"
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://finops:finops@localhost:5433/finops"
    redis_url: str = "redis://localhost:6379/0"

    @field_validator("redis_url", mode="before")
    @classmethod
    def _parse_redis_url(cls, v: Any) -> Any:
        """Accept the URL, or a pasted `redis-cli --tls -u redis://...` command (as Upstash
        shows it). Reject anything else by name, never echoing the value (it has a password)."""
        if not isinstance(v, str):
            return v
        v = v.strip().strip("\"'")
        found = re.search(r"rediss?://\S+", v)
        if found:
            url = found.group(0)
            if "--tls" in v and url.startswith("redis://"):
                url = "rediss://" + url[len("redis://") :]
            return url
        kind = "an https:// REST URL" if v.startswith("http") else "not a redis:// URL"
        raise ValueError(
            f"REDIS_URL is {kind}. Use the TCP URL: rediss://default:<password>@<host>:6379"
        )

    s3_endpoint: str = "http://localhost:9100"
    s3_public_endpoint: str = "http://localhost:9100"
    s3_access_key: str = "finops"
    s3_secret_key: str = "finops-secret"
    s3_bucket: str = "invoices"
    s3_region: str = "us-east-1"

    # "proxy": a LiteLLM router at llm_router_url (local dev, CI).
    # "direct": call free providers from the app with built-in failover (small hosts).
    llm_mode: Literal["proxy", "direct"] = "proxy"
    llm_router_url: str = "http://localhost:4000/v1"
    # Provider keys for direct mode (also read from the process environment).
    gemini_api_key: str = ""
    mistral_api_key: str = ""
    openrouter_api_key: str = ""
    groq_api_key: str = ""
    llm_router_key: str = ""
    llm_timeout_s: float = 120.0
    # Prompt version for extraction; a name in agents/prompts/ or a path to a .md file.
    # Lets evals compare prompts without code changes.
    extract_prompt: str = "extract_v3"

    jwt_secret: str = "dev-insecure-secret"
    jwt_algorithm: str = "HS256"
    jwt_ttl_minutes: int = 60 * 12

    # JSON list, a comma-separated list, or one URL. Empty means the default, so a
    # blank value in a hosting dashboard can't stop the app from starting.
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000"]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_origins(cls, v: Any) -> Any:
        if not isinstance(v, str):
            return v
        v = v.strip()
        if not v:
            return ["http://localhost:3000"]
        if v.startswith("["):
            return json.loads(v)
        return [o.strip().strip("\"'") for o in v.split(",") if o.strip()]

    # Seconds between the worker's queue polls. Each poll is a few Redis commands, and
    # Upstash's free tier allows 500k commands a month, so production uses ~5.
    worker_poll_delay_s: float = 0.5

    max_upload_mb: int = 15
    max_pages: int = 5


@lru_cache
def get_settings() -> Settings:
    return Settings()
