#!/usr/bin/env bash
set -euo pipefail

CAND="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$CAND/../.." && pwd)"
WEEK="${1:?usage: run_v4_regression_week.sh W10}"
OUT="${2:-$CAND/v4/regression/results/$WEEK}"

case "$WEEK" in
  W10|W11|W12|W13|W14|W15|W16|W17|W18|W19|W20|W21|W22|W23|W24|W25|W26|W27|W28|W29) ;;
  *) echo "week must be W10 through W29" >&2; exit 64 ;;
esac

export PYTHONPATH="$CAND:$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
smc4 doctor
python -m py_compile "$CAND"/*.py
rm -rf "$OUT"
mkdir -p "$OUT"
python "$CAND/candidate13_runner_v4.py" \
  "$WEEK" "$OUT" \
  --protocol "$CAND/protocol-v4-regression.json"

for file in run.json data_manifest.json metrics.json summary.json audit.json audit.md source_lock.json effective_config.json scenario_events.jsonl submitted_plans.json order_lifecycle.json orders.csv positions.csv account.csv; do
  test -s "$OUT/$file"
done
