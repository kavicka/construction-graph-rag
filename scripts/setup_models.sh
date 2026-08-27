#!/usr/bin/env bash
set -euo pipefail

command -v ollama >/dev/null 2>&1 || { echo "ollama chýba: brew install ollama"; exit 1; }

if ! curl -sf http://localhost:11434/api/version >/dev/null 2>&1; then
  echo "→ štartujem ollama serve"
  nohup ollama serve >/tmp/ollama.log 2>&1 &
  sleep 2
fi

for m in qwen3:4b-instruct qwen3:1.7b; do
  echo "→ pull $m"
  ollama pull "$m"
done
ollama list
