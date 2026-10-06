# FinOps Agent

An autonomous accounts-payable agent for Indian small businesses and CA firms: it reads
vendor invoices, extracts and validates them, reconciles them against purchase orders and
bank statements, and asks a human only when it is unsure.

> Status: **M1, Skeleton and extraction.** Upload an invoice (PDF, PNG, JPG) and see every
> field extracted with a per-field confidence score. See the
> [project spec](docs/FinOps_Agent_Project_Spec.md) for the roadmap.

## Architecture (M1)

```
Next.js ──REST──▶ FastAPI ──enqueue──▶ Redis/arq ──▶ worker
                     │                                 │  LangGraph: intake → extract
                     ▼                                 ▼
                 Postgres  ◀───── results ─────── S3-compatible storage
                                                       │
                                     LLMRouter ──▶ LiteLLM proxy ──▶ free providers
                                                   (Gemini, Mistral, OpenRouter, Groq)
```

- **Every LLM call** goes through `backend/app/llm/router.py`, which records the model that
  actually answered, tokens, latency, real cost (free tier: $0) and list-price-equivalent cost.
- **Extraction** sends page images *and* the PDF text layer to a `vision` tier, asks for
  JSON, validates it with Pydantic and gives the model one correction round if it fails.
- **Transient provider failures** (429/5xx) are retried by the worker with backoff; the
  invoice is only marked `failed` after four tries.
- **Prompts** live in `backend/app/agents/prompts/` as versioned files.

## Running locally

Prerequisites: Docker, [uv](https://docs.astral.sh/uv/), Node 22, and a running
[LiteLLM](https://docs.litellm.ai/) proxy on `localhost:4000` with a `vision` model group
(any multimodal models; free tiers work).

```bash
cp .env.example .env          # set LLM_ROUTER_KEY to your proxy's master key
docker compose up -d --build  # Postgres, Redis, object store, API, worker, web
open http://localhost:3000    # "Try the demo"
```

Or run the app processes on your machine with hot reload:

```bash
make infra          # Postgres, Redis, object store in Docker
make backend-dev    # API on :8000 (runs migrations + seed)
make worker-dev     # job worker
make frontend-dev   # Next.js on :3000
```

Synthetic sample invoices (fictional companies and GSTINs, with ground-truth JSON):

```bash
make samples        # writes samples/*.pdf|png + *.expected.json
```

## Tests

```bash
make test   # pytest: schema, router, document rendering, upload→worker→API flow
make lint
```

Tests use a separate `finops_test` database, in-memory storage and a fake LLM: they make
no network calls to model providers.

## Repository layout

```
backend/app/
  api/          routers (auth, invoices)
  agents/       graph.py, state.py, nodes/, tools/, prompts/
  core/         config, auth, logging, queue
  db/           models, session      (migrations in backend/alembic)
  llm/          router.py, pricing.py
  schemas/      Pydantic models
  storage/      S3-compatible storage
  worker.py     arq worker
frontend/       Next.js (App Router), Tailwind, shadcn/ui, TanStack Query
scripts/        sample invoice generator
docs/           spec, architecture decision records
```

## Decisions

- [ADR 0001: Free-tier stack](docs/decisions/0001-free-tier-stack.md)
