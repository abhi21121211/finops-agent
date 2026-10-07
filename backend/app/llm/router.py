"""LLMRouter: the single gateway for every LLM call (spec §12 rule 5).

Talks to the shared LiteLLM proxy over its OpenAI-compatible API. The proxy owns the
provider list and fallback (OpenRouter, Gemini, Groq, Mistral free tiers); this class
owns the app-side contract: pick a tier, ask for JSON, validate it with Pydantic, and
record model, tokens, latency and cost for every call.
"""

import base64
import json
import os
import time
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from functools import lru_cache
from typing import Any

from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from app.core.config import get_settings
from app.core.logging import log
from app.llm.direct import DirectClient
from app.llm.pricing import actual_cost_usd, list_price_usd


class Tier(StrEnum):
    vision = "vision"  # multimodal: invoice images
    powerful = "powerful"  # hard text reasoning
    normal = "normal"  # cheap classification / short text


@dataclass
class CallRecord:
    tier: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_usd: Decimal
    list_price_usd: Decimal
    attempts: int


@dataclass
class LLMResult[T: BaseModel]:
    output: T
    calls: list[CallRecord] = field(default_factory=list)

    @property
    def model(self) -> str:
        return self.calls[-1].model if self.calls else ""

    @property
    def cost_usd(self) -> Decimal:
        return sum((c.cost_usd for c in self.calls), Decimal("0"))

    @property
    def list_price_usd(self) -> Decimal:
        return sum((c.list_price_usd for c in self.calls), Decimal("0"))

    @property
    def latency_ms(self) -> int:
        return sum(c.latency_ms for c in self.calls)


class LLMOutputError(Exception):
    """The model never produced output that matched the schema."""

    def __init__(self, message: str, calls: list[CallRecord]) -> None:
        super().__init__(message)
        self.calls = calls  # still recorded for cost accounting


def image_part(data: bytes, mime: str = "image/jpeg") -> dict[str, Any]:
    b64 = base64.b64encode(data).decode()
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}


def text_part(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def _extract_json(text: str) -> str:
    """Models sometimes wrap JSON in ``` fences or add prose; keep the outer object."""
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if start != -1 and end > start else text


@lru_cache
def default_client():
    """One client per process, so connection pools (and, in direct mode, provider
    cooldowns) are shared across calls."""
    s = get_settings()
    if s.llm_mode == "direct":
        keys = {
            "GEMINI_API_KEY": s.gemini_api_key,
            "MISTRAL_API_KEY": s.mistral_api_key,
            "OPENROUTER_API_KEY": s.openrouter_api_key,
            "GROQ_API_KEY": s.groq_api_key,
        }
        env = {**os.environ, **{k: v for k, v in keys.items() if v}}
        return DirectClient(timeout_s=s.llm_timeout_s, env=env)
    return AsyncOpenAI(
        base_url=s.llm_router_url,
        api_key=s.llm_router_key or "no-key",
        timeout=s.llm_timeout_s,
        max_retries=1,
    )


class LLMRouter:
    def __init__(self, client=None) -> None:
        self._client = client or default_client()

    async def structured[T: BaseModel](
        self,
        *,
        tier: Tier,
        system: str,
        user_content: list[dict[str, Any]],
        schema: type[T],
        max_attempts: int = 2,
        temperature: float = 0.0,
    ) -> LLMResult[T]:
        """Call the router and parse the reply into `schema`.

        Uses JSON mode (supported by every provider in the pool) rather than strict
        json_schema (not all free providers support it). If the reply fails validation
        the error is sent back to the model for one more try.
        """
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]
        calls: list[CallRecord] = []
        last_error = ""

        for attempt in range(1, max_attempts + 1):
            started = time.perf_counter()
            raw = await self._client.chat.completions.with_raw_response.create(
                model=tier.value,
                messages=messages,
                temperature=temperature,
                response_format={"type": "json_object"},
            )
            resp = raw.parse()
            latency_ms = int((time.perf_counter() - started) * 1000)

            # The proxy names the deployment that actually answered (e.g. gemini/...).
            model = raw.headers.get("x-litellm-model-name") or resp.model or tier.value
            usage = resp.usage
            in_tok = usage.prompt_tokens if usage else 0
            out_tok = usage.completion_tokens if usage else 0
            header_cost = raw.headers.get("x-litellm-response-cost")
            list_price = (
                Decimal(header_cost)
                if header_cost not in (None, "", "0", "0.0", "None")
                else list_price_usd(model, in_tok, out_tok)
            )
            record = CallRecord(
                tier=tier.value,
                model=model,
                input_tokens=in_tok,
                output_tokens=out_tok,
                latency_ms=latency_ms,
                cost_usd=actual_cost_usd(model, in_tok, out_tok),
                list_price_usd=list_price,
                attempts=attempt,
            )
            calls.append(record)
            log.info("llm_call", **{k: str(v) for k, v in record.__dict__.items()})

            content = resp.choices[0].message.content or ""
            try:
                parsed = schema.model_validate(json.loads(_extract_json(content)))
                return LLMResult(output=parsed, calls=calls)
            except (json.JSONDecodeError, ValidationError) as e:
                last_error = str(e)[:2000]
                log.warning("llm_output_invalid", attempt=attempt, error=last_error[:300])
                messages += [
                    {"role": "assistant", "content": content},
                    {
                        "role": "user",
                        "content": "Your reply did not match the required JSON schema. "
                        f"Errors:\n{last_error}\nReply again with only the corrected JSON.",
                    },
                ]

        raise LLMOutputError(f"No valid output after {max_attempts} attempts: {last_error}", calls)
