#!/usr/bin/env bash
set -euo pipefail

CAND="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$CAND/../.." && pwd)"
WEEK="${1:?usage: run_v16_source_transfer_week.sh W13|W20}"
OUT="${2:-$CAND/v16/source_transfer/development/results/$WEEK}"

case "$WEEK" in
  W13|W20) ;;
  *) echo "diagnostic week must be W13 or W20" >&2; exit 64 ;;
esac

export PYTHONPATH="$CAND:$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
smc4 doctor
python -m py_compile "$CAND"/*.py
rm -rf "$OUT"
mkdir -p "$OUT"
python "$CAND/candidate13_runner_v16.py" \
  "$WEEK" "$OUT" \
  --protocol "$CAND/protocol-v16-source-transfer-development.json"

for file in run.json data_manifest.json metrics.json summary.json audit.json audit.md source_lock.json effective_config.json scenario_events.jsonl submitted_plans.json order_lifecycle.json orders.csv positions.csv account.csv; do
  test -s "$OUT/$file"
done
