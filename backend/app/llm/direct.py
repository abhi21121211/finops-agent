"""Direct mode: call free LLM providers' OpenAI-compatible APIs from the app itself,
with failover, instead of going through a LiteLLM proxy.

Used where an extra 370 MB proxy process doesn't fit (Render's free 512 MB instance).
It mimics the slice of the OpenAI client the LLMRouter uses, and reports the model that
answered in the same `x-litellm-model-name` header, so nothing else changes.

Per tier, deployments are tried in order. A deployment that fails is cooled down and the
next one is tried; if every deployment fails, the last error is raised (the worker then
retries the job with backoff). Deployments whose API key is not set are skipped.
"""

import os
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import openai

from app.core.logging import log


@dataclass(frozen=True)
class Deployment:
    provider: str  # label used in the reported model name, e.g. "gemini"
    model: str
    base_url: str
    key_env: str


GEMINI = "https://generativelanguage.googleapis.com/v1beta/openai/"
MISTRAL = "https://api.mistral.ai/v1"
OPENROUTER = "https://openrouter.ai/api/v1"
GROQ = "https://api.groq.com/openai/v1"

# Free-tier model names change; update here (and in infra/litellm/config.yaml).
TIERS: dict[str, tuple[Deployment, ...]] = {
    "vision": (
        Deployment("gemini", "gemini-flash-lite-latest", GEMINI, "GEMINI_API_KEY"),
        Deployment("gemini", "gemini-flash-latest", GEMINI, "GEMINI_API_KEY"),
        Deployment("mistral", "mistral-medium-latest", MISTRAL, "MISTRAL_API_KEY"),
        Deployment("openrouter", "google/gemma-4-31b-it:free", OPENROUTER, "OPENROUTER_API_KEY"),
        Deployment(
            "openrouter", "google/gemma-4-26b-a4b-it:free", OPENROUTER, "OPENROUTER_API_KEY"
        ),
    ),
    "powerful": (
        Deployment("gemini", "gemini-flash-latest", GEMINI, "GEMINI_API_KEY"),
        Deployment("mistral", "mistral-medium-latest", MISTRAL, "MISTRAL_API_KEY"),
        Deployment(
            "openrouter", "nvidia/nemotron-3-super-120b-a12b:free", OPENROUTER, "OPENROUTER_API_KEY"
        ),
    ),
    "normal": (
        Deployment("groq", "openai/gpt-oss-20b", GROQ, "GROQ_API_KEY"),
        Deployment("gemini", "gemini-flash-lite-latest", GEMINI, "GEMINI_API_KEY"),
        Deployment("mistral", "mistral-small-latest", MISTRAL, "MISTRAL_API_KEY"),
    ),
}

SHORT_COOLDOWN_S = 60  # rate limit, overload
LONG_COOLDOWN_S = 600  # bad key, quota exhausted, model removed


class NoDeploymentError(Exception):
    """No configured, healthy deployment for the tier."""


class DirectClient:
    def __init__(
        self,
        timeout_s: float,
        tiers: dict[str, tuple[Deployment, ...]] = TIERS,
        env: dict[str, str] | None = None,
        client_factory=None,
    ) -> None:
        env = os.environ if env is None else env
        self._tiers = {
            tier: tuple(d for d in deps if env.get(d.key_env)) for tier, deps in tiers.items()
        }
        factory = client_factory or (
            lambda d: openai.AsyncOpenAI(
                base_url=d.base_url, api_key=env[d.key_env], timeout=timeout_s, max_retries=0
            )
        )
        self._clients = {d: factory(d) for deps in self._tiers.values() for d in deps}
        self._cool_until: dict[Deployment, float] = {}
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(with_raw_response=SimpleNamespace(create=self._create))
        )

    def configured(self, tier: str) -> list[str]:
        return [f"{d.provider}/{d.model}" for d in self._tiers.get(tier, ())]

    async def _create(self, *, model: str, **kwargs: Any):
        deployments = self._tiers.get(model, ())
        if not deployments:
            raise NoDeploymentError(f"no API key set for any '{model}' deployment")
        now = time.monotonic()
        # Healthy ones first; cooled-down ones as a last resort rather than failing outright.
        ordered = sorted(deployments, key=lambda d: self._cool_until.get(d, 0) > now)
        last_error: Exception | None = None
        for d in ordered:
            try:
                raw = await self._clients[d].chat.completions.with_raw_response.create(
                    model=d.model, **kwargs
                )
            except openai.APIError as e:
                last_error = e
                self._cool_until[d] = time.monotonic() + _cooldown(e)
                log.warning(
                    "llm_failover",
                    tier=model,
                    deployment=f"{d.provider}/{d.model}",
                    error=type(e).__name__,
                    status=getattr(e, "status_code", None),
                )
                continue
            return _Raw(raw, f"{d.provider}/{d.model}")
        assert last_error is not None
        raise last_error


def _cooldown(e: openai.APIError) -> int:
    status = getattr(e, "status_code", None)
    text = str(e).lower()
    if status in (401, 403, 404) or "quota" in text:
        return LONG_COOLDOWN_S
    return SHORT_COOLDOWN_S


class _Raw:
    """Wraps a raw response so the router sees which deployment answered."""

    def __init__(self, raw, model_name: str) -> None:
        self._raw = raw
        self.headers = {"x-litellm-model-name": model_name}

    def parse(self):
        return self._raw.parse()
