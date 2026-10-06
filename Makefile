.PHONY: up down infra logs test lint migration samples backend-dev worker-dev frontend-dev

up:            ## Start everything
	docker compose up -d --build

down:
	docker compose down

infra:         ## Only Postgres, Redis, object store (run backend/frontend locally)
	docker compose up -d postgres redis objectstore

logs:
	docker compose logs -f backend worker

test:
	cd backend && uv run pytest -q

lint:
	cd backend && uv run ruff check . && uv run ruff format --check .

migration:     ## make migration m="message"
	cd backend && uv run alembic revision --autogenerate -m "$(m)"

samples:       ## Generate synthetic sample invoices into samples/
	cd backend && uv run python ../scripts/make_sample_invoices.py

backend-dev:
	cd backend && uv run alembic upgrade head && uv run python -m app.seed && uv run uvicorn app.main:app --reload --port 8000

worker-dev:
	cd backend && uv run arq app.worker.WorkerSettings

frontend-dev:
	cd frontend && npm run dev
