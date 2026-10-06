# FinOps Agent

An autonomous accounts-payable agent for Indian small businesses and CA firms: it reads
vendor invoices, extracts and validates them, reconciles them against purchase orders and
bank statements, and asks a human only when it is unsure.

> Status: **M3, Three-way reconciliation.** Upload an invoice; the agent extracts it,
> checks it with deterministic rules, matches it to its purchase order and to bank payments
> (part-payments, combined transfers, TDS), and either auto-approves it or pauses for a
> human. Paused invoices survive restarts.
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
                            → reconcile → route → post                (auto_approve)
                                                → human_review → post (interrupt; approve / edit / reject)
```

- **Validation is plain code** (`backend/app/agents/tools/validation.py`): GSTIN pattern and
  mod-36 check character, line maths, subtotal + tax = total, tax vs line GST rates
  (₹1 tolerance), CGST+SGST vs IGST and place of supply, dates, duplicates, vendor master.
- **Self-correction:** issues a misread could explain go back to `extract` as feedback. If the
  re-read returns identical values, the document really says that, so it stops early.
- **Routing is plain code** (`nodes/route.py`): any unresolved validation error, low field
  confidence (tenant threshold, default 85%), missing GSTIN or a total above the tenant's
  auto-approve limit (default ₹50,000) sends the invoice to a human. Every reason is shown.
- **Reconciliation** (`backend/app/reconcile/`, plain code): the PO is found by number, or by
  vendor and amount. Lines are matched by description, quantity (against what is *left* on
  the PO, which catches over-billing across invoices) and price (2% tolerance). An LLM is
  asked only whether two differently worded lines are the same item. Bank payments are
  allocated tenant-wide from CSV statements: invoice references, exact amounts, TDS
  deductions (0.1–10% of taxable value), combined transfers and part-payments. Amount alone
  never counts as a match. Only PO problems block approval ([ADR 0002](docs/decisions/0002-reconciliation-routing.md)).
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

| Sample | Route | Reconciliation (after the bank statement) |
|---|---|---|
| 01 intrastate | auto-approve | matched: PO-2026-0412; paid in a combined transfer with 04 |
| 02 interstate | review (over ₹50,000, no PO) | no_po; paid in two parts, ₹880 shortfall = 2% TDS |
| 03 scanned | auto-approve | matched: 500 of 1000 boxes on PO-2026-0398; paid net of 2% TDS |
| 04 bad tax | review (misprinted CGST) | matched: PO-2026-0431; combined transfer with 01 |
| 05 unknown vendor | review (not in vendor master) | no_po; unpaid |
| 06 PO price | review | mismatch: toner billed 13.9% above PO-2026-0450 |

`samples/bank-statement-sep-2026.csv` (HDFC layout) pays them, plus rent, salary and an
incoming receipt that must be ignored. Upload it on the Reconciliation page.

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
- [ADR 0002: What reconciliation blocks, and how payments are matched](docs/decisions/0002-reconciliation-routing.md)
