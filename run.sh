#!/usr/bin/env bash
# Start the Expense Manager server (creates the virtualenv on first run).
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  PY=$(command -v python3.14 || command -v python3.13 || command -v python3.12 || command -v python3)
  "$PY" -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi
[ -f .env ] || cp .env.example .env

# An EM_PORT set in the environment wins over .env (e.g. EM_PORT=8799 ./run.sh for a demo instance).
PORT="${EM_PORT:-$(grep -E '^EM_PORT=' .env | cut -d= -f2 || true)}"
exec .venv/bin/python -m uvicorn expense_manager.api:app --host 0.0.0.0 --port "${PORT:-8642}" --no-proxy-headers --log-level info
