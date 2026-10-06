# Hugging Face Space (Docker SDK, free CPU): API + worker + LLM router in one container.
# Build context is assembled by .github/workflows/deploy.yml (backend/, infra/, this dir).
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/app-venv PATH="/opt/app-venv/bin:$PATH"

# Spaces run as uid 1000.
RUN useradd -m -u 1000 user
WORKDIR /home/user/app

# LiteLLM in its own environment: its pins must not constrain the app's.
RUN uv venv /opt/litellm && VIRTUAL_ENV=/opt/litellm uv pip install 'litellm[proxy]'

COPY --chown=user backend/pyproject.toml backend/uv.lock backend/.python-version ./backend/
RUN cd backend && uv sync --frozen --no-dev --no-install-project
COPY --chown=user backend ./backend
COPY --chown=user infra/litellm/config.yaml ./litellm.yaml
COPY --chown=user start.sh ./start.sh

USER user
EXPOSE 7860
CMD ["bash", "start.sh"]
