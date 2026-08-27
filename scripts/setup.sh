#!/usr/bin/env bash
# One-time setup: install deps, seed data, train ML scorers.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

echo "=== Python dependencies ==="
pip install -r requirements.txt

echo
echo "=== Frontend dependencies ==="
(cd frontend && npm install)

echo
echo "=== Seed data + train scorers ==="
./scripts/demo_reset.sh

echo
if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env from .env.example — add your GEMINI_API_KEY before running the backend."
else
    echo ".env already exists, left untouched."
fi

echo
echo "Setup complete. Run ./scripts/run.sh to start the backend (:8000) and frontend (:5173)."
