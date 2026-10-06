#!/usr/bin/env bash
# Starts the LLM router, migrates, then runs the API and the worker. If any process
# exits, the container exits and the Space restarts it.
set -euo pipefail

export LLM_ROUTER_URL="http://127.0.0.1:4000/v1"
export LLM_ROUTER_KEY="${LITELLM_MASTER_KEY:?set LITELLM_MASTER_KEY as a Space secret}"

wait_for() {  # no curl in the slim image
  for _ in $(seq 1 90); do
    python -c "import urllib.request,sys; urllib.request.urlopen(sys.argv[1], timeout=2)" "$1" \
      2>/dev/null && return 0
    sleep 2
  done
  echo "timed out waiting for $1" >&2
  return 1
}

# LiteLLM treats DATABASE_URL as its own (optional) database and exits on our asyncpg URL,
# so it must not see the app's.
env -u DATABASE_URL /opt/litellm/bin/litellm --config litellm.yaml --host 127.0.0.1 --port 4000 &
wait_for http://127.0.0.1:4000/health/liveliness

cd backend
alembic upgrade head
python -m app.seed
arq app.worker.WorkerSettings &
uvicorn app.main:app --host 0.0.0.0 --port 7860 --timeout-graceful-shutdown 5 --proxy-headers &

wait -n
exit 1
