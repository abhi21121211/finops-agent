from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# .env lives at the repo root, one level above backend/
_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore")

    env: str = "dev"
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://finops:finops@localhost:5433/finops"
    redis_url: str = "redis://localhost:6379/0"

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
    llm_router_key: str = ""
    llm_timeout_s: float = 120.0
    # Prompt version for extraction; a name in agents/prompts/ or a path to a .md file.
    # Lets evals compare prompts without code changes.
    extract_prompt: str = "extract_v3"

    jwt_secret: str = "dev-insecure-secret"
    jwt_algorithm: str = "HS256"
    jwt_ttl_minutes: int = 60 * 12

    cors_origins: list[str] = ["http://localhost:3000"]

    # Seconds between the worker's queue polls. Each poll is a few Redis commands, and
    # Upstash's free tier allows 500k commands a month, so production uses ~5.
    worker_poll_delay_s: float = 0.5

    max_upload_mb: int = 15
    max_pages: int = 5


@lru_cache
def get_settings() -> Settings:
    return Settings()
