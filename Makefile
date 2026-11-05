.PHONY: install dev backend frontend models test lint eval eval-full eval-gate eval-baseline mcp docker \
	demo demo-seed demo-up demo-reset

# Voice (Whisper STT + Piper TTS, GPL-3) is an optional extra — installed on macOS only
UV_EXTRAS := $(if $(filter Darwin,$(shell uname -s)),--extra voice,)

install:
	cd backend && uv sync $(UV_EXTRAS)
	cd frontend && pnpm install

# retrieval ablations over the construction corpus (eval/construction.py) → backend/eval/REPORT.md
eval:
	cd backend && uv run python -m eval.harness

# + answer quality (LLM judge) and prompt-injection attacks — needs Ollama, ~30 min
eval-full:
	cd backend && uv run python -m eval.harness --generate --injection

# regression gate: fails if a metric drops more than 2 points below eval/baseline.json
eval-gate:
	cd backend && uv run python -m eval.harness --generate --gate

eval-baseline:
	cd backend && uv run python -m eval.harness --generate --update-baseline

# MCP server over stdio (Claude Desktop / Claude Code)
mcp:
	cd backend && uv run python -m app.mcp_server

docker:
	docker compose up -d --build

dev:
	./scripts/dev.sh

# synthetic demo in demo-data/ (backend :8010, frontend :5180) — seeds on the first run
demo:
	./scripts/demo.sh

demo-seed:
	./scripts/demo.sh seed

demo-up:
	./scripts/demo.sh up

demo-reset:
	rm -rf demo-data

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
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy app eval
	cd frontend && pnpm lint
