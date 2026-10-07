# ADR 0004: Render instead of Hugging Face Spaces; LLM calls without a proxy in production

- **Status:** Accepted
- **Date:** 2026-10-07
- **Supersedes:** [ADR 0003](0003-free-deployment.md)

## Context

ADR 0003 put the API, worker and LiteLLM router in one free Hugging Face Docker Space.
Since July 2026, creating a Docker Space requires a paid plan (PRO, $9/month).

The free container hosts left in 2026:

- **Render:** 512 MB, sleeps when idle, no card.
- **Koyeb:** 512 MB, needs a $29 card hold.
- **Oracle Always Free:** a real VM, but it needs a credit card, which many Indian debit
  cards fail.

Render is the only one with no card at all.

We measured the existing container under load: 599 MB in total. LiteLLM used 370 MB, the
worker 169 MB and the API 144 MB.

## Decision

- **Deploy the API and the worker to a Render free web service** (`render.yaml`,
  `deploy/render/`). Render deploys after GitHub checks pass.
- **Add `LLM_MODE=direct`.** The `LLMRouter` then calls the providers' OpenAI-compatible
  endpoints (Gemini, Mistral, OpenRouter, Groq) itself, with ordered failover and
  cooldowns (`app/llm/direct.py`). It reports the model that answered exactly as the
  proxy did, so cost tracking and evals are unchanged. Local development and CI keep using
  the LiteLLM proxy (`LLM_MODE=proxy`).
- **The worker polls Redis every 5 seconds in production**, to stay inside Upstash's free
  500k commands a month.

## Consequences

- Measured under Render's limits (512 MB cap, half a CPU, three invoices at once): 284 MB
  peak, no out-of-memory kills.
- Two model lists to keep in step: `infra/litellm/config.yaml` (proxy) and `TIERS` in
  `app/llm/direct.py` (direct).
- Render sleeps after 15 idle minutes, and a cold start takes about a minute. Queued jobs
  run once the service wakes.
- Jobs can start up to 5 seconds later than with the default polling.
