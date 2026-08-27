#!/usr/bin/env bash
# Wipe to a clean demo state: reseed with the fixed RNG seed, then rebuild every
# layer 3 signal and the case bus from scratch. Same fixed seed every time, so this
# is the thing to run between rehearsals — never leaves stale cases from a previous
# run's approvals/investigations lying around.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

echo "=== 1/5 seeding synthetic data ==="
python -m scripts.seed

echo
echo "=== 2/5 consistency matrix -> signals ==="
python -m backend.ledgers.scanner

echo
echo "=== 3/5 anomaly detection -> signals ==="
python -m backend.anomaly.detector

echo
echo "=== 4/5 training ML scorers ==="
python -m backend.ml.train

echo
echo "=== 5/5 signals -> cases ==="
python -m backend.cases.bus

echo
echo "Demo state is clean. Start the backend and frontend with ./scripts/run.sh"
