.PHONY: dev backend frontend models test lint eval

eval:
	cd backend && uv run python -m eval.run_eval

dev:
	./scripts/dev.sh

backend:
	cd backend && uv run uvicorn app.main:app --reload --port 8000

frontend:
	cd frontend && pnpm dev

models:
	./scripts/setup_models.sh

test:
	cd backend && uv run pytest -q
	cd frontend && pnpm build

lint:
	cd backend && uv run ruff check . && uv run mypy app
