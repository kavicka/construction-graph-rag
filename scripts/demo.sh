#!/usr/bin/env bash
# One-command demo on synthetic data (fictional company Stavby Kráľ, 12 generated PDFs).
#
#   scripts/demo.sh          seed demo-data/ if needed, then run backend + frontend (Ctrl-C stops)
#   scripts/demo.sh seed     only seed demo-data/ (backend is started and stopped again)
#   scripts/demo.sh up       only run backend + frontend on an already seeded demo-data/
#
# Everything lives in demo-data/ (gitignored, `make demo-reset` wipes it). The demo never touches
# data/ or backend/.env: every setting is passed as an environment variable and the Python
# processes run from demo-data/, where there is no .env file to pick up.
#
# Overrides: DEMO_BACKEND_PORT (8010), DEMO_FRONTEND_PORT (5180), RAG_ANSWER_MODEL,
# RAG_EXTRACT_MODEL, RAG_WORKFORCE_MODEL, RAG_RAM_PROFILE (low), RAG_OLLAMA_BASE_URL.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEMO_DIR="$ROOT/demo-data"
BACKEND_PORT="${DEMO_BACKEND_PORT:-8010}"
FRONTEND_PORT="${DEMO_FRONTEND_PORT:-5180}"
API="http://127.0.0.1:$BACKEND_PORT"
MODE="${1:-all}"

# demo-only credentials — the data is fake, so they are printed and fixed
ADMIN_EMAIL="demo@example.com"
ADMIN_PASSWORD="demo-demo-demo"

die() { echo "demo: $*" >&2; exit 1; }
now() { date +%s; }
T_START=$(now)

case "$MODE" in all | seed | up) ;; *) die "usage: scripts/demo.sh [all|seed|up]" ;; esac
[ "$BACKEND_PORT" != 8000 ] || die "port 8000 is reserved for a regular instance — pick another DEMO_BACKEND_PORT"

export RAG_DATA_DIR="$DEMO_DIR"
export RAG_AUTH_ENABLED=true
export RAG_ADMIN_EMAIL="$ADMIN_EMAIL"
export RAG_ADMIN_PASSWORD="$ADMIN_PASSWORD"
export RAG_COOKIE_SECURE=false
export RAG_INBOX_SYNC=false
export RAG_LLM_PROVIDER=ollama
export RAG_OLLAMA_BASE_URL="${RAG_OLLAMA_BASE_URL:-http://localhost:11434}"
# low profile = small embedding model (~0.5 GB download) and qwen3:4b for the Firma module
export RAG_RAM_PROFILE="${RAG_RAM_PROFILE:-low}"
export RAG_ANSWER_MODEL="${RAG_ANSWER_MODEL:-qwen3:4b-instruct}"
export RAG_EXTRACT_MODEL="${RAG_EXTRACT_MODEL:-qwen3:1.7b}"
export RAG_WORKFORCE_MODEL="${RAG_WORKFORCE_MODEL:-$RAG_ANSWER_MODEL}"

# --- preflight: tools, Ollama, models, ports ----------------------------------------------

for tool in uv pnpm curl; do
  command -v "$tool" >/dev/null 2>&1 || die "$tool is missing (see README → Quick start)"
done

if ! curl -sf "$RAG_OLLAMA_BASE_URL/api/version" >/dev/null 2>&1; then
  die "Ollama is not running at $RAG_OLLAMA_BASE_URL — start it with \`ollama serve\` (install: https://ollama.com)"
fi
tags="$(curl -sf "$RAG_OLLAMA_BASE_URL/api/tags")"
missing=()
for model in $(printf '%s\n' "$RAG_ANSWER_MODEL" "$RAG_EXTRACT_MODEL" "$RAG_WORKFORCE_MODEL" | sort -u); do
  case "$model" in *:*) tagged="$model" ;; *) tagged="$model:latest" ;; esac
  grep -Eq "\"name\": ?\"$tagged\"" <<<"$tags" || missing+=("$model")
done
if [ ${#missing[@]} -gt 0 ]; then
  echo "demo: Ollama is missing these models — pull them first:" >&2
  for model in "${missing[@]}"; do echo "  ollama pull $model" >&2; done
  echo "(or point the demo at models you have, e.g. RAG_EXTRACT_MODEL=$RAG_ANSWER_MODEL make demo)" >&2
  exit 1
fi

port_busy() { curl -s -o /dev/null --max-time 2 "http://127.0.0.1:$1/" 2>/dev/null; }
port_busy "$BACKEND_PORT" && die "port $BACKEND_PORT is in use — stop it or set DEMO_BACKEND_PORT"
if [ "$MODE" != seed ]; then
  port_busy "$FRONTEND_PORT" && die "port $FRONTEND_PORT is in use — stop it or set DEMO_FRONTEND_PORT"
fi
if [ "$MODE" = up ] && [ ! -f "$DEMO_DIR/app.db" ]; then
  die "demo-data/ is empty — run \`make demo\` (or \`make demo-seed\`) first"
fi

mkdir -p "$DEMO_DIR"
if [ "$MODE" != seed ] && [ ! -d "$ROOT/frontend/node_modules" ]; then
  echo "→ pnpm install (frontend)"
  pnpm --dir "$ROOT/frontend" install --frozen-lockfile
fi
T_PREFLIGHT=$(now)

# --- processes -----------------------------------------------------------------------------

BACK_PID=""
FRONT_PID=""
cleanup() {
  [ -n "$FRONT_PID" ] && kill "$FRONT_PID" 2>/dev/null || true
  [ -n "$BACK_PID" ] && kill "$BACK_PID" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT TERM

start_backend() {
  echo "→ backend on $API (log: demo-data/backend.log)"
  # cwd = demo-data/ → pydantic-settings finds no .env; exec → the PID is uv, which forwards signals
  (cd "$DEMO_DIR" && PYTHONFAULTHANDLER=1 exec uv run --project "$ROOT/backend" \
    uvicorn app.main:app --app-dir "$ROOT/backend" --host 127.0.0.1 --port "$BACKEND_PORT") \
    >>"$DEMO_DIR/backend.log" 2>&1 &
  BACK_PID=$!
}

seed() {
  # demo_seed.py is idempotent and the backend resumes unfinished ingests on start, so a crashed
  # backend (rare native crash in the PDF parser on its first document) is simply restarted
  for attempt in 1 2 3; do
    [ -n "$BACK_PID" ] && kill -0 "$BACK_PID" 2>/dev/null || start_backend
    if (cd "$DEMO_DIR" && uv run --project "$ROOT/backend" python -u "$ROOT/backend/scripts/demo_seed.py" \
      --api "$API" --data-dir "$DEMO_DIR" --email "$ADMIN_EMAIL" --password "$ADMIN_PASSWORD"); then
      return 0
    fi
    kill -0 "$BACK_PID" 2>/dev/null && break # backend alive → a real seeding error
    status=0
    wait "$BACK_PID" 2>/dev/null || status=$?
    BACK_PID=""
    echo "→ backend exited with code $status (attempt $attempt) — restarting, work done so far is kept"
  done
  tail -n 30 "$DEMO_DIR/backend.log" >&2
  die "seeding failed (backend log: demo-data/backend.log)"
}

T_SEED_START=$(now)
if [ "$MODE" != up ]; then
  echo "→ seeding (first run: model downloads + LLM work, see the stage timings at the end)"
  seed
fi
T_SEED_END=$(now)

if [ "$MODE" != up ]; then
  echo
  echo "preflight + installs: $((T_PREFLIGHT - T_START)) s · seeding: $((T_SEED_END - T_SEED_START)) s"
fi
[ "$MODE" = seed ] && exit 0

[ -n "$BACK_PID" ] || start_backend
for _ in $(seq 1 600); do
  curl -sf "$API/api/health" >/dev/null 2>&1 && break
  kill -0 "$BACK_PID" 2>/dev/null || { tail -n 30 "$DEMO_DIR/backend.log" >&2; die "backend exited"; }
  sleep 1
done

echo "→ frontend on http://localhost:$FRONTEND_PORT"
(cd "$ROOT/frontend" && RAG_API="$API" exec pnpm dev --port "$FRONTEND_PORT" --strictPort) \
  >>"$DEMO_DIR/frontend.log" 2>&1 &
FRONT_PID=$!
for _ in $(seq 1 60); do
  port_busy "$FRONTEND_PORT" && break
  kill -0 "$FRONT_PID" 2>/dev/null || { tail -n 30 "$DEMO_DIR/frontend.log" >&2; die "frontend exited"; }
  sleep 1
done

cat <<EOF

  ┌ rag demo — synthetic data, fictional company "Stavby Kráľ"
  │ App:      http://localhost:$FRONTEND_PORT
  │ Admin:    $ADMIN_EMAIL / $ADMIN_PASSWORD
  │ Workers:  Tomáš Baláž, PIN 2222 · Marek Hudák, PIN 1111
  │           (same URL, "Pracovník" tab on the login page — the worker's phone app)
  │ Logs:     demo-data/backend.log, demo-data/frontend.log
  └ Ctrl-C stops both · \`make demo-reset\` wipes demo-data/

EOF
wait
