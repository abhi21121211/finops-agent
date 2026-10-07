# Render free web service (512 MB): API + job worker in one container. No LLM proxy:
# the app calls the free providers directly (LLM_MODE=direct) to stay well under 512 MB.
# Build context: repository root.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH" \
    LLM_MODE=direct WORKER_POLL_DELAY_S=5 \
    DEMO_DAILY_RESET=true DEMO_SAMPLES_DIR=/app/samples

WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/ ./
COPY samples/ ./samples/
COPY deploy/render/start.sh /start.sh

RUN useradd --create-home app
USER app
CMD ["bash", "/start.sh"]
