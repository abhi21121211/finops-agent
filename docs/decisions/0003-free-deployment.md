# ADR 0003: Free deployment on Hugging Face Spaces, Vercel, Supabase and Upstash

- **Status:** Superseded by [ADR 0004](0004-render-instead-of-hugging-face.md) (Hugging Face made Docker Spaces paid)
- **Date:** 2026-10-06

## Context

The spec deploys the backend, worker and Redis to one EC2 instance, with AWS Secrets
Manager and CloudWatch. ADR 0001 deferred hosting to M4 with a constraint: free.

## Decision

- **Backend:** one Hugging Face Docker Space runs uvicorn, the arq worker and the LiteLLM
  router as three processes (`deploy/hf-space/start.sh`). If any process exits, the
  container exits and the Space restarts it.
- **Frontend:** Vercel Hobby, deployed by Vercel's GitHub integration.
- **Data:** Supabase Postgres (with pgvector) and Supabase Storage through its S3 API,
  so the storage code is unchanged. Redis on Upstash over TLS.
- **Secrets:** Space secrets and GitHub Actions secrets instead of AWS Secrets Manager.
  **Logs:** structured JSON to stdout, visible in the Space logs, instead of CloudWatch.
- **Deploys:** `deploy.yml` pushes an assembled build context to the Space after CI
  passes on `main`.

## Consequences

- Cold starts: the Space sleeps after inactivity, and Supabase pauses after a week. The
  runbook says to warm both up before a demo.
- API, worker and router share one container's CPU. That is fine for a demo; to scale,
  split them into separate services (the code needs no changes).
- Moving to the spec's AWS setup is a matter of configuration. Everything speaks
  Postgres, S3, Redis and an OpenAI-compatible router.
