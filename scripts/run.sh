#!/usr/bin/env bash
# Backend on :8000, frontend on :5173. Ctrl+C stops both.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [ ! -f backend/db/paygent.db ]; then
    echo "No database found — running demo_reset.sh first."
    ./scripts/demo_reset.sh
fi

cleanup() {
    echo
    echo "Stopping..."
    kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "Starting backend on :8000..."
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!

echo "Starting frontend on :5173..."
(cd frontend && npm run dev) &
FRONTEND_PID=$!

wait "$BACKEND_PID" "$FRONTEND_PID"
