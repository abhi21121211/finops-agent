# Deploying FinOps Agent for free

Everything here runs on free tiers. **None of them needs a credit card.** Expect about
45 minutes the first time.

| Piece | Service (free tier) | What it runs |
|---|---|---|
| Web app | **Vercel** Hobby | `frontend/` (Next.js) |
| API + job worker | **Render** free web service (512 MB) | `deploy/render/` image, via `render.yaml` |
| Postgres + pgvector | **Supabase** Free | data, workflow checkpoints |
| Invoice files | **Supabase Storage** (S3 API) | uploads, page images |
| Job queue | **Upstash Redis** Free | arq jobs, live event pub/sub |
| LLMs | Gemini, Mistral, OpenRouter, Groq free tiers | called directly by the app (`LLM_MODE=direct`) |

```
Browser ──▶ Vercel (Next.js) ──HTTPS──▶ Render web service
                                         ├─ uvicorn (API)
                                         └─ arq worker ──▶ free LLM providers (with failover)
                                    Supabase Postgres · Supabase Storage · Upstash Redis
```

Measured locally under Render's limits (512 MB, half a CPU), processing three invoices at
once: 284 MB peak. That is why there is no LLM proxy in production: LiteLLM alone needed
370 MB. See [ADR 0004](decisions/0004-render-instead-of-hugging-face.md).

## 1. Supabase: database and file storage

1. Create a project at [supabase.com](https://supabase.com). Pick the Mumbai region.
2. **Database URL.** Click **Connect** at the top, then **Session pooler**, and copy the URI.
   Convert it to the app's format by changing the start to `postgresql+asyncpg://` and
   adding `?ssl=require` at the end:
   `postgresql+asyncpg://postgres.<ref>:<password>@aws-0-ap-south-1.pooler.supabase.com:5432/postgres?ssl=require`.
   Use the **Session** pooler (port 5432), not the Transaction pooler.
3. Enable pgvector: *Database → Extensions →* search `vector` *→ enable*.
4. **Storage.**
   - Create a **private** bucket named `invoices`.
   - Then go to *Project Settings → Storage → S3 Connection*, enable it, and create an
     access key.
   - Note the endpoint (`https://<ref>.storage.supabase.co/storage/v1/s3`), the region,
     the access key ID and the secret.

> You don't need the Supabase "anon"/"publishable" key or project URL for this app. Those
> are for browser apps that talk to Supabase directly. Ours goes through its own API.

## 2. Upstash: Redis

Create a Redis database at [upstash.com](https://upstash.com) in the region closest to Mumbai.

On the database page, open the **TCP** tab (not REST) and copy the URL that starts with
`rediss://default:...@...upstash.io:6379`. The app needs this Redis protocol URL. The
`UPSTASH_REDIS_REST_URL`/`TOKEN` pair is a different, HTTP-based API.

The free tier allows 500,000 commands a month. The worker polls every 5 seconds
(`WORKER_POLL_DELAY_S=5`) and Render sleeps when idle, so normal demo use stays far below that.

## 3. Render: API and worker

1. Sign up at [render.com](https://render.com) with GitHub. No card is needed.
2. Click **New → Blueprint**, pick the `finops-agent` repository and click **Apply**.
   `render.yaml` creates the `finops-agent-api` service on the free plan.
3. Render asks for the values marked `sync: false`. Fill in:

   | Name | Value |
   |---|---|
   | `DATABASE_URL` | from step 1.2 |
   | `REDIS_URL` | the `rediss://` URL from step 2 |
   | `S3_ENDPOINT`, `S3_PUBLIC_ENDPOINT` | the Supabase S3 endpoint (the same value in both) |
   | `S3_REGION` | the region shown on the Supabase S3 page (e.g. `ap-south-1`) |
   | `S3_ACCESS_KEY`, `S3_SECRET_KEY` | from step 1.4 |
   | `CORS_ORIGINS` | `["https://<your-app>.vercel.app"]` (set after step 4; use `["*"]` until then) |
   | `GEMINI_API_KEY`, `MISTRAL_API_KEY`, `OPENROUTER_API_KEY`, `GROQ_API_KEY` | your free-tier keys (any subset works; Gemini alone is enough) |

   `JWT_SECRET` is generated for you.
4. The first deploy takes about 5 minutes. When it's done,
   `https://finops-agent-api.onrender.com/health` returns `{"status":"ok"}`. Your URL may
   end differently; Render shows it at the top of the service page.

After that, Render deploys every push to `main` once GitHub's CI checks pass.

## 4. Vercel: web app

1. Go to *Add New → Project*, import `finops-agent` and set **Root Directory** to `frontend`.
2. Add the environment variable
   `NEXT_PUBLIC_API_URL=https://finops-agent-api.onrender.com/api/v1`, using your Render URL.
3. Click Deploy. Then set `CORS_ORIGINS` on Render to `["https://<your-app>.vercel.app"]`.

## 5. GitHub: the eval gate

In the repository's *Settings → Secrets and variables → Actions*, add `GEMINI_API_KEY`
(and optionally `MISTRAL_API_KEY`, `OPENROUTER_API_KEY`, `GROQ_API_KEY`). Pull requests
then run the 20-case eval gate, and `main` runs all 100 cases nightly.

## Free-tier behaviour to know before a demo

- **Render sleeps** after 15 minutes without traffic. The first request then takes about a
  minute while it wakes. Open the app a couple of minutes before a demo.
- **The Supabase project pauses** after a week of inactivity. Resume it from the
  dashboard; the data is kept.
- **The LLM free tiers have daily quotas.** The app fails over between providers, and the
  worker retries rate-limited jobs with backoff. Under heavy use, an invoice can take
  minutes or end up `failed` with a "Reprocess" button.
- **Render's free bandwidth is 5 GB a month.** Invoice images are served straight from
  Supabase Storage, not through Render, so this is ample.
- **Logs** are in the Render service's *Logs* tab (structured JSON from the API and worker).

## Public demo protection

Configured in `backend/app/core/config.py` and enforced by `app/core/ratelimit.py`:

| Setting | Default | What it limits |
|---|---|---|
| `DEMO_UPLOADS_PER_HOUR` | 10 | invoice uploads and reprocesses per visitor (IP) |
| `DEMO_STATEMENTS_PER_HOUR` | 10 | bank statement uploads per visitor |
| `DEMO_LOGINS_PER_HOUR` | 30 | demo logins per visitor |
| `DEMO_DAILY_LLM_BUDGET` | 150 | invoices processed per day, all visitors together |

Visitors over a limit get a "try again in N minutes" message.

**Daily reset** (`DEMO_DAILY_RESET=true`, set in the Render image). The first demo login
each day wipes the demo company's invoices, files and payments and reloads the six samples
and the sample bank statement. Render sleeps when idle, so a cron job wouldn't run reliably.
Other tenants and eval history are never touched.

## Never paste secrets into chat or commit them

`.env` is gitignored. Keys belong in Render, Vercel and GitHub settings only. If a key has
been shared anywhere public, rotate it in the provider's dashboard.

## Running the full stack locally instead

```bash
cp .env.example .env
docker compose up -d --build
docker compose --profile router up -d   # optional: LiteLLM on :4001 (provider keys + LITELLM_MASTER_KEY in .env)
```
