# ADR 0001: Free-tier stack

- **Status:** Accepted
- **Date:** 2026-10-06

## Context

The spec (§3) names paid services: Amazon Bedrock and OpenAI for models, AWS S3, EC2,
Secrets Manager and CloudWatch. The project is a portfolio build with no budget, so it
should run on free tiers while keeping the same architecture and the option to switch back.

## Decision

| Spec | Used instead | Why it is safe to swap back |
|---|---|---|
| Bedrock / OpenAI via LangChain | A shared LiteLLM proxy with tiers `vision`, `powerful`, `normal`, each a pool of free deployments (Gemini, Mistral, OpenRouter, Groq) | `LLMRouter` speaks the OpenAI-compatible API; a paid provider is one more entry in the proxy config |
| LangChain chat models | The `openai` SDK pointed at the proxy | LangChain adds nothing here; response headers give the real model and cost |
| AWS S3 (MinIO locally) | SeaweedFS locally (MinIO no longer publishes free images); Supabase Storage in production | Any S3-compatible store; only endpoint and keys change |
| Strict JSON schema output | JSON mode + Pydantic validation + one correction round | Not every free provider supports `json_schema`; JSON mode works across the pool |
| EC2, Secrets Manager, CloudWatch | Render free web service, platform secrets (ADR 0004) | Docker Compose runs anywhere |

Cost tracking: free calls cost $0, so every call also records a **list-price equivalent**
(from LiteLLM's `x-litellm-response-cost`, or `llm/pricing.py`). Cost dashboards and resume
numbers use this column so they stay meaningful.

## Consequences

- Free tiers rate-limit in bursts (seen in development: Gemini 503 "high demand" and
  OpenRouter's 50-requests-per-day cap at the same moment). Mitigations: several
  deployments per tier, and worker-level retries with backoff before marking an invoice failed.
- Which model answers varies between runs, which adds noise to evals. The eval harness (M4)
  must record the model per case and can pin a single deployment when comparing prompts.
- The proxy is a separate process that must be running; in production it runs as a container
  next to the backend.
