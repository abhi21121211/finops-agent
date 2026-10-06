# Deploying FinOps Agent for free

Everything here runs on free tiers. Total cost: ₹0. Expect about 45 minutes the first time.

| Piece | Service (free tier) | What it runs |
|---|---|---|
| Web app | **Vercel** Hobby | `frontend/` (Next.js) |
| API, worker, LLM router | **Hugging Face Spaces**, Docker, CPU basic | `deploy/hf-space/` image |
| Postgres + pgvector | **Supabase** Free | data, workflow checkpoints |
| Invoice files | **Supabase Storage** (S3 API) | uploads, page images |
| Job queue | **Upstash Redis** Free | arq jobs, live event pub/sub |
| LLMs | Gemini, Mistral, OpenRouter, Groq free tiers via LiteLLM | `infra/litellm/config.yaml` |

```
Browser ──▶ Vercel (Next.js) ──HTTPS──▶ HF Space :7860
                                         ├─ uvicorn (API)
                                         ├─ arq worker
                                         └─ LiteLLM :4000 ──▶ free LLM providers
                                    Supabase Postgres · Supabase Storage · Upstash Redis
```

## 1. Supabase: database and file storage

1. Create a project at [supabase.com](https://supabase.com). Pick the Mumbai region (`ap-south-1`).
2. **Database URL.** Go to *Connect → Session pooler* and copy the URI. Turn it into the
   app's format:
   `postgresql+asyncpg://postgres.<ref>:<password>@aws-0-ap-south-1.pooler.supabase.com:5432/postgres?ssl=require`.
   Use the **session** pooler (port 5432), not the transaction pooler. The transaction
   pooler breaks prepared statements.
3. Enable pgvector: *Database → Extensions → vector*.
4. **Storage.** Create a **private** bucket named `invoices`. Then go to
   *Project Settings → Storage → S3 Connection* and create an access key. Note the
   endpoint (`https://<ref>.supabase.co/storage/v1/s3`), the region, the key id and the secret.

## 2. Upstash: Redis

Create a Redis database at [upstash.com](https://upstash.com) in the region nearest
Mumbai. Copy the `rediss://default:<password>@<host>:6379` URL. It must start with
`rediss`, which means TLS.

## 3. Hugging Face Space: API, worker and router

1. Create a Space at [huggingface.co/new-space](https://huggingface.co/new-space). Choose
   **Docker → Blank** and **CPU basic (free)**, and name it e.g. `finops-agent-api`.
2. Add these under *Settings → Variables and secrets*. Every one is a **secret** except
   `S3_REGION` and `CORS_ORIGINS`.

   | Name | Value |
   |---|---|
   | `DATABASE_URL` | from step 1.2 |
   | `REDIS_URL` | from step 2 |
   | `S3_ENDPOINT`, `S3_PUBLIC_ENDPOINT` | `https://<ref>.supabase.co/storage/v1/s3` (both) |
   | `S3_ACCESS_KEY`, `S3_SECRET_KEY` | from step 1.4 |
   | `S3_BUCKET` | `invoices` |
   | `S3_REGION` | e.g. `ap-south-1` |
   | `JWT_SECRET` | a long random string (`python -c "import secrets;print(secrets.token_urlsafe(48))"`) |
   | `LITELLM_MASTER_KEY` | another long random string |
   | `GEMINI_API_KEY`, `MISTRAL_API_KEY`, `OPENROUTER_API_KEY`, `GROQ_API_KEY` | your free-tier keys (any subset works) |
   | `CORS_ORIGINS` | `["https://<your-app>.vercel.app"]` |

3. Create a Hugging Face **access token** with *write* permission (*Settings → Access Tokens*).

## 4. GitHub: CI, eval gate and backend deploys

In the repository's *Settings → Secrets and variables → Actions*:

- **Secrets:** `HF_TOKEN` (from step 3.3), plus `GEMINI_API_KEY`, `MISTRAL_API_KEY`,
  `OPENROUTER_API_KEY` and `GROQ_API_KEY` for the eval gate.
- **Variables:** `HF_SPACE` = `<hf-username>/finops-agent-api`.

Every push to `main` that passes CI now deploys the backend (`.github/workflows/deploy.yml`).
To deploy the first time without pushing, run *Actions → Deploy backend → Run workflow*.
The Space's build log shows progress. The API is live when
`https://<hf-username>-finops-agent-api.hf.space/health` returns `{"status":"ok"}`.

## 5. Vercel: web app

1. Go to *Add New → Project*, import the GitHub repository and set **Root Directory** to `frontend`.
2. Add the environment variable
   `NEXT_PUBLIC_API_URL=https://<hf-username>-finops-agent-api.hf.space/api/v1`.
3. Deploy. If the final `*.vercel.app` URL differs from the one in `CORS_ORIGINS`, update
   that Space variable.

## Free-tier behaviour to know before a demo

- **The Space sleeps** after about 48 h without traffic. The first request then takes a
  minute or two while it boots. Open the app before an interview.
- **The Supabase project pauses** after a week of inactivity. Resume it from the
  dashboard; the data is kept.
- **The LLM free tiers have daily quotas.** The router fails over between providers, and
  the worker retries rate-limited jobs with backoff. Under heavy use an invoice can take
  minutes, or end up `failed` with a "Reprocess" option.
- **Logs:** the Space's *Logs* tab shows the structured JSON logs from the API and the worker.

## Running the full stack locally instead

```bash
cp .env.example .env    # set LLM_ROUTER_KEY, or use the bundled router below
docker compose up -d --build
docker compose --profile router up -d   # optional: LiteLLM on :4001 (provider keys + LITELLM_MASTER_KEY in .env)
```
