#!/usr/bin/env bash
# Starts the LLM router, migrates, then runs the API and the worker. If any process
# exits, the container exits and the Space restarts it.
set -euo pipefail

export LLM_ROUTER_URL="http://127.0.0.1:4000/v1"
export LLM_ROUTER_KEY="${LITELLM_MASTER_KEY:?set LITELLM_MASTER_KEY as a Space secret}"

/opt/litellm/bin/litellm --config litellm.yaml --host 127.0.0.1 --port 4000 &
for _ in $(seq 1 60); do curl -sf http://127.0.0.1:4000/health/liveliness >/dev/null && break; sleep 2; done

cd backend
alembic upgrade head
python -m app.seed
arq app.worker.WorkerSettings &
uvicorn app.main:app --host 0.0.0.0 --port 7860 --timeout-graceful-shutdown 5 --proxy-headers &

wait -n
exit 1
