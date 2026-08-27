#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if ! curl -sf http://localhost:11434/api/version >/dev/null 2>&1; then
  echo "→ štartujem ollama serve"
  nohup ollama serve >/tmp/ollama.log 2>&1 &
  sleep 2
fi

# zabíjame len naše dva servery — ollama daemon necháme bežať
# exec: subshell sa NAHRADÍ serverom, takže kill PID trafí priamo server
(cd backend && exec uv run uvicorn app.main:app --reload --port 8000) &
BACK_PID=$!
(cd frontend && exec pnpm dev) &
FRONT_PID=$!
trap 'kill "$BACK_PID" "$FRONT_PID" 2>/dev/null' EXIT INT TERM
wait
