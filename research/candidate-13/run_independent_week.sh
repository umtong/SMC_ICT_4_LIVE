#!/usr/bin/env bash
set -euo pipefail

CAND="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$CAND/../.." && pwd)"
WEEK="${1:?usage: run_independent_week.sh W15}"
OUT="${2:-$CAND/independent/results/$WEEK}"

case "$WEEK" in
  W15|W16|W17|W18|W19) ;;
  *) echo "week must be W15 through W19" >&2; exit 64 ;;
esac

export PYTHONPATH="$CAND:$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

smc4 doctor
python -m py_compile \
  "$CAND/bar_adapter.py" \
  "$CAND/global_allocator.py" \
  "$CAND/logic.py" \
  "$CAND/market_leadership.py" \
  "$CAND/session_engine.py" \
  "$CAND/run_leadership_scdam.py" \
  "$CAND/runner_materializer.py" \
  "$CAND/semantic_execution.py" \
  "$CAND/semantic_logic.py" \
  "$CAND/semantic_market_leadership.py" \
  "$CAND/semantic_post_gate.py" \
  "$CAND/evidence_audit.py" \
  "$CAND/candidate13_runner.py" \
  "$CAND/aggregate.py"

rm -rf "$OUT"
mkdir -p "$OUT"
python "$CAND/candidate13_runner.py" \
  "$WEEK" "$OUT" \
  --protocol "$CAND/protocol-independent.json"

for file in \
  run.json data_manifest.json metrics.json summary.json audit.json audit.md \
  source_lock.json effective_config.json scenario_events.jsonl \
  submitted_plans.json order_lifecycle.json orders.csv positions.csv account.csv; do
  test -s "$OUT/$file"
done
