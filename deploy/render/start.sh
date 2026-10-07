#!/usr/bin/env bash
# Migrate, seed the demo tenant, then run the worker and the API. If either exits, the
# container exits and Render restarts it.
set -euo pipefail

alembic upgrade head
python -m app.seed
arq app.worker.WorkerSettings &
uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-10000}" \
  --timeout-graceful-shutdown 5 --proxy-headers --forwarded-allow-ips '*' &

wait -n
exit 1
