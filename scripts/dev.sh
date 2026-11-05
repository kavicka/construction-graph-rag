#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

# sync the core dependencies; --inexact keeps opt-in extras (voice from `make install-voice`,
# visual) that are already installed — this script never adds one
(cd backend && uv sync --inexact)

if ! curl -sf http://localhost:11434/api/version >/dev/null 2>&1; then
  echo "→ starting ollama serve"
  nohup ollama serve >/tmp/ollama.log 2>&1 &
  sleep 2
fi

# kill only our two servers on exit — the ollama daemon keeps running
# exec: the subshell is REPLACED by the server, so kill PID hits the server directly
(cd backend && exec uv run uvicorn app.main:app --reload --port 8000) &
BACK_PID=$!
(cd frontend && exec pnpm dev) &
FRONT_PID=$!
trap 'kill "$BACK_PID" "$FRONT_PID" 2>/dev/null' EXIT INT TERM
wait
