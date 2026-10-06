.PHONY: up down infra logs test lint migration samples dataset eval eval-smoke eval-oracle backend-dev worker-dev frontend-dev

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

dataset:       ## Generate the 100-case eval dataset into evals/dataset/
	cd backend && uv run python ../scripts/generate_dataset.py

eval: dataset  ## Full eval (100 cases): records a run, writes a report, applies the gate
	cd backend && uv run python ../evals/run.py

eval-smoke: dataset  ## 20-case eval, as run on every pull request
	cd backend && uv run python ../evals/run.py --smoke

eval-oracle: dataset  ## Ground-truth extraction: tests rules and matching, no LLM calls
	cd backend && uv run python ../evals/run.py --oracle --no-gate

backend-dev:
	cd backend && uv run alembic upgrade head && uv run python -m app.seed && uv run uvicorn app.main:app --reload --port 8000 --timeout-graceful-shutdown 5

worker-dev:
	cd backend && uv run arq app.worker.WorkerSettings

frontend-dev:
	cd frontend && npm run dev
