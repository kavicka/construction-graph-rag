.PHONY: dev backend frontend models test lint eval eval-full eval-gate eval-baseline mcp docker

# retrieval ablácie nad stavebným korpusom (eval/construction.py) → backend/eval/REPORT.md
eval:
	cd backend && uv run python -m eval.harness

# + kvalita odpovedí (LLM judge) a prompt-injection útoky — potrebuje Ollamu, ~30 min
eval-full:
	cd backend && uv run python -m eval.harness --generate --injection

# regresný gate: zlyhá, ak metrika klesne o viac ako 2 body pod eval/baseline.json
eval-gate:
	cd backend && uv run python -m eval.harness --generate --gate

eval-baseline:
	cd backend && uv run python -m eval.harness --generate --update-baseline

# MCP server cez stdio (Claude Desktop / Claude Code)
mcp:
	cd backend && uv run python -m app.mcp_server

docker:
	docker compose up -d --build

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
	cd backend && uv run ruff check . && uv run mypy app eval
	cd frontend && pnpm lint
