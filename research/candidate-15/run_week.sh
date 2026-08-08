#!/usr/bin/env bash
set -euo pipefail

CAND="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$CAND/../.." && pwd)"
INTERVAL="${1:?usage: run_week.sh INTERVAL_ID [OUTPUT_DIR]}"
OUT="${2:-$CAND/results/$INTERVAL}"

export PYTHONPATH="$CAND:$CAND/../candidate-14:$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

smc4 doctor
python -m py_compile \
  "$CAND/sequential_response_router.py" \
  "$CAND/candidate15_logic.py" \
  "$CAND/managed_transfer_initiative.py" \
  "$CAND/candidate15_v8_managed_transfer_materializer.py" \
  "$CAND/run_leadership_scdam.py" \
  "$CAND/candidate15_runner.py"
python -m unittest discover -s "$CAND" -p 'test_*.py' -v

rm -rf "$OUT"
mkdir -p "$OUT"
python "$CAND/candidate15_runner.py" "$INTERVAL" "$OUT"

for file in \
  run.json data_manifest.json metrics.json summary.json effective_config.json \
  scenario_events.jsonl scenario_events.raw.jsonl submitted_plans.json \
  order_lifecycle.json orders.csv positions.csv account.csv; do
  test -s "$OUT/$file"
done
