# FinOps Agent: Project Specification

**Owner:** Abhishek Dukare
**Version:** 1.0 (6 Oct 2026)
**Purpose of this document:** the single source of truth for building the project. It is written so a coding agent can work from it milestone by milestone.

---

## 1. What we are building

FinOps Agent is an autonomous accounts-payable system for small businesses and CA firms in India. It receives vendor invoices by email or upload, extracts and validates them, reconciles them against purchase orders and bank statements, flags fraud and anomalies, and asks a human only when it is unsure. The owner can ask spend questions in plain language.

**One-line pitch:** "An AI accounts team that a business can trust with money, with measured accuracy."

### Goals

1. Show production-grade agent engineering: multi-agent orchestration, human-in-the-loop, durable workflows, evaluation and guardrails.
2. Be usable by a real person in a live demo in under two minutes.
3. Produce real numbers (accuracy, cost per invoice, latency, auto-approval rate) for the resume.

### Non-goals

- Not an accounting package. No ledgers, no GST return filing, no payments.
- No real customer data. All demo and test data is synthetic.
- No mobile app.

### Users

| Role | What they do |
|---|---|
| Owner / Admin | Sets up the company, vendors and approval rules; asks spend questions |
| Accountant / Reviewer | Reviews low-confidence invoices; approves, edits or rejects |
| Vendor (external) | Sends invoices by email; receives automatic clarification emails |

---

## 2. Feature list

Priority: **P0** = core, must be live before applying to jobs. **P1** = add while interviewing. **P2** = stretch.

| # | Feature | Priority | Milestone |
|---|---|---|---|
| F1 | Upload invoice (PDF, PNG, JPG) and extract structured fields | P0 | M1 |
| F2 | Validator agent with self-correction loop (tax maths, GSTIN format, dates) | P0 | M2 |
| F3 | Per-field confidence scores and confidence-based routing (auto-approve or human review) | P0 | M2 |
| F4 | Human review screen: approve, edit, reject; workflow pauses and resumes | P0 | M2 |
| F5 | Three-way reconciliation: invoice, purchase order, bank statement | P0 | M3 |
| F6 | Evaluation harness on 100 synthetic invoices, with eval-gated CI | P0 | M4 |
| F7 | Deployed live with Docker, CI/CD, secrets management and logging | P0 | M4 |
| F8 | Anomaly and fraud detection (duplicates, price jumps, changed bank details) | P1 | M5 |
| F9 | Email ingestion and automatic vendor clarification emails | P1 | M5 |
| F10 | Model routing by difficulty, with a cost dashboard | P1 | M6 |
| F11 | RAG over vendor contracts, with citations | P1 | M6 |
| F12 | Text-to-SQL spend questions with charts | P1 | M7 |
| F13 | Multi-tenant isolation, audit trail, prompt-injection guardrails | P1 | M7 |
| F14 | MCP server exposing invoice data and actions | P2 | M8 |
| F15 | CLI for bulk upload (npm package) | P2 | M8 |
| F16 | Learning from corrections (few-shot memory per vendor) | P2 | M8 |

---

## 3. Architecture

```
                 ┌────────────────────────────────────────────┐
                 │            Next.js frontend                │
                 │ Dashboard · Review · Chat · Evals · Costs  │
                 └───────────────┬────────────────────────────┘
                                 │ REST + SSE
                 ┌───────────────▼────────────────────────────┐
  Email inbox ──▶│            FastAPI backend                 │◀── MCP server
  (IMAP poll)    │ Auth · Tenancy · API · SSE · Guardrails    │◀── CLI
                 └───────┬───────────────┬────────────────────┘
                         │               │
              ┌──────────▼─────┐   ┌─────▼──────────────────┐
              │ Worker         │   │ LangGraph workflow      │
              │ (job queue)    │──▶│ Intake → Extract →      │
              └────────────────┘   │ Validate ⇄ Extract →    │
                                   │ Reconcile → Anomaly →   │
                                   │ Route → [Human] → Post  │
                                   └─────┬──────────────────┘
                                         │
      ┌───────────────┬──────────────────┼──────────────┬──────────────┐
      ▼               ▼                  ▼              ▼              ▼
 PostgreSQL      pgvector           S3 storage     LLM router     LangSmith
 (data, RLS,     (contract          (invoice       (Bedrock,      (tracing,
 checkpoints)    embeddings)        files)         Gemini, etc.)  evals)
```

### Tech stack

| Layer | Choice | Notes |
|---|---|---|
| Agent orchestration | LangGraph (Python) | `PostgresSaver` checkpointer, `interrupt()` for human review |
| LLM access | LangChain chat models behind one `LLMRouter` class | Providers: Amazon Bedrock (Nova, Claude), Gemini, OpenAI. Must be swappable by config |
| Backend | FastAPI, Pydantic v2, SQLAlchemy 2 (async), Alembic | Python 3.12, `uv` for dependencies |
| Background jobs | `arq` with Redis | Simple and async-native. Workflow runs never block API requests |
| Database | PostgreSQL 16 with `pgvector` | Supabase for hosting; row-level security for tenancy |
| File storage | AWS S3 (MinIO locally) | Pre-signed URLs for upload and download |
| Auth | Supabase Auth (JWT) | Backend verifies the JWT and resolves `tenant_id` |
| Frontend | Next.js (App Router), TypeScript, Tailwind, shadcn/ui, TanStack Query, Recharts | |
| Streaming | Server-Sent Events | Live agent progress per invoice |
| Observability | LangSmith for traces, CloudWatch for logs, structured JSON logging | |
| Testing | pytest, Playwright | |
| CI/CD | GitHub Actions | Lint, tests, eval gate, build, deploy |
| Deployment | Frontend on Vercel; backend, worker and Redis on one small AWS EC2 instance with Docker Compose | Secrets in AWS Secrets Manager |
| MCP | Official Python MCP SDK (`FastMCP`) | |
| CLI | Node.js + TypeScript, published to npm | |

**Decision notes**

- `pgvector` is used instead of Pinecone to keep tenancy rules in one database. The vector store sits behind an interface so Pinecone can be swapped in.
- Extraction uses a multimodal LLM on page images. For digital PDFs, extracted text (via `pdfplumber`) is passed alongside the image to improve accuracy and lower cost.
- GitHub Actions is used instead of Jenkins because the repo is public and it needs no server.

---

## 4. The agent workflow

One LangGraph `StateGraph` per invoice. Each run is a thread keyed by `invoice_id`, checkpointed in Postgres.

### State

```python
class InvoiceState(TypedDict):
    invoice_id: str
    tenant_id: str
    file_keys: list[str]              # S3 keys of page images / PDF
    source: Literal["upload", "email", "cli"]
    extraction: ExtractedInvoice | None
    field_confidence: dict[str, float]
    validation_issues: list[ValidationIssue]
    extraction_attempts: int           # max 3
    match_result: MatchResult | None
    anomalies: list[Anomaly]
    route: Literal["auto_approve", "human_review", "vendor_query", "reject"] | None
    human_decision: HumanDecision | None
    model_used: str
    cost_usd: float
    events: list[WorkflowEvent]        # for audit trail and SSE
```

### Nodes

| Node | Type | What it does |
|---|---|---|
| `intake` | Code | Stores files, converts PDF to images, runs the injection scan, detects duplicates by file hash |
| `classify_difficulty` | LLM (cheap) | Labels the document easy or hard (scan quality, layout, handwriting). Picks the model |
| `extract` | LLM (multimodal, structured output) | Returns `ExtractedInvoice` plus per-field confidence. On retry, receives the validator's feedback |
| `validate` | Code + tools | Deterministic checks. Returns issues |
| `reconcile` | Code + LLM for fuzzy cases | Matches to purchase order and bank transactions |
| `detect_anomalies` | Code + LLM summary | Runs the anomaly rules |
| `route` | Code | Applies routing rules (below) |
| `human_review` | `interrupt()` | Pauses until a reviewer acts. Can wait for days |
| `vendor_query` | LLM + email tool | Drafts and sends a clarification email, then waits for a reply |
| `post` | Code | Marks approved, writes the audit entry, stores the correction as a learning example |

### Edges

```
intake → classify_difficulty → extract → validate
validate → extract            (if fixable issues and attempts < 3)
validate → reconcile          (otherwise)
reconcile → detect_anomalies → route
route → post                  (auto_approve)
route → human_review → post   (or → reject)
route → vendor_query → extract (when the vendor replies with a corrected invoice)
```

### Extracted schema

```python
class LineItem(BaseModel):
    description: str
    hsn_sac: str | None
    quantity: Decimal
    unit_price: Decimal
    tax_rate: Decimal           # percent
    amount: Decimal

class ExtractedInvoice(BaseModel):
    vendor_name: str
    vendor_gstin: str | None
    buyer_gstin: str | None
    invoice_number: str
    invoice_date: date
    due_date: date | None
    po_number: str | None
    currency: str = "INR"
    line_items: list[LineItem]
    subtotal: Decimal
    cgst: Decimal
    sgst: Decimal
    igst: Decimal
    total: Decimal
    bank_account_last4: str | None
    bank_ifsc: str | None
```

### Validation tools (deterministic, no LLM)

- `check_gstin_format`: 15 characters, correct pattern and checksum digit.
- `check_tax_maths`: line amounts sum to subtotal; subtotal plus taxes equals total; tolerance of 1 rupee.
- `check_tax_split`: either CGST + SGST, or IGST, never both.
- `check_dates`: invoice date not in the future; due date not before invoice date.
- `check_duplicate`: same vendor GSTIN and invoice number already exists.
- `check_vendor_known`: GSTIN matches a vendor in the vendor master.

### Routing rules

```
if any anomaly with severity == "high"        → human_review
elif missing mandatory field after 3 attempts → vendor_query (if vendor email known) else human_review
elif min(field_confidence) < tenant.threshold → human_review      # default threshold 0.85
elif match_result.status != "matched"         → human_review
elif total > tenant.auto_approve_limit        → human_review      # default ₹50,000
else                                          → auto_approve
```

### Three-way reconciliation

1. **Invoice to purchase order.** Match by PO number; fall back to vendor plus amount. Compare each line by description similarity, quantity and unit price. Allow a configurable tolerance (default 2%).
2. **Invoice to bank statement.** Bank statements are uploaded as CSV. Match by amount, date window and narration text. Handle:
   - one payment covering several invoices,
   - one invoice paid in parts,
   - TDS deducted (payment is lower than the invoice total by a known percentage).
3. Result status: `matched`, `partial`, `mismatch`, `no_po`, `unpaid`. Each result includes a plain-language explanation.

### Anomaly rules

| Rule | Severity |
|---|---|
| Bank account or IFSC differs from the vendor master | High |
| Duplicate invoice number, or same vendor, amount and date as an existing invoice | High |
| Unit price more than 20% above the vendor's 90-day average for the same item | Medium |
| Invoice from an unknown vendor above the auto-approve limit | Medium |
| Round-number total just under the auto-approve limit | Low |
| Injection scan found suspicious instructions in the document | High |

---

## 5. Data model

All tables carry `tenant_id` and are protected by Postgres row-level security.

| Table | Key columns |
|---|---|
| `tenants` | id, name, confidence_threshold, auto_approve_limit |
| `users` | id, tenant_id, email, role (`admin`, `reviewer`) |
| `vendors` | id, tenant_id, name, gstin, email, bank_account_last4, bank_ifsc |
| `purchase_orders` | id, tenant_id, vendor_id, po_number, date, total, status |
| `po_lines` | id, po_id, description, quantity, unit_price |
| `invoices` | id, tenant_id, vendor_id, status, source, extraction (JSONB), field_confidence (JSONB), total, invoice_date, model_used, cost_usd, latency_ms |
| `invoice_lines` | id, invoice_id, description, hsn_sac, quantity, unit_price, tax_rate, amount |
| `invoice_files` | id, invoice_id, s3_key, page_no, sha256 |
| `bank_transactions` | id, tenant_id, date, amount, narration, reference |
| `matches` | id, invoice_id, po_id, bank_transaction_ids, status, explanation |
| `anomalies` | id, invoice_id, rule, severity, detail |
| `review_decisions` | id, invoice_id, user_id, action, field_edits (JSONB), comment |
| `audit_log` | id, tenant_id, actor, action, entity, entity_id, before, after, created_at (append-only) |
| `email_messages` | id, tenant_id, invoice_id, direction, subject, body, message_id |
| `contract_chunks` | id, tenant_id, vendor_id, content, embedding (vector), page_no, source_file |
| `eval_runs` | id, git_sha, model, field_accuracy, routing_accuracy, avg_cost_usd, p95_latency_ms, created_at |
| `eval_results` | id, eval_run_id, case_id, field, expected, actual, correct |

Invoice status values: `received`, `processing`, `needs_review`, `awaiting_vendor`, `approved`, `rejected`, `failed`.

---

## 6. API

Base path `/api/v1`. All routes require a JWT except `/health`.

| Method | Path | Purpose |
|---|---|---|
| POST | `/invoices` | Upload files; creates the invoice and queues the workflow |
| GET | `/invoices` | List with filters (status, vendor, date range) |
| GET | `/invoices/{id}` | Full detail: extraction, confidence, match, anomalies, events |
| GET | `/invoices/{id}/events` | SSE stream of workflow progress |
| POST | `/invoices/{id}/review` | Body: `action` (`approve`, `edit`, `reject`), `field_edits`, `comment`. Resumes the workflow |
| POST | `/invoices/{id}/reprocess` | Re-run from extraction |
| GET, POST | `/vendors`, `/purchase-orders` | CRUD |
| POST | `/bank-statements` | Upload CSV |
| POST | `/contracts` | Upload a contract PDF for RAG |
| POST | `/chat` | SSE. Routes the question to the SQL tool, the RAG tool or both |
| GET | `/analytics/summary` | Totals, auto-approval rate, average cost, average latency |
| GET | `/analytics/costs` | Cost per model and per day |
| GET | `/evals/runs`, `/evals/runs/{id}` | Eval history and per-case results |
| GET | `/audit-log` | Filterable audit trail |
| GET, PATCH | `/settings` | Thresholds and limits |

---

## 7. Frontend pages

| Page | Contents |
|---|---|
| `/login` | Supabase Auth. Include a "Try the demo" button that signs into a seeded demo tenant |
| `/dashboard` | Key numbers, invoices by status, recent activity, anomalies needing attention |
| `/invoices` | Table with filters and upload dropzone |
| `/invoices/[id]` | Left: document viewer. Right: extracted fields with confidence colours, validation issues, match result, anomalies, live workflow timeline, action buttons |
| `/review` | Queue of invoices needing review, with keyboard shortcuts |
| `/reconciliation` | Invoice, PO and bank transaction side by side, with the explanation |
| `/chat` | Ask questions. Shows the generated SQL, a chart, and citations for contract answers |
| `/evals` | Accuracy by field, by model and over time; drill into failed cases |
| `/costs` | Cost per invoice, per model; routing split |
| `/vendors`, `/purchase-orders` | Master data |
| `/settings` | Thresholds, limits, connected inbox |
| `/audit` | Audit trail |

---

## 8. Evaluation

Evaluation is the feature that most separates this from a demo project. Build it carefully.

### Synthetic dataset

Write `scripts/generate_dataset.py` that produces 100 invoices with ground-truth JSON:

- 10 or more visual templates (HTML rendered to PDF).
- 40 clean digital PDFs, 30 scanned-looking (rotated, blurred, low contrast, rendered as images), 15 multi-page, 15 with deliberate problems.
- Deliberate problems: wrong tax total, missing GSTIN, duplicate invoice, changed bank details, price jump, hidden text containing a prompt injection.
- Each case has `expected_extraction.json` and `expected_route`.
- Also generate matching vendors, purchase orders and a bank statement CSV.

### Metrics

| Metric | How it is measured |
|---|---|
| Field accuracy | Exact match per field after normalisation (dates, decimals, whitespace) |
| Line-item accuracy | F1 over matched line items |
| Routing accuracy | Predicted route equals expected route |
| Anomaly recall and precision | Against the labelled problem cases |
| Auto-approval rate | Share of invoices approved with no human |
| False auto-approvals | Auto-approved invoices that had an error. Target: zero |
| Cost per invoice | Token cost from the router |
| Latency | p50 and p95 end to end |
| RAG answer quality | LLM-as-judge for faithfulness and relevance on 30 question-answer pairs |
| Text-to-SQL accuracy | Result-set match on 30 questions |

### Eval-gated CI

- `make eval` runs the full set and writes an `eval_runs` row and a JSON report.
- `make eval-smoke` runs 20 cases; this runs on every pull request.
- The pull request fails if field accuracy drops more than 2 points below the baseline in `evals/baseline.json`, or if any false auto-approval appears.
- The full set runs nightly and on merges to `main`.

---

## 9. Security and guardrails

- **Tenant isolation:** row-level security on every table; the backend sets `app.tenant_id` per request; S3 keys are prefixed by tenant. Write a test that proves tenant A cannot read tenant B's data.
- **Prompt injection:** document and email content is always passed as data, never as instructions. An intake scan flags instruction-like text. The LLM has no tool that can approve an invoice; approval is done only by code after the routing rules.
- **Text-to-SQL safety:** read-only database role, `SELECT` only, parsed and validated with `sqlglot`, tenant filter enforced by RLS, row limit and statement timeout.
- **Email safety:** outgoing emails use fixed templates with LLM-filled slots; emails go only to the address in the vendor master; rate-limited.
- **Secrets:** AWS Secrets Manager in production, `.env` locally, nothing committed.
- **Audit:** every state change and human action is written to the append-only audit log.
- **PII:** only the last four digits of bank accounts are stored.

---

## 10. Repository structure

```
finops-agent/
├── README.md
├── docker-compose.yml
├── Makefile
├── .github/workflows/        ci.yml, eval.yml, deploy.yml
├── backend/
│   ├── pyproject.toml
│   ├── alembic/
│   ├── app/
│   │   ├── main.py
│   │   ├── api/              routers
│   │   ├── core/             config, auth, tenancy, logging
│   │   ├── db/               models, session
│   │   ├── schemas/          Pydantic models
│   │   ├── agents/
│   │   │   ├── graph.py      the StateGraph
│   │   │   ├── state.py
│   │   │   ├── nodes/        one file per node
│   │   │   ├── tools/        validation tools, email tool
│   │   │   └── prompts/      versioned prompt files
│   │   ├── llm/              router.py, pricing.py
│   │   ├── reconcile/        matching logic
│   │   ├── anomaly/          rules
│   │   ├── rag/              ingest, retrieve
│   │   ├── sql_agent/        text-to-SQL
│   │   ├── email/            IMAP poller, sender
│   │   ├── guardrails/
│   │   └── worker.py
│   └── tests/
├── frontend/                 Next.js app
├── evals/
│   ├── dataset/              generated cases
│   ├── run.py
│   └── baseline.json
├── scripts/                  generate_dataset.py, seed_demo.py
├── mcp-server/
├── cli/
└── docs/                     architecture.md, decisions/, this spec
```

---

## 11. Milestones

Planned at 1–2 hours a day. Start: 7 Oct 2026.

| Milestone | Target date | Deliverable | Done when |
|---|---|---|---|
| **M1. Skeleton and extraction** | 20 Oct | Repo, Docker Compose, database, auth, upload, `extract` node, invoice detail page | Uploading a PDF shows extracted fields in the UI |
| **M2. Validation and human review** | 3 Nov | `validate` with retry loop, confidence, routing, `interrupt()`, review screen, SSE timeline | A bad invoice pauses for review and resumes after approval, even after a server restart |
| **M3. Reconciliation** | 14 Nov | PO and bank statement upload, matching logic, reconciliation page | Partial payments, combined payments and TDS cases match correctly in tests |
| **M4. Evals and deployment** | 24 Nov | Dataset generator, eval harness, eval page, GitHub Actions with the eval gate, live deployment, README | Live URL works; CI fails when a prompt is deliberately broken. **Start applying here.** |
| **M5. Anomalies and email** | 4 Dec | Anomaly rules, inbox poller, vendor clarification emails | An emailed invoice with no GSTIN triggers a reply to the vendor |
| **M6. Model routing and RAG** | 14 Dec | Difficulty classifier, router, cost page, contract RAG with citations | Cost per invoice drops with routing at the same accuracy; numbers recorded |
| **M7. Text-to-SQL and tenancy hardening** | 24 Dec | Chat page with SQL and charts, RLS tests, audit page, injection tests | Tenant isolation test passes; injected invoice is flagged, not obeyed |
| **M8. MCP, CLI, learning** | 5 Jan 2027 | MCP server, npm CLI, per-vendor few-shot memory | Claude Desktop can query invoices through MCP |

### Definition of done for every milestone

- Unit tests pass; new logic has tests.
- `docker compose up` starts everything from a clean clone.
- README updated; one architecture decision record added if a design choice was made.
- Deployed to the live environment from M4 onward.
- Eval numbers recorded from M4 onward.

---

## 12. Instructions for the coding agent

1. Work one milestone at a time, in order. Do not start features from later milestones.
2. Before writing code for a milestone, propose a short task list and wait for approval.
3. Follow the repository structure and tech stack in this document. If a change seems necessary, explain why and ask first.
4. Keep the LLM out of anything that can be done with plain code: maths, format checks, routing and approval are deterministic.
5. Every LLM call goes through `LLMRouter` so cost, latency and model name are recorded.
6. Prompts live in `agents/prompts/` as files, never inline strings.
7. Use Pydantic structured output for every LLM response that feeds code.
8. Write tests alongside code. Reconciliation and validation need thorough unit tests with edge cases.
9. Never use real invoices, real GSTINs of real companies or real bank details. Use the synthetic generator.
10. Small commits with clear messages. One pull request per feature.
11. No secrets in code or commits.

---

## 13. What to record for the resume

Fill these in from real eval runs. Do not estimate.

- Field-level extraction accuracy on 100 invoices: ____%
- Auto-approval rate with zero false approvals: ____%
- Cost per invoice before and after model routing: ____ → ____
- p95 processing time per invoice: ____ seconds
- Anomaly detection recall on seeded fraud cases: ____%
- Text-to-SQL accuracy on 30 questions: ____%

**Draft resume entry (complete once the numbers exist)**

> **FinOps Agent** (live demo · GitHub): autonomous accounts-payable system for small businesses.
> - Built a multi-agent LangGraph workflow with self-correcting extraction, three-way reconciliation and human-in-the-loop approval; __% field accuracy on a 100-invoice eval set with zero false auto-approvals.
> - Added eval-gated CI that blocks merges when accuracy regresses, and model routing that cut cost per invoice by __% at equal accuracy.
> - Shipped fraud detection, contract RAG, text-to-SQL analytics and an MCP server on FastAPI, Next.js, PostgreSQL and AWS.

---

## 14. Risks

| Risk | Mitigation |
|---|---|
| Scope is large for a part-time build | P0 features alone make a complete project. Stop at M4 if time runs short |
| LLM costs during development | Use a low-cost model for development; cache responses in tests; run the full eval only nightly |
| Email setup takes too long | Use a dedicated mailbox with IMAP and an app password. Upload remains the primary path |
| Hosting cost | One small EC2 instance, Supabase and Vercel free tiers; add a budget alert on day one |
| Demo breaks during an interview | Seeded demo tenant, a recorded 2-minute video in the README, and a health check alert |
