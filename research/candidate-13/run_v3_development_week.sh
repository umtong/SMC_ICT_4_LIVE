#!/usr/bin/env bash
set -euo pipefail

CAND="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$CAND/../.." && pwd)"
WEEK="${1:?usage: run_v3_development_week.sh W10}"
OUT="${2:-$CAND/v3/development/results/$WEEK}"

case "$WEEK" in
  W10|W11|W12|W13|W14|W15|W16|W17|W18|W19) ;;
  *) echo "week must be W10 through W19" >&2; exit 64 ;;
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
  --protocol "$CAND/protocol-v3-development.json"

for file in \
  run.json data_manifest.json metrics.json summary.json audit.json audit.md \
  source_lock.json effective_config.json scenario_events.jsonl \
  submitted_plans.json order_lifecycle.json orders.csv positions.csv account.csv; do
  test -s "$OUT/$file"
done
