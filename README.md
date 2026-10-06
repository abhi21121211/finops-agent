# FinOps Agent

An autonomous accounts-payable agent for Indian small businesses and CA firms: it reads
vendor invoices, extracts and validates them, reconciles them against purchase orders and
bank statements, and asks a human only when it is unsure.

> Status: **M2, Validation and human review.** Upload an invoice; the agent extracts it,
> checks it with deterministic rules, re-reads the document when a check suggests a misread,
> and either auto-approves it or pauses for a human. Paused invoices survive restarts.
> See the [project spec](docs/FinOps_Agent_Project_Spec.md) for the roadmap.

## Architecture

```
Next.js ──REST/SSE──▶ FastAPI ──enqueue──▶ Redis/arq ──▶ worker ──publish──▶ Redis pub/sub
                         │                                │                     │
                         ▼                                ▼                     ▼
                     Postgres ◀── checkpoints + results ── LangGraph       SSE to browser
                                                          │
                                        LLMRouter ──▶ LiteLLM proxy ──▶ free providers

LangGraph, one thread per invoice (checkpointed in Postgres):

  intake → extract → validate ⇄ extract   (fixable issue, < 3 attempts, re-read changed something)
                            → route → post                  (auto_approve)
                                    → human_review → post   (interrupt; approve / edit / reject)
```

- **Validation is plain code** (`backend/app/agents/tools/validation.py`): GSTIN pattern and
  mod-36 check character, line maths, subtotal + tax = total, tax vs line GST rates
  (₹1 tolerance), CGST+SGST vs IGST and place of supply, dates, duplicates, vendor master.
- **Self-correction:** issues a misread could explain go back to `extract` as feedback. If the
  re-read returns identical values, the document really says that, so it stops early.
- **Routing is plain code** (`nodes/route.py`): any unresolved validation error, low field
  confidence (tenant threshold, default 85%), missing GSTIN or a total above the tenant's
  auto-approve limit (default ₹50,000) sends the invoice to a human. Every reason is shown.
- **Human review** is a LangGraph `interrupt()`. `POST /invoices/{id}/review` records the
  decision and enqueues a resume; the run continues from its Postgres checkpoint, on any
  worker, after any restart. Edits are re-validated and stored.
- **Live timeline:** the worker publishes each step to Redis; `GET /invoices/{id}/events`
  streams it as Server-Sent Events.
- **Every LLM call** goes through `backend/app/llm/router.py`, which records the model that
  actually answered, tokens, latency, real cost (free tier: $0) and list-price-equivalent cost.
- **Transient provider failures** (429/5xx) are retried with backoff, resuming from the
  last checkpoint rather than starting over.

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

Synthetic sample invoices (fictional companies and GSTINs that match the seeded vendor
master, with ground-truth JSON and the expected route):

```bash
make samples        # writes samples/*.pdf|png + *.expected.json
```

| Sample | Expected | Why |
|---|---|---|
| 01 intrastate | auto-approve | clean, known vendor, under limit |
| 02 interstate | review | total above ₹50,000 |
| 03 scanned | auto-approve | tilted, blurred scan of a clean invoice |
| 04 bad tax | review | vendor misprinted CGST; maths doesn't add up |
| 05 unknown vendor | review | GSTIN not in the vendor master |

## Tests

```bash
make test   # pytest: validators, graph routing, restart durability, review API, ...
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
